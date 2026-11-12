"""Nexus Operative development desktop API. Simulation only: no brokerage or money movement."""
import math
import threading
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
from pathlib import Path
import sys
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from nexus.state import load_settings, save_settings, audit
from nexus.markets import list_markets
from nexus.market_universe import page as market_universe_page
from nexus.expansion_initiative import roadmap as expansion_roadmap, assess_evidence as assess_expansion_evidence
from nexus.durable_orders import gateway as research_order_gateway
from nexus.paper_forward import runner as paper_forward_runner
from nexus.hub_refresh import refresh_worker
from nexus.hub_jobs import BUILD_LOCK
from nexus.tier_permissions import current as tier_permissions_current, configure as configure_tier_permission, require_entry as require_tier_entry
from nexus.tier_performance import tier_performance
from nexus.decision_lane import benchmark as offline_decision_benchmark
from nexus.paper_execution import PaperEngine
from nexus.replay import replay
from nexus.journal import record_replay, recent_replays
from nexus.walkforward import walk_forward
from nexus.risk_gate import evaluate as risk_evaluate
from nexus.stress import stress_screen
from nexus.public_data import read_ticker, PRODUCTS
from nexus.feed_guard import feed_guard
from nexus.cross_venue import crosscheck
from nexus.market_evidence import capture as capture_market_evidence, recent as recent_market_evidence
from nexus.data_quality import quality as market_data_quality
from nexus.tick_history import capture_tick, dataset as chronological_tick_dataset
from nexus.historical_candles import collect as collect_history_candles, history as read_history_candles
from nexus.candle_research import review as review_candle_research, evaluate as evaluate_candle_research
from nexus.cost_sensitivity import sensitivity as evaluate_cost_sensitivity
from nexus.execution_realism import assess as execution_realism_assess
from nexus.regime_stress import analyze as analyze_regime_stress
from nexus.strategy_branches import available_branches
from nexus.observer import observer, history as observation_history
from nexus.tournament import tournament
from nexus.council import council
from nexus.evidence_auditor import audit_evidence
from nexus.latency import latency_observatory
from nexus.model_adapters import coordinate as coordinate_model_advisors
from nexus.advisory_benchmark import benchmark as advisory_contract_benchmark
from nexus.provider_research import status as provider_research_status
from nexus.onboarding import inventory as local_capability_inventory
from nexus import credential_vault, emergency_intent
from nexus.found_markets import worker as found_market_worker
from nexus.ai_evidence_preview import build as ai_evidence_preview, authorize_preview as ai_authorize_preview
from nexus.safety_benchmark import benchmark as safety_benchmark
from nexus.portfolio_governor import audit_portfolio
from nexus.recovery import recovery_report, backup_snapshot
from nexus.paper_portfolio import initialize as init_paper_portfolio, state as paper_portfolio_state, buy as paper_buy, sell as paper_sell, reconcile as reconcile_paper

@asynccontextmanager
async def application_lifespan(application):
    try:yield
    finally:
        # Shutdown has its own worker. The default asyncio executor may be
        # occupied by a slow feed/storage call, but that must not delay the
        # emergency lane drain or let later cleanup overtake an in-flight cut.
        lifecycle_executor=None
        try:
            try:
                lifecycle_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nexus-lifecycle-stop')
                future=lifecycle_executor.submit(_shutdown_application_workers)
            except Exception:
                if lifecycle_executor is not None:
                    lifecycle_executor.shutdown(wait=False,cancel_futures=True)
                    lifecycle_executor=None
                # Worker creation is not allowed to skip the stop fence.
                _shutdown_application_workers()
            else:
                wrapped=asyncio.wrap_future(future)
                try:await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    # Cancellation is not allowed to abandon the stop fence or
                    # leave its exception on an unobserved asyncio wrapper.
                    # Finish the private worker synchronously, then cancel the
                    # wrapper before preserving cancellation or surfacing the
                    # worker failure from Future.result().
                    lifecycle_executor.shutdown(wait=True,cancel_futures=False)
                    lifecycle_executor=None
                    try:future.result()
                    finally:wrapped.cancel()
                    raise
        finally:
            # If the lifespan task is cancelled, still wait for the private
            # worker: its nested finally chain is the application stop fence.
            if lifecycle_executor is not None:
                lifecycle_executor.shutdown(wait=True,cancel_futures=False)

app = FastAPI(title="Nexus Operative", version="0.340.275",lifespan=application_lifespan)
from nexus.local_http_boundary import LocalHTTPBoundary
app.add_middleware(LocalHTTPBoundary)
_started = time.monotonic()
_paused = True
# Lifecycle admission is serialized; a throttle cut publishes its latch before waiting.
_engine_control_lock = threading.Lock()
_trading_control_lock = threading.RLock()
_cut_lock = threading.Lock()
_cut_epoch = 0
_cut_pending = 0
_cut_executor = None
_cut_future = None
_cut_lane_closing = False
def _recover_emergency_cut():
    try:
        order_path = research_order_gateway._path()
        pending_intent = emergency_intent.requested(order_path)
        # A first launch must not create accounting databases just to inspect a
        # stop state. stat distinguishes missing history from inaccessible state.
        if order_path.is_symlink(): raise ValueError('ORDER_STARTUP_SYMLINK')
        try: order_path.stat()
        except FileNotFoundError: durable_halt = False
        else: durable_halt = research_order_gateway.recovery_report()["halted"]
        halted, confirmed = pending_intent or durable_halt, durable_halt
    except (ValueError,OSError,sqlite3.Error):
        halted, confirmed = True, False
    if halted:paper_forward_runner.request_cut()
    return halted, confirmed

_halted, _cut_confirmed = _recover_emergency_cut()

def _execution_halted():
    """Adopt a process-external latch without touching SQLite or starting work."""
    global _halted,_cut_confirmed
    if _halted:return True
    try:external=emergency_intent.requested(research_order_gateway._path())
    except (ValueError,OSError):external=True
    if external:
        paper_forward_runner.request_cut()
        with _cut_lock:
            _halted=True
            _cut_confirmed=False  # This process did not witness durable cleanup.
    return external

_tiers = {
    "terrestrial": {"risk":"low", "max_weight":0.01, "score":28, "approach":"Prioritize liquid assets, modest position exposure and defined exits."},
    "solar": {"risk":"moderate", "max_weight":0.03, "score":38, "approach":"Research trend signals, confirmation and volatility before considering entries."},
    "universal": {"risk":"high", "max_weight":0.05, "score":19, "approach":"Stress-test correlated drawdowns and tail exposure before risking capital."},
}
class PortalCommand(BaseModel):
    enabled: bool
    product: str = Field(default="BTC-USD",pattern="^(BTC-USD|ETH-USD|SOL-USD)$")
    granularity: int = Field(default=3600)
    limit: int = Field(default=1000,ge=240,le=1000)
    collect_public_history: bool = True
    discover_markets: bool = False

class PaperSettings(BaseModel):
    paper_budget: float = Field(gt=0, le=1000000)
    terrestrial_allocation: int = Field(ge=0, le=100)
    solar_allocation: int = Field(ge=0, le=100)
    universal_allocation: int = Field(ge=0, le=100)

@app.get("/api/paper/settings")
def paper_settings():
    try:return {"simulation_only": True, **load_settings()}
    except ValueError as exc:
        detail=str(exc) if str(exc)=='PAPER_SETTINGS_DATABASE_BUSY' else 'PAPER_SETTINGS_REVIEW_REQUIRED'
        raise HTTPException(status_code=503,detail=detail)
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="PAPER_SETTINGS_REVIEW_REQUIRED")

@app.put("/api/paper/settings")
def update_paper_settings(value: PaperSettings):
    try:
        settings = save_settings(value.model_dump())
    except ValueError as e:
        if str(e)=='PAPER_SETTINGS_DATABASE_BUSY':raise HTTPException(status_code=503,detail=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="PAPER_SETTINGS_REVIEW_REQUIRED")
    return {"simulation_only": True, **settings}

class PaperScenario(BaseModel):
    capital: float = Field(gt=0, le=1000000)
    tier: str = Field(pattern="^(terrestrial|solar|universal)$")
    symbol: str = Field(pattern="^(SPY|QQQ|IWM|BTC/USD|ETH/USD)$")
    direction: str = Field(pattern="^(UP|DOWN)$")
    notional: float = Field(gt=0, le=1000000)
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)

@app.post("/api/paper/scenario")
def paper_scenario(value: PaperScenario):
    # Deterministic, one-roundtrip execution simulation; no external market or broker calls.
    engine = PaperEngine(capital=value.capital)
    now = time.time()
    try:
        enter = engine.open(value.tier,value.symbol,value.direction,value.notional,value.entry_price,now,{})
        out = engine.close(value.symbol,value.exit_price,now,{})
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))
    return {"mode":"PAPER_SCENARIO_ONLY","synthetic":True,"open":enter,"close":out,
            "ending_paper_cash":float(engine.cash),
            "starting_paper_cash":float(engine.initial),
            "net_pnl":float(engine.cash-engine.initial),
            "disclaimer":"This is a hypothetical round-trip, not a trade or estimated win probability."}

class HistoricalReplay(BaseModel):
    capital: float = Field(gt=0, le=1000000)
    closes: list[float] = Field(min_length=5, max_length=10000)
    tier: str = Field(pattern="^(terrestrial|solar|universal)$")
    direction: str = Field(pattern="^(UP|DOWN)$")

@app.post("/api/paper/replay")
def historical_replay(value: HistoricalReplay):
    try:
        return replay(value.closes,value.capital,value.tier,value.direction)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/paper/safety-benchmark")
def run_safety_benchmark():
    return safety_benchmark()

@app.get("/api/onboarding/tasks")
def onboarding_tasks():
    from nexus.startup_tasks import checklist
    return checklist(portal_active=not _paused)

@app.get("/api/onboarding/readiness")
def onboarding_readiness():
    return local_capability_inventory()

def _require_local_portal(request:Request):
    from urllib.parse import urlsplit
    # Prevent cross-site form CSRF and off-machine browser requests.
    origin=request.headers.get("origin","")
    target=request.url
    expected=f"{target.scheme}://{target.netloc}"
    if origin!=expected or target.hostname not in ("localhost","127.0.0.1") or request.client is None or request.client.host not in ("127.0.0.1","::1"):
        raise HTTPException(status_code=403,detail="Use the local Nexus PORTAL to change connector settings")

from nexus.paper_onboarding import register as register_paper_onboarding
register_paper_onboarding(app, _require_local_portal)

from nexus.ai_onboarding import register as register_ai_onboarding
register_ai_onboarding(app, _require_local_portal)

class PublicAiConsent(BaseModel):
    product: str = Field(pattern="^(BTC-USD|ETH-USD|SOL-USD)$")
    preview_sha256: str = Field(pattern="^[a-f0-9]{64}$")

@app.get("/apim«ëŒ+Š×®º+º$zzb¥â÷W"ö’×&÷f–FW"÷V&Æ–2×&Wf–Wr÷·&öGV7GÒ"¦FVbV&Æ–5ö•÷&W6V&6…÷&Wf–Wr‡&öGV7C¢7G"“ ¢G'“§&WGW&â•öWf–FVæ6U÷&Wf–Wr‡&öGV7B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"ö’×&÷f–FW"÷V&Æ–2×&Wf–Wr÷6VæB"¦FVbV&Æ–5ö•÷&W6V&6…÷6VæB†6öç6VçC¥V&Æ–4”6öç6VçBÇ&WVW7C¥&WVW7B“ ¢÷&WV—&UöÆö6Å÷÷'FÂ‡&WVW7B¢G'“§&WGW&â•öWF†÷&—¦U÷&Wf–Wr†6öç6VçBç&öGV7BÆ6öç6VçBç&Wf–Wu÷6†#Sb¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’÷W"ö’×&÷f–FW"öGf—6÷'’Ö&Væ6†Ö&²"¦FVb•öGf—6÷'•ö&Væ6†Ö&²‚“ ¢&WGW&âGf—6÷'•ö6öçG&7Eö&Væ6†Ö&²‚ ¤ævWB‚"ö’÷W"ö’×&÷f–FW"÷7FGW2"¦FVb•÷&÷f–FW%÷7FGW2‚“ ¢&WGW&â¥4ôå&W7öç6R‡&÷f–FW%÷&W6V&6…÷7FGW2‚’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â%&vÖ#¢&æòÖ66†R'Ò ¤ç÷7B‚"ö’÷W"ö’×&÷f–FW"÷·&öÆWÒ÷&W6V&6‚"¦FVb•÷&÷f–FW%÷&W6V&6‚‡&öÆS¢7G"Â6æ6†÷C¢F–7B“ ¢2ÆVv7’&&—G&'’×–ÆöB&÷WFR—2f–ÂÖ6Æ÷6VC²W6R&Wf–WvVB6öç6VçBà¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC2ÂFWF–ÃÒ%W6R&Wf–WvVBV&Æ–2×&Wf–Wr6öç6VçBv÷&¶fÆ÷r" ¤ç÷7B‚"ö’÷W"öÖöFVÂÖ6÷Væ6–Â"¦FVbÖöFVÅö6÷Væ6–Å÷&Wf–Wr‡6æ6†÷C¢F–7B“ ¢G'“ ¢&WGW&â6ö÷&F–æFUöÖöFVÅöGf—6÷'2‡6æ6†÷B¢W†6WB…fÇVTW'&÷"ÅG—TW'&÷"Ä÷fW&fÆ÷tW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ$–çfÆ–B÷"÷fW'6—¦VBGf—6÷'’6æ6†÷B" ¤ævWB‚"ö’öÆFVæ7’"¦FVbÆFVæ7•÷7FGW2‚“ ¢&WGW&âÆFVæ7•öö'6W'fF÷'’ç7VÖÖ'’‚ ¤ç÷7B‚"ö’÷W"öWf–FVæ6RÖVF—B"¦FVbWf–FVæ6UöVF—B†Wf–FVæ6S¦F–7B“ ¢G'“ ¢&WGW&âÆFVæ7•öö'6W'fF÷'’æÖV7W&R‚&Wf–FVæ6UöVF—B"ÆÆÖ&F¦VF—EöWf–FVæ6R†Wf–FVæ6R’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"ö6÷Væ6–Â"¦FVbW%ö6÷Væ6–Â†Wf–FVæ6S¢F–7B“ ¢G'“ ¢2VF—B—2–æFWVæFVçC¢æò76W'FVBwfW&–f–VBrfÆr6â'—72—Bà¢VF—CÖÆFVæ7•öö'6W'fF÷'’æÖV7W&R‚vWf–FVæ6UöVF—BrÆÆÖ&F¦VF—EöWf–FVæ6R†Wf–FVæ6R’¢&W7VÇCÖÆFVæ7•öö'6W'fF÷'’æÖV7W&R‚v6÷Væ6–ÅöFV6—6–öârÆÆÖ&F¦6÷Væ6–Â†Wf–FVæ6R’¢&W7VÇE²vWf–FVæ6UöVF—BuÓÖVF—@¢&W7VÇE²v6öç6Vç7W2uÓÒu$T¤T5Br–bVF—E²vFV6—6–öâuÒÒudU$”d”TBrVÇ6R&W7VÇE²v6öç6Vç7W2uĞ¢&WGW&â&W7VÇ@¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷F÷W&æÖVçB"¦FVbW%÷F÷W&æÖVçB‡fÇVS¢†—7F÷&–6Å&WÆ’“ ¢G'“ ¢&WGW&âF÷W&æÖVçB‡fÇVRæ6Æ÷6W2ÇfÇVRæ6—FÂ¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷vÆ²Öf÷'v&B"¦FVbW%÷vÆµöf÷'v&B‡fÇVS¢†—7F÷&–6Å&WÆ’“ ¢G'“ ¢&WGW&âvÆµöf÷'v&B‡fÇVRæ6Æ÷6W2ÇfÇVRæ6—FÂÇfÇVRçF–W"ÇfÇVRæF—&V7F–öâ¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷&WÆ’÷&V6÷&B"¦FVb&V6÷&Eö†—7F÷&–6Å÷&WÆ’‡fÇVS¢†—7F÷&–6Å&WÆ’“ ¢G'“ ¢&WVW7C×fÇVRæÖöFVÅöGV×‚¢&W7VÇC×&WÆ’‡fÇVRæ6Æ÷6W2ÇfÇVRæ6—FÂÇfÇVRçF–W"ÇfÇVRæF—&V7F–öâ¢&WGW&â&V6÷&E÷&WÆ’‡&WVW7BÇ&W7VÇB¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒuU%õ$UÄ•ô¤õU$äÅôDD$4Uô%U5’s ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–Ã×7G"†W†2’¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’÷W"÷&WÆ’ö†—7F÷'’"¦FVb&WÆ•ö†—7F÷'’†Æ–Ö—C¢–çBÒ#“ ¢G'“ ¢&WGW&â²&ÖöFR#¢$Äô4ÅõU%ô¤õU$äÂ"Â'6–×VÆF–öåööæÇ’#¥G'VRÂ''Vç2#§&V6VçE÷&WÆ—2†Æ–Ö—B—Ğ¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒuU%õ$UÄ•ô¤õU$äÅôDD$4Uô%U5’s ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–Ã×7G"†W†2’¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷&—6²×67&VVâ"¦FVb&—6µ÷67&VVâ‡fÇVS¢F–7B“ ¢2&V¦V7FVBöÖ—76–ær6Æ–Ò—2æWfW"66WFVB2Wf–FVæ6RöbÖ&¶WB&öf—F&–Æ—G’à¢&WGW&â&—6µöWfÇVFR‡fÇVR ¦6Æ727G&W75&WVW7B„&6TÖöFVÂ“ ¢WV—G“¢fÆöBÒf–VÆB†wCÓÂÆSÓ¢æ÷F–öæÃ¢fÆöBÒf–VÆB†wCÓÂÆSÓ¢F—&V7F–öã¢7G"Òf–VÆB‡GFW&ãÒ%â…UÄDõtâ’B"¢F–Ç•öÆ÷73¢fÆöBÒf–VÆB†vSÓÂÆSÓ ¤ç÷7B‚"ö’÷W"÷7G&W72"¦FVbW%÷7G&W72‡fÇVS¢7G&W75&WVW7B“ ¢G'“ ¢&WGW&â7G&W75÷67&VVâ‡fÇVRæWV—G’ÇfÇVRææ÷F–öæÂÇfÇVRæF—&V7F–öâÇfÇVRæF–Ç•öÆ÷72¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¦6Æ72FW÷6—E&Wf–Wr„&6TÖöFVÂ“ ¢Ö÷VçC¢fÆöBÒf–VÆB†wCÓÂÆSÓ¢6FVæ6S¢7G"Òf–VÆB‡GFW&ãÒ%â†öæ6WÇvVV¶Ç—ÆÖöçF†Ç’’B" ¦FVb6–×VÆFVE÷6æ6†÷B‚“ ¢VÆ6VBÒ–çB‚‡F–ÖRæÖöæ÷Föæ–2‚’Õ÷7F'FVB’òóR¢&WGW&â°¢²'F–W"#¦æÖRÂ'&—6²#¦FF²'&—6²%ÒÂ&–ÆÇW7G&F—fU÷6–væÅ÷66÷&R#¦Ö‚ƒRÆÖ–âƒƒRÆFF²'66÷&R%Ò·&÷VæB†ÖF‚ç6–â‚†VÆ6VB¶’£2’£ã3R’£R’’’À¢'66÷&Uö—5÷v–å÷&ö&&–Æ—G’#¤fÇ6RÂ&Ö†–×VÕ÷÷6—F–öåög&7F–öâ#¦FF²&Ö…÷vV–v‡B%ÒÀ¢&W†×ÆU÷G&FR#¢%7–çF†WF–2UDbvF6†Æ—7C¢W"&W6V&6‚öæÇ’"À¢&7W'&VçEö÷WF6öÖR#¢$æòÆ—fR÷6—F–öâ"Â'7G&FVw•ö&ö6‚#¦FF²&&ö6‚%×Ğ¢f÷"’Â†æÖRÆFF’–âVçVÖW&FR…÷F–W'2æ—FV×2‚’¢Ğ ¤ævWB‚"ö’÷&öFÖ"¦FVb&VÆV6U÷&öFÖ‚“ ¢g&öÒæW‡W2ç&VÆV6U÷&öFÖ–×÷'B6æ6†÷@¢&WGW&â6æ6†÷B‚ ¦FVb7FGW2‚“ ¢öW†V7WF–öåö†ÇFVB‚¢v—F‚ö7WEöÆö6³ ¢†ÇFVCÕö†ÇFVC·VæF–æsÖ&ööÂ…ö7WE÷VæF–ær¢7WE÷7FFSÒt4ÄT"r–bæ÷B†ÇFVBVÇ6RuTäD”ärr–bVæF–ærVÇ6RudU$”d”TBr–bö7WEö6öæf—&ÖVBVÇ6RuTä4ôäd•$ÔTBp¢&WGW&â²'fW'6–öâ#¢#ã3Cã#sR"Â&ÖöFR#¢%5”åD„UD”5õU%ôôäÅ’"Â'÷'FÅöVæ&ÆVB#¦æ÷B÷W6VBÀ¢&†ÇFVB#¦†ÇFVBÂ&VÖW&vVæ7•ö7WE÷VæF–ær#§VæF–ærÂ&VÖW&vVæ7•ö7WE÷7FFR#¦7WE÷7FFRÀ¢&'&ö¶W%ö6öææV7FVB#¤fÇ6RÂ&&æµö6öææV7FVB#¤fÇ6RÀ¢&Æ—fUö÷&FW'5öVæ&ÆVB#¤fÇ6RÂ&ÖöæW•öÖ÷fVÖVçEöVæ&ÆVB#¤fÇ6RÂ&•ö6öææV7FVB#¤fÇ6WĞ ¤ævWB‚"ö’÷7FGW2"¦7–æ2FVb'VçF–ÖU÷7FGW2‚“ ¢2&÷VæFVBÆF6‚Ç7FBF÷G27FæFÆöæR7WG3²æò5Æ—FRöfVVBövVæW&Âv÷&¶W"à¢&WGW&â¥4ôå&W7öç6R‡7FGW2‚’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò ¤ç÷7B‚"ö’ö6æFÆR×&W6V&6‚÷·&öGV7GÒ÷&Vv–ÖR×7G&W72"¦FVb6æFÆU÷&Vv–ÖU÷7G&W72‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3cÂÆ–Ö—C¢–çBÒ3“ ¢G'“§&WGW&âæÇ—¦U÷&Vv–ÖU÷7G&W72‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’ö6æFÆR×&W6V&6‚÷·&öGV7GÒöW†V7WF–öâ×7G&W72"¦FVb6æFÆUöW†V7WF–öå÷7G&W72‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3cÂÆ–Ö—C¢–çBÒ3“ ¢G'“§&WGW&âW†V7WF–öå÷&VÆ—6Õö76W72‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’ö6æFÆR×&W6V&6‚÷·&öGV7GÒö6÷7B×6Vç6—F—f—G’"¦FVb6æFÆUö6÷7E÷6Vç6—F—f—G’‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3cÂÆ–Ö—C¢–çBÒ3“ ¢G'“§&WGW&âWfÇVFUö6÷7E÷6Vç6—F—f—G’‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’ö6æFÆR×&W6V&6‚÷·&öGV7GÒöFÖ—76–öâ"¦FVb6æFÆU÷&W6V&6…öFÖ—76–öâ‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3cÂÆ–Ö—C¢–çBÒ3“ ¢G'“ ¢&W÷'C×&Wf–Wuö6æFÆU÷&W6V&6‚‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢&WGW&â¶³§bf÷"²Çb–â&W÷'Bæ—FV×2‚’–b²Ò&6Æ÷6W2'Ğ¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’ö6æFÆR×&W6V&6‚÷·&öGV7GÒöWfÇVFR"¦FVb6æFÆU÷&W6V&6…öWfÇVFR‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3cÂÆ–Ö—C¢–çBÒ3“ ¢G'“§&WGW&âWfÇVFUö6æFÆU÷&W6V&6‚‡&öGV7BÆw&çVÆ&—G“Öw&çVÆ&—G’ÆÆ–Ö—CÖÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’ö†—7F÷&–6ÂÖ6æFÆW2÷·&öGV7GÒö6öÆÆV7B"¦FVb†—7F÷&–6Åö6æFÆW5ö6öÆÆV7B‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3“ ¢–b&öGV7Bæ÷B–â$ôET5E3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ%Vç7W÷'FVB&öGV7B"¢G'“§&WGW&â6öÆÆV7Eö†—7F÷'•ö6æFÆW2‡&öGV7BÆw&çVÆ&—G’¢W†6WBfÇVTW'&÷#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$†—7F÷&–6Â6æFÆR6÷W&6RVæf–Æ&ÆR÷"–çfÆ–B" ¤ævWB‚"ö’ö†—7F÷&–6ÂÖ6æFÆW2÷·&öGV7GÒ"¦FVb†—7F÷&–6Åö6æFÆW5ö†—7F÷'’‡&öGV7C¢7G"Âw&çVÆ&—G“¢–çBÒ3ÂÆ–Ö—C¢–çBÒ3“ ¢G'“§&WGW&â&VEö†—7F÷'•ö6æFÆW2‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷&W6V&6‚×F–6·2÷·&öGV7GÒö6GW&R"¦FVb6GW&U÷&W6V&6…÷F–6²‡&öGV7C¢7G"“ ¢–b&öGV7Bæ÷B–â$ôET5E3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ%Vç7W÷'FVB&öGV7B"¢G'“§&WGW&â6GW&U÷F–6²‡&öGV7B¢W†6WB…fÇVTW'&÷"ÅG—TW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%&W6V&6‚V÷FRVæf–Æ&ÆR÷"–çfÆ–B" ¤ævWB‚"ö’÷&W6V&6‚×F–6·2÷·&öGV7GÒ"¦FVbvWE÷&W6V&6…÷F–6·2‡&öGV7C¢7G"ÂÆ–Ö—C¢–çBÒ#SÂv÷6V6öæG3¢fÆöBÒc“ ¢G'“§&WGW&â6‡&öæöÆöv–6Å÷F–6µöFF6WB‡&öGV7BÆÆ–Ö—BÆv÷6V6öæG2¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’öÖ&¶WBÖWf–FVæ6R÷·&öGV7GÒ÷VÆ—G’"¦FVbÖ&¶WEöWf–FVæ6U÷VÆ—G’‡&öGV7C¢7G"ÂÆ–Ö—C¢–çBÒÂv÷6V6öæG3¢fÆöBÒc“ ¢G'“§&WGW&âÖ&¶WEöFF÷VÆ—G’‡&öGV7BÆÆ–Ö—BÆv÷6V6öæG2¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’öÖ&¶WBÖWf–FVæ6R"¦FVbÖ&¶WEöWf–FVæ6Uö†—7F÷'’‡&öGV7C¢7G"ÂæöæRÒæöæRÂÆ–Ö—C¢–çBÒ#R“ ¢G'“§&WGW&â&V6VçEöÖ&¶WEöWf–FVæ6R‡&öGV7BÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’öÖ&¶WBÖWf–FVæ6R÷·&öGV7GÒö6GW&R"¦FVbÖ&¶WEöWf–FVæ6Uö6GW&R‡&öGV7C¢7G"“ ¢–b&öGV7Bæ÷B–â$ôET5E3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ%Vç7W÷'FVB&öGV7B"¢G'“§&WGW&â6GW&UöÖ&¶WEöWf–FVæ6R‡&öGV7B¢W†6WBfÇVTW'&÷#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%&VBÖöæÇ’Ö&¶WBWf–FVæ6RVæf–Æ&ÆR" ¤ævWB‚"ö’÷V&Æ–2×V÷FR÷·&öGV7GÒö7&÷72×fVçVR"¦FVbV&Æ–5ö7&÷75÷fVçVR‡&öGV7C¢7G"“ ¢–b&öGV7Bæ÷B–â$ôET5E3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ$Ö&¶WB&öGV7Bæ÷BVæ&ÆVB"¢&WGW&â7&÷766†V6²‡&öGV7B ¤ævWB‚"ö’÷V&Æ–2×V÷FR÷·&öGV7GÒ÷VÆ—G’"¦FVbV÷FU÷VÆ—G’‡&öGV7C¢7G"“ ¢–b&öGV7Bæ÷B–â$ôET5E3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ%&öGV7Bæ÷BÆÆ÷vÆ—7FVB"¢G'“ ¢&WGW&âfVVEöwV&BæWfÇVFR‡&VE÷F–6¶W"‡&öGV7B’¢W†6WBfÇVTW'&÷# ¢&WGW&â²&6ö×öæVçB#¢%4”ätÄUõ4õU$4UôdTTEôuT$B"Â'&öGV7B#§&öGV7BÀ¢&FV6—6–öâ#¢%dUDò"Â'&V6öç2#¥²%$õd”DU%õTäd”Ä$ÄR%ÒÀ¢&–æFWVæFVçE÷&–6U÷fW&–f–6F–öâ#¤fÇ6RÂ&Æ—fUö÷&FW'5öVæ&ÆVB#¤fÇ6WĞ ¤ævWB‚"ö’÷V&Æ–2ÖFFöVff–6–Væ7’"¦FVbV&Æ–5öFFöVff–6–Væ7’‚“ ¢g&öÒæW‡W2çV&Æ–5öFF–×÷'BVff–6–Væ7¢&WGW&â¥4ôå&W7öç6R†Vff–6–Væ7’‚’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò ¤ævWB‚"ö’÷V&Æ–2×V÷FR÷·&öGV7GÒ"¦FVbV&Æ–5÷V÷FR‡&öGV7C¢7G"“ ¢–b&öGV7Bæ÷B–â$ôET5E3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ$Ö&¶WB&öGV7B—2æ÷BVæ&ÆVB"¢G'“ ¢&WGW&â&VE÷F–6¶W"‡&öGV7B¢W†6WBfÇVTW'&÷# ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%&VBÖöæÇ’V&Æ–2V÷FRVæf–Æ&ÆR÷"7FÆR" ¦6Æ72÷'FföÆ–ô–æ—B„&6TÖöFVÂ“ ¢W%ö6—FÃ¢fÆöBÒf–VÆB†wCÓÂÆSÓ ¦6Æ72W%G&FR„&6TÖöFVÂ“ ¢&öGV7C¢7G"Òf–VÆB‡GFW&ãÒ%â„%D2ÕU4GÄUD‚ÕU4GÅ4ôÂÕU4B’B"¢æ÷F–öæÃ¢fÆöBÒf–VÆB†wCÓÂÆSÓ¢F–W#¢7G"Òf–VÆB‡GFW&ãÒ%â‡FW'&W7G&–ÇÇ6öÆ'ÇVæ—fW'6Â’B" ¦6Æ72W$6Æ÷6R„&6TÖöFVÂ“ ¢&öGV7C¢7G"Òf–VÆB‡GFW&ãÒ%â„%D2ÕU4GÄUD‚ÕU4GÅ4ôÂÕU4B’B" ¤ævWB‚"ö’öö'6W'fW"÷7FGW2"¦FVbö'6W'fW%÷7FGW2‚“ ¢&WGW&âö'6W'fW"ç7FGW2‚ ¤ævWB‚"ö’öö'6W'fW"ö†—7F÷'’"¦FVbö'6W'fW%ö†—7F÷'’†Æ–Ö—C¦–çCÓ3“ ¢G'“§&WGW&â²&ÖöFR#¢%$TEôôäÅ•ôô%4U%dU""Â&WfVçG2#¦ö'6W'fF–öåö†—7F÷'’†Æ–Ö—B’Â&÷&FW'5öVæ&ÆVB#¤fÇ6WĞ¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’÷7G&FVw’Ö'&æ6†W2"¦FVb7G&FVw•ö'&æ6†W2‚“ ¢&WGW&âf–Æ&ÆUö'&æ6†W2‚ ¤ævWB‚"ö’÷W"÷÷'FföÆ–ò÷&V6÷fW'’×&W÷'B"¦FVb÷'FföÆ–õ÷&V6÷fW'•÷&W÷'B‚“ ¢&WGW&â&V6÷fW'•÷&W÷'B‚ ¤ç÷7B‚"ö’÷W"÷÷'FföÆ–ò÷6æ6†÷B"¦FVb÷'FföÆ–õöÆö6Å÷6æ6†÷B‚“ ¢G'“ ¢&WGW&â&6·W÷6æ6†÷B‚¢W†6WBfÇVTW'&÷"2W†3 ¢FWF–Ã×7G"†W†2¢–bæ÷BFWF–Âç7F'G7v—F‚‚%U%õ4ä4„õEò"“ ¢FWF–ÃÒ%U%õ4ä4„õEõTäd”Ä$ÄR ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÖFWF–Â¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%U%õ4ä4„õEõTäd”Ä$ÄR" ¤ævWB‚"ö’÷W"÷÷'FföÆ–òö–çFVw&—G’"¦FVbW%÷÷'FföÆ–õö–çFVw&—G’‚“ ¢&WGW&âVF—E÷÷'FföÆ–ò‚ ¤ævWB‚"ö’÷W"÷÷'FföÆ–ò"¦FVbW%÷÷'FföÆ–ò‚“ ¢G'“§&WGW&âW%÷÷'FföÆ–õ÷7FFR‚¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒuU%õõ%DdôÄ”õõTäd”Ä$ÄRr ¤ç÷7B‚"ö’÷W"÷÷'FföÆ–òö–æ—B"¦FVbW%÷÷'FföÆ–õö–æ—B†FF¥÷'FföÆ–ô–æ—B“ ¢G'“§&WGW&â–æ—E÷W%÷÷'FföÆ–ò†FFçW%ö6—FÂ¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷÷'FföÆ–òö'W’"¦FVbW%÷÷'FföÆ–õö'W’†FF¥W%G&FR“ ¢–böW†V7WF–öåö†ÇFVB‚“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô„ÅEô5D•dR"¢–b÷W6VC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ%õ%DÅõU4TB"¢v—F‚ö7WEöÆö6³¦Wö6ƒÕö7WEöWö6€¢G'“ ¢&WV—&U÷F–W%öVçG'’†FFçF–W"ÆFFææ÷F–öæÂ¢V÷FS×&VE÷F–6¶W"†FFç&öGV7B¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢–böW†V7WF–öåö†ÇFVB‚’÷"Wö6‚Õö7WEöWö6ƒ§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô5UEô”åDU%%UDTEõE$DR"¢–b÷W6VC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ%õ%DÅõU4TB"¢&WV—&U÷F–W%öVçG'’†FFçF–W"ÆFFææ÷F–öæÂ¢&WGW&âW%ö'W’†FFç&öGV7BÆFFææ÷F–öæÂÇV÷FR¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ç÷7B‚"ö’÷W"÷÷'FföÆ–ò÷6VÆÂ"¦FVbW%÷÷'FföÆ–õ÷6VÆÂ†FF¥W$6Æ÷6R“ ¢–böW†V7WF–öåö†ÇFVB‚“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô„ÅEô5D•dR"¢v—F‚ö7WEöÆö6³¦Wö6ƒÕö7WEöWö6€¢G'“ ¢V÷FS×&VE÷F–6¶W"†FFç&öGV7B¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢–böW†V7WF–öåö†ÇFVB‚’÷"Wö6‚Õö7WEöWö6ƒ§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô5UEô”åDU%%UDTEõE$DR"¢&WGW&âW%÷6VÆÂ†FFç&öGV7BÇV÷FR¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’÷W"÷÷'FföÆ–ò÷&V6öæ6–ÆR"¦FVbW%÷÷'FföÆ–õ÷&V6öæ6–ÆR‚“ ¢6æ6†÷C×W%÷÷'FföÆ–õ÷7FFR‚¢V÷FW3×·Ğ¢f÷"÷6—F–öâ–â6æ6†÷E²'÷6—F–öç2%Ó ¢G'“§V÷FW5·÷6—F–öå²'&öGV7B%ÕÓ×&VE÷F–6¶W"‡÷6—F–öå²'&öGV7B%Ò¢W†6WBfÇVTW'&÷# ¢&WGW&â²'&V6öæ6–ÆVB#¤fÇ6RÂ'&V6öâ#¢%V÷FRVæf–Æ&ÆR"Â'6–×VÆF–öåööæÇ’#¥G'VWĞ¢&WGW&â&V6öæ6–ÆU÷W"‡V÷FW2 ¤ævWB‚"ö’öW‡ç6–öâ÷&öFÖ"¦FVbW‡ç6–öåö–æ—F–F—fU÷&öFÖ‚“ ¢&WGW&âW‡ç6–öå÷&öFÖ‚ ¤ç÷7B‚"ö’öW‡ç6–öâ÷¶fVçVWÒ÷67&VVâ"¦FVbW‡ç6–öåö–æ—F–F—fU÷67&VVâ†fVçVS¢7G"ÂWf–FVæ6S¢F–7B“ ¢G'“§&WGW&â76W75öW‡ç6–öåöWf–FVæ6R†fVçVRÆWf–FVæ6R¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’öÖ&¶WG2öF—66÷fW""¦FVbF—66÷fW%÷V&Æ–5öÖ&¶WE÷Væ—fW'6R†Æ–Ö—C¢–çBÒÂöfg6WC¢–çBÒÂ6V&6ƒ¢7G"Ò""“ ¢G'“§&WGW&âÖ&¶WE÷Væ—fW'6U÷vR†Æ–Ö—CÖÆ–Ö—BÆöfg6WCÖöfg6WBÇ6V&6ƒ×6V&6‚¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2–b%Täd”Ä$ÄR"–â7G"†W†2’VÇ6RC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’öW†V7WF–öâöFV6—6–öâÖ&Væ6†Ö&²"¦FVbW†V7WF–öåöFV6—6–öåö&Væ6†Ö&²†6÷VçC¦–çCÓ3“ ¢G'“§&WGW&âöffÆ–æUöFV6—6–öåö&Væ6†Ö&²†6÷VçB¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¦6Æ72W$f÷'v&E7F'B„&6TÖöFVÂ“ ¢ÖöFVÅö6öæf–s×²vW‡G&s¢vf÷&&–BwĞ¢öÆ–7•ö–C¢7G"Òf–VÆB†FVfVÇCÒv'F5öÖöÖVçGVÕöW‡W&–ÖVçE÷crÆÖ–åöÆVæwFƒÓÆÖ…öÆVæwFƒÓcB¢F–W#¢7G"Òf–VÆB‡GFW&ãÒ%â‡FW'&W7G&–ÇÇ6öÆ'ÇVæ—fW'6Â’B"¢6öæf—&ÕöW‡W&–ÖVçFÅ÷W#¢&ööÂÒf–VÆB‡7G&–7CÕG'VR ¤ævWB‚"ö’÷W"öf÷'v&B÷öÆ–6–W2"¦FVbW%öf÷'v&E÷öÆ–6–W2‚“ ¢g&öÒæW‡W2çW%÷öÆ–7’–×÷'B6FÆöwVP¢&WGW&â6FÆöwVR‚ ¤ævWB‚"ö’÷W"öf÷'v&B÷6W76–öç2÷·6W76–öåö–GÒöWf–FVæ6R"¦FVbW%öf÷'v&EöWf–FVæ6R‡6W76–öåö–C§7G"“ ¢G'“§&WGW&âW%öf÷'v&E÷'VææW"æWf–FVæ6U÷&W÷'B‡6W76–öåö–B¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒuTä´äõtåôdõ%t$Eõ4U54”ôâs§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–Ã×7G"†W†2’¢–b7G"†W†2“ÓÒt”ådÄ”Eôdõ%t$Eõ4U54”ôåô”Bs§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$EôUd”DTä4UõTäd”Ä$ÄR"¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$EôUd”DTä4UõTäd”Ä$ÄR" ¤ævWB‚"ö’÷W"öf÷'v&B÷6W76–öç2÷·6W76–öåö–GÒ÷&Wf–Wræ§6öâ"¦FVbW%öf÷'v&E÷&Wf–WuöW‡÷'B‡6W76–öåö–C§7G"“ ¢–×÷'B§6öà¢g&öÒf7F’ç&W7öç6W2–×÷'B&W7öç6P¢g&öÒæW‡W2æf÷'v&E÷&Wf–Wr–×÷'B'V–Æ@¢&W÷'C×W%öf÷'v&EöWf–FVæ6R‡6W76–öåö–B¢&WGW&â&W7öç6R†§6öâæGV×2†'V–ÆB‡&W÷'B’Ç6÷'Eö¶W—3ÕG'VRÆ–æFVçCÓ"ÆÆÆ÷uöæãÔfÇ6R’À¢ÖVF–÷G—SÒ&Æ–6F–öâö§6öâ"À¢†VFW'3×²$6öçFVçBÔF—7÷6—F–öâ#¦bvGF6†ÖVçC²f–ÆVæÖSÒ&æW‡W2Öf÷'v&B×·6W76–öåö–GÒæ§6öâ"rÀ¢$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò ¤ævWB‚"ö’÷W"öf÷'v&B÷6W76–öç2"¦FVbW%öf÷'v&E÷6W76–öç2†Æ–Ö—C¦–çCÓ#“ ¢G'“§&WGW&âW%öf÷'v&E÷'VææW"ç6W76–öç2†Æ–Ö—B¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒ$”ådÄ”Eõ4U54”ôåô„•5Dõ%•ôÄ”Ô•B#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$Eõ4U54”ôå5õTäd”Ä$ÄR"¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$Eõ4U54”ôå5õTäd”Ä$ÄR" ¤ævWB‚"ö’÷W"öf÷'v&B÷&V6÷fW'’"¦FVbW%öf÷'v&E÷&V6÷fW'’†Æ–Ö—C¦–çCÓ#“ ¢G'“§&WGW&âW%öf÷'v&E÷'VææW"ç&V6÷fW'•÷7FGW2†Æ–Ö—B¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒ$”ådÄ”Eõ4U54”ôåô„•5Dõ%•ôÄ”Ô•B#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$Eõ$T4õdU%•õTäd”Ä$ÄR"¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$Eõ$T4õdU%•õTäd”Ä$ÄR" ¤ævWB‚"ö’÷W"öf÷'v&B"¦FVbW%öf÷'v&E÷7FGW2‚“ ¢G'“§&WGW&â²¢§W%öf÷'v&E÷'VææW"ç7FGW2‚’Â¢§W%öf÷'v&E÷'VææW"æF–væ÷7F–72‚—Ğ¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"ÅfÇVTW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$EôD”täõ5D”55õTäd”Ä$ÄR" ¤ævWB‚rö’÷W"öf÷'v&B÷G&–Â×&VF–æW72r¦FVbW%÷G&–Å÷&VF–æW72††÷W'3¦–çCÓs"“ ¢g&öÒæW‡W2çG&–Å÷&VF–æW72–×÷'B'V–ÆBÄ„õU%0¢–b†÷W'2æ÷B–â„õU%3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒt”ådÄ”EõE$”Åõt”äDõrr¢G'“§&WGW&â'V–ÆB‡&W6V&6…ö÷&FW%övFWv’å÷F‚‚’ç&VçBòwW%öf÷'v&Bç7Æ—FS2rÆ†÷W'3Ö†÷W'2À¢÷&FW%övFWv“×&W6V&6…ö÷&FW%övFWv’¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"ÅG—TW'&÷"Ä¶W”W'&÷"Ä&—F†ÖWF–4W'&÷"ÄÖVÖ÷'”W'&÷"Å&V7W'6–öäW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒuE$”Åô„•5Dõ%•õ$Ud”Uuõ$UT•$TBr ¤ævWB‚"ö’÷W"öf÷'v&B÷&VfÆ–v‡B"¦FVbW%öf÷'v&E÷&VfÆ–v‡B‡F–W#§7G#ÒwFW'&W7G&–Âr“ ¢g&öÒæW‡W2æf÷'v&E÷&VfÆ–v‡B–×÷'B'V–ÆBÅD”U%0¢–bF–W"æ÷B–âD”U%3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒt”ådÄ”Eõ$TdÄ”t…EõD”U"r¢G'“§&WGW&â'V–ÆB‡&W6V&6…ö÷&FW%övFWv’ÇW%öf÷'v&E÷'VææW"ÇF–W#×F–W"Ç÷'FÅö7F—fSÖæ÷B÷W6VBÆ†ÇFVCÕöW†V7WF–öåö†ÇFVB‚’¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒuU%õ$TdÄ”t…EõTäd”Ä$ÄRr ¤ç÷7B‚"ö’÷W"öf÷'v&B÷7F'B"¦FVbW%öf÷'v&E÷7F'B†FF¥W$f÷'v&E7F'B“ ¢–bæ÷BFFæ6öæf—&ÕöW‡W&–ÖVçFÅ÷W# ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ$U…U$”ÔTåDÅõU%ô4ôäd•$ÔD”ôåõ$UT•$TB"¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢–b÷W6VB÷"öW†V7WF–öåö†ÇFVB‚“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$5D•dDUõTä„ÅDTEõõ%DÅôd•%5B"¢G'“ ¢&W7VÇC×W%öf÷'v&E÷'VææW"ç7F'B†FFçF–W"ÆFFçöÆ–7•ö–B¢27WB÷W6R6â'&—fRv†–ÆR&W&F–öâ—2FÖ—GF–ærF†R6W76–öâà¢–böW†V7WF–öåö†ÇFVB‚’÷"÷W6VC ¢W%öf÷'v&E÷'VææW"ç7F÷‚u5D%Eô”åDU%%UDTEô%•ô5UEôõ%õU4Rr¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TättUô”åDU%%UDTEô%•ô5UEôõ%õU4R"¢&WGW&â&W7VÇ@¢W†6WBfÇVTW'&÷"2W†3 ¢6öFSÓC’–b7G"†W†2’–â‚tTÔU$tTä5•ô5UEõ$UTU5DTBrÂtTÔU$tTä5•ô„ÅEô5D•dRr’VÇ6RC# ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÖ6öFRÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"Å'VçF–ÖTW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$dõ%t$Eõ5Dõ$tUôõ%õ5D%EõTäd”Ä$ÄR" ¤ç÷7B‚"ö’÷W"öf÷'v&B÷7F÷"¦FVbW%öf÷'v&E÷7F÷‚“ ¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³§&WGW&âW%öf÷'v&E÷'VææW"ç7F÷‚ ¤ç÷7B‚"ö’öW†V7WF–öâö÷W&F–öâÖG&–ÆÂ"¦FVbW†V7WF–öåö÷W&F–öåöG&–ÆÂ†7–6ÆW3¦–çCÓ"“ ¢g&öÒæW‡W2æ÷W&F–öåöG&–ÆÂ–×÷'BG&–ÆÀ¢G'“§&WGW&âG&–ÆÂ†7–6ÆW2¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õU$D”ôåôE$”ÄÅõ5Dõ$tUõTäd”Ä$ÄR" ¤ç÷7B‚"ö’öW†V7WF–öâöGW&&ÆRÖ&Væ6†Ö&²"¦FVbW†V7WF–öåöGW&&ÆUö&Væ6†Ö&²†7–6ÆW3¦–çCÓb“ ¢g&öÒæW‡W2æGW&&ÆUö&Væ6†Ö&²–×÷'B&Væ6†Ö&°¢G'“§&WGW&â&Væ6†Ö&²†7–6ÆW2¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$EU$$ÄUô$Tä4„Ô$µõ5Dõ$tUõTäd”Ä$ÄR" ¦6Æ72&6†—fUFW&Ö–æÅ&WVW7B„&6TÖöFVÂ“ ¢&F6…ö–C¢7G"Òf–VÆB†Ö–åöÆVæwFƒÓÆÖ…öÆVæwFƒÓcBÇGFW&ãÒ%å´Õ¦×£Ó•òÕÒ²B"¢6öæf—&Õö&6†—fS¢&ööÂÒf–VÆB‡7G&–7CÕG'VR ¤ç÷7B‚"ö’öW†V7WF–öâö&6†—fR×FW&Ö–æÂ"¦FVbW†V7WF–öåö&6†—fU÷FW&Ö–æÂ†FF¤&6†—fUFW&Ö–æÅ&WVW7B“ ¢–bFFæ6öæf—&Õö&6†—fR—2æ÷BG'VS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ$U…Ä”4•Eô$4„•dUô4ôäd•$ÔD”ôåõ$UT•$TB"¢–bæ÷BöW†V7WF–öåö†ÇFVB‚’÷"æ÷B÷W6VC ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$$4„•dÅõ$UT•$U5ô„ÅDTEõU4TEõõ%DÂ"¢G'“ ¢&WGW&â²¢§&W6V&6…ö÷&FW%övFWv’æ&6†—fU÷FW&Ö–æÂ†FFæ&F6…ö–B’Â&†ÇFVB#¥G'VRÂ'W6VB#¥G'VRÂ&†—7F÷'•öFVÆWFVB#¤fÇ6WĞ¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%ô$4„•dÅõ5Dõ$tUõTäd”Ä$ÄR" ¤ævWB‚"ö’öW†V7WF–öâö–çFVçB÷¶6Æ–VçEö÷&FW%ö–GÒ"¦FVbW†V7WF–öåö÷&FW%ö†—7F÷'’†6Æ–VçEö÷&FW%ö–C§7G"“ ¢G'“§&WGW&â&W6V&6…ö÷&FW%övFWv’æ÷&FW%÷&W÷'B†6Æ–VçEö÷&FW%ö–B¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2“ÓÒ%Tä´äõtåôõ$DU%ô”B#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCBÆFWF–ÃÒ%Tä´äõtåôõ$DU%ô”B"¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%ô„•5Dõ%•õTäd”Ä$ÄR"¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%ô„•5Dõ%•õTäd”Ä$ÄR" ¤ævWB‚"ö’öW†V7WF–öâ÷&V6÷fW'’"¦FVbW†V7WF–öåö÷&FW%÷&V6÷fW'’‚“ ¢G'“§&WGW&â&W6V&6…ö÷&FW%övFWv’ç&V6÷fW'•÷&W÷'B‚¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%õ$T4õdU%•õTäd”Ä$ÄR" ¤ævWB‚"ö’öW†V7WF–öâöÇ6×W"öfVR×&ö¦V7F–öâÖf—‡GW&R"¦FVbÇ6÷W%öfVUöf—‡GW&R‚“ ¢g&öÒæW‡W2æ'&ö¶W%öfVU÷&ö¦V7F–öâ–×÷'Bf—‡GW&U÷&W÷'@¢G'“§&WGW&â¥4ôå&W7öç6R†f—‡GW&U÷&W÷'B‚’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$ôddÄ”äUô%$ô´U%ôdTUôd•…EU$UõTäd”Ä$ÄR"Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò ¤ævWB‚"ö’öW†V7WF–öâöÇ6×W"÷&WVW7B×&V6÷fW'’Öf—‡GW&R"¦FVbÇ6÷W%÷&WVW7E÷&V6÷fW'•öf—‡GW&R‚“ ¢g&öÒæW‡W2æ'&ö¶W%÷&WVW7Eö¦÷W&æÂ–×÷'Bf—‡GW&U÷&W÷'@¢G'“§&WGW&â¥4ôå&W7öç6R†f—‡GW&U÷&W÷'B‚’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$ôddÄ”äUô%$ô´U%õ$T4õdU%•ôd•…EU$UõTäd”Ä$ÄR"Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò ¤ævWB‚"ö’öW†V7WF–öâöÇ6×W"ö6öçG&7B"¦FVbÇ6÷W%ö6öçG&7B‚“ ¢g&öÒæW‡W2æÇ6÷W%ö6öçG&7B–×÷'B6FÆöwVP¢&WGW&â6FÆöwVR‚ ¤ævWB‚"ö’öW†V7WF–öâö6öææV7F÷'2"¦FVbW†V7WF–öåö6öææV7F÷%ö6FÆör‚“ ¢&WGW&â&W6V&6…ö÷&FW%övFWv’æ6öææV7F–öç2‚ ¦6Æ726–×VÆFVD÷&FW$–çFVçB„&6TÖöFVÂ“ ¢6Æ–VçEö÷&FW%ö–C¢7G"Òf–VÆB†Ö–åöÆVæwFƒÓÆÖ…öÆVæwFƒÓcB¢&öGV7C¢7G"Òf–VÆB‡GFW&ãÒ%â„%D2ÕU4GÄUD‚ÕU4GÅ4ôÂÕU4B’B"¢6–FS¢7G"Òf–VÆB‡GFW&ãÒ%â„%U—Å4TÄÂ’B"¢æ÷F–öæÃ¢fÆöBÒf–VÆB†wCÓÆÆSÓ#¢&÷f–FW#¢7G"Ò&æW‡W5öÆö6Å÷W" ¢F–W#¢7G"Òf–VÆB‡GFW&ãÒ%â‡FW'&W7G&–ÇÇ6öÆ'ÇVæ—fW'6Â’B" ¤ç÷7B‚"ö’öW†V7WF–öâö–çFVçB÷6–×VÆFR"¦FVb6–×VÆFUö÷&FW%ö–çFVçB†FF¥6–×VÆFVD÷&FW$–çFVçB“ ¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢–böW†V7WF–öåö†ÇFVB‚“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô„ÅEô5D•dR"¢–bFFç&÷f–FW"Ò&æW‡W5öÆö6Å÷W"#§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ$U…DU$äÅô%$ô´U%ôäõEôUD„õ$•¤TB"¢–bFFç6–FSÓÒ$%U’# ¢–b÷W6VC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ%õ%DÅõU4TB"¢G'“§&WV—&U÷F–W%öVçG'’†FFçF–W"ÆFFææ÷F–öæÂ¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢24TÄÂ–çFVçB—2ÆÆ÷vVBWfVâ–bF–W"—2F—6VævvVBÂ6ò&—6²×&VGV6–æp¢2W†—B&WVW7G2&Ræ÷B&Æö6¶VBâF†—2vFWv’öæÇ’&V6÷&G2Öö6²4²à¢G'“§&WGW&â&W6V&6…ö÷&FW%övFWv’ç7V&Ö—B†FFæ6Æ–VçEö÷&FW%ö–BÆFFç&öGV7BÆFFç6–FRÆFFææ÷F–öæÂÆFFç&÷f–FW"ÇF–W#ÖFFçF–W"¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%õ5Dõ$tUõTäd”Ä$ÄR" ¤ç÷7B‚"ö’öW†V7WF–öâö–çFVçB÷¶6Æ–VçEö÷&FW%ö–GÒö6æ6VÂ"¦FVb6æ6VÅö÷&FW%ö–çFVçB†6Æ–VçEö÷&FW%ö–C§7G"“ ¢G'“§&WGW&â&W6V&6…ö÷&FW%övFWv’æ6æ6VÂ†6Æ–VçEö÷&FW%ö–B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$õ$DU%õ5Dõ$tUõTäd”Ä$ÄR" ¤ævWB‚"ö’öÖ&¶WG2"¦FVbÖ&¶WEö6FÆör‚“ ¢&WGW&âÆ—7EöÖ&¶WG2‚ ¥ö‡V%ö'V–ÆEöÆö6³Ô%T”ÄEôÄô4° ¦6Æ72&Vg&W6…&WVW7B„&6TÖöFVÂ“ ¢&öGV7C§7G"Òf–VÆB‡GFW&ãÒ%â„%D2ÕU4GÄUD‚ÕU4GÅ4ôÂÕU4B’B"¢w&çVÆ&—G“¦–çBÒ3c ¢6öÆÆV7E÷V&Æ–5ö†—7F÷'“¦&ööÂÒfÇ6P¢Æ–Ö—C¦–çBÒf–VÆB†FVfVÇCÓÆvSÓ#CÆÆSÓ ¤ævWB‚"ö’öÖG&—‚ö‡V"÷&Vg&W6‚"¦FVb‡V%÷&Vg&W6…÷7FGW2‚“§&WGW&â&Vg&W6…÷v÷&¶W"ç7FGW2‚ ¤ç÷7B‚"ö’öÖG&—‚ö‡V"÷&Vg&W6‚÷7F'B"¦FVb‡V%÷&Vg&W6…÷7F'B‡fÇVS¥&Vg&W6…&WVW7B“ ¢G'“§&WGW&â&Vg&W6…÷v÷&¶W"ç7F'B‡fÇVRç&öGV7BÇfÇVRæw&çVÆ&—G’ÇfÇVRæÆ–Ö—BÆFÖ—76–öãÖÆÖ&F¦æ÷B÷W6VBÆ6öÆÆV7E÷V&Æ–5ö†—7F÷'“×fÇVRæ6öÆÆV7E÷V&Æ–5ö†—7F÷'’¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Å'VçF–ÖTW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒu$Te$U4…õ5Dõ$tUôõ%õ5D%EõTäd”Ä$ÄRr ¤ç÷7B‚"ö’öÖG&—‚ö‡V"÷&Vg&W6‚÷7F÷"¦FVb‡V%÷&Vg&W6…÷7F÷‚“§&WGW&â&Vg&W6…÷v÷&¶W"ç7F÷‚ ¤ævWB‚"ö’öÖG&—‚ö‡V""¦FVbÖG&—…ö‡V%÷7FGW2‚“ ¢g&öÒæW‡W2æÖ&¶WEö‡V"–×÷'B7FGW0¢G'“§&WGW&â7FGW2‚¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$…T%õ5Dõ$tUõTäd”Ä$ÄR" ¤ç÷7B‚"ö’öÖG&—‚ö‡V"÷&W&R"¦FVbÖG&—…ö‡V%÷&W&R‡&öGV7C§7G#Ò$%D2ÕU4B"Æw&çVÆ&—G“¦–çCÓ3cÆÆ–Ö—C¦–çCÓ“ ¢g&öÒæW‡W2æ‡V%÷&VF–7F–öâ–×÷'B&W&P¢–bæ÷Bö‡V%ö'V–ÆEöÆö6²æ7V—&R„fÇ6R“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒt…T%ô%T”ÄEôÅ$TE•õ%Tää”ärr¢G'“§&WGW&â&W&R‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$…T%õ5Dõ$tUõTäd”Ä$ÄR"¢f–æÆÇ“¥ö‡V%ö'V–ÆEöÆö6²ç&VÆV6R‚ ¤ç÷7B‚"ö’öÖG&—‚ö‡V"ö'V–ÆB"¦FVbÖG&—…ö‡V%ö'V–ÆB‡&öGV7C§7G#Ò$%D2ÕU4B"Æw&çVÆ&—G“¦–çCÓ3cÆÆ–Ö—C¦–çCÓ“ ¢g&öÒæW‡W2æÖ&¶WEö‡V"–×÷'B&6¶f–ÆÀ¢g&öÒæW‡W2æ‡V%÷&VF–7F–öâ–×÷'B&W&P¢–b&öGV7Bæ÷B–â$ôET5E2÷"w&çVÆ&—G’æ÷B–âƒ3Ã3cÃƒcC’÷"æ÷B#CÃÖÆ–Ö—CÃÓ§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ$…T%ô%T”ÄEõ44õR"¢–bæ÷Bö‡V%ö'V–ÆEöÆö6²æ7V—&R„fÇ6R“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒt…T%ô%T”ÄEôÅ$TE•õ%Tää”ärr¢G'“ ¢6öÆÆV7FVCÖ&6¶f–ÆÂ‡&öGV7BÆw&çVÆ&—G’¢&WGW&âF–7B†6öÆÆV7FVCÖ6öÆÆV7FVBÂ¢§&W&R‡&öGV7BÆw&çVÆ&—G’ÆÆ–Ö—B’¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$…T%õ4õU$4Uôõ%õ5Dõ$tUõTäd”Ä$ÄR"¢f–æÆÇ“¥ö‡V%ö'V–ÆEöÆö6²ç&VÆV6R‚ ¤ævWB‚"ö’öÖG&—‚ö‡V"÷6–væÂ"¦FVbÖG&—…ö‡V%÷6–væÂ‡&öGV7C§7G#Ò$%D2ÕU4B"Æw&çVÆ&—G“¦–çCÓ3c“ ¢g&öÒæW‡W2æÖ&¶WEö‡V"–×÷'BÆFW7EöÖöFVÀ¢g&öÒæW‡W2æ‡V%÷&VF–7F–öâ–×÷'B6–væÀ¢G'“§&WGW&â6–væÂ†ÆFW7EöÖöFVÂ‡&öGV7BÆw&çVÆ&—G’’¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$…T%ôÔôDTÅõTäd”Ä$ÄR" ¤ævWB‚"ö’öÖG&—‚ö66÷VçB"¦FVbÖG&—…ö66÷VçEö†—7F÷'’‡7ã§7G#Ò#B"“ ¢g&öÒæW‡W2æÖG&—…ö66÷VçB–×÷'B†—7F÷'¢G'“§&WGW&â†—7F÷'’‡7â¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$ÔE$•…õ5Dõ$tUõTäd”Ä$ÄR" ¤ç÷7B‚"ö’öÖG&—‚ö66÷VçBöö'6W'fR"¦FVbÖG&—…ö66÷VçEöö'6W'fR‚“ ¢–b÷W6VC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ%õ%DÅôÕU5Eô$Uô5D•dR"¢g&öÒæW‡W2æÖG&—…ö66÷VçB–×÷'B6GW&P¢G'“§&WGW&â6GW&R‚¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$ÔE$•…õ5Dõ$tUõTäd”Ä$ÄR" ¤ævWB‚"ö’öÖG&—‚"¦FVbÖG&—‚‚“ ¢&WGW&â°¢&ÖöFR#¢%5”åD„UD”5õDTÄTÔUE%’"À¢&Æ—fUö÷&FW'2#¤fÇ6RÀ¢&7F—fR#¦æ÷B÷W6VBÀ¢&WfVçG2#¥°¢²'F–W"#§E²'F–W"%ÒÂ&ÖW76vR#§E²&W†×ÆU÷G&FR%ÒÂ'66÷&R#§E²&–ÆÇW7G&F—fU÷6–væÅ÷66÷&R%ÒÀ¢&÷WF6öÖR#§E²&7W'&VçEö÷WF6öÖR%ÒÂ'7–çF†WF–2#¥G'VWĞ¢f÷"B–â6–×VÆFVE÷6æ6†÷B‚¢Ğ¢Ğ ¦6Æ72F–W%W&Ö—76–öä6öÖÖæB„&6TÖöFVÂ“ ¢Væ&ÆVC¢&ööÀ ¤ævWB‚"ö’÷F–W'2÷W&f÷&Öæ6R"¦FVbF–W%ö†—7F÷&–6Å÷W&f÷&Öæ6R‡&öGV7C§7G#Ò$%D2ÕU4B"“ ¢–b&öGV7Bæ÷B–â$ôET5E3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ%Vç7W÷'FVB&W6V&6‚&öGV7B"¢G'“§&WGW&âF–W%÷W&f÷&Öæ6R‡&öGV7B¢W†6WBfÇVTW'&÷"2W†3§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–Ã×7G"†W†2’ ¤ævWB‚"ö’÷F–W'2÷W&Ö—76–öç2"¦FVbF–W%÷W&Ö—76–öå÷7FFR‚“ ¢G'“§&WGW&âF–W%÷W&Ö—76–öç5ö7W'&VçB‚¢W†6WBfÇVTW'&÷"2W†3 ¢FWF–Ã×7G"†W†2’–b7G"†W†2“ÓÒuU%õ4UED”äu5ôDD$4Uô%U5’rVÇ6RuU%õ4UED”äu5õ$Ud”Uuõ$UT•$TBp¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÖFWF–Â¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%U%õ4UED”äu5õ$Ud”Uuõ$UT•$TB" ¤çWB‚"ö’÷F–W'2÷·F–W'Ò÷W&Ö—76–öâ"¦FVb6WE÷F–W%÷W&Ö—76–öâ‡F–W#§7G"Æ6ÖC¥F–W%W&Ö—76–öä6öÖÖæB“ ¢–böW†V7WF–öåö†ÇFVB‚’æB6ÖBæVæ&ÆVC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒ$TÔU$tTä5•ô„ÅEô5D•dR"¢G'“§&WGW&â6öæf–wW&U÷F–W%÷W&Ö—76–öâ‡F–W"Æ6ÖBæVæ&ÆVB¢W†6WBfÇVTW'&÷"2W†3 ¢7FGW3ÓS2–b7G"†W†2“ÓÒuU%õ4UED”äu5ôDD$4Uô%U5’rVÇ6RC# ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFS×7FGW2ÆFWF–Ã×7G"†W†2’¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%U%õ4UED”äu5õ$Ud”Uuõ$UT•$TB" ¤ævWB‚"ö’÷F–W'2"¦FVbF–W'2‚“ ¢G'“§&WGW&â²&FFöÖöFR#¢$”ÄÅU5E$D•dR"Â'WFFVEöB#¦–çB‡F–ÖRçF–ÖR‚’’Â'F–W'2#§6–×VÆFVE÷6æ6†÷B‚’Â'F–W%÷W&Ö—76–öç2#§F–W%÷W&Ö—76–öç5ö7W'&VçB‚•²'F–W'2%×Ğ¢W†6WBfÇVTW'&÷"2W†3 ¢FWF–Ã×7G"†W†2’–b7G"†W†2“ÓÒuU%õ4UED”äu5ôDD$4Uô%U5’rVÇ6RuU%õ4UED”äu5õ$Ud”Uuõ$UT•$TBp¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÖFWF–Â¢W†6WB„õ4W'&÷"Ç7Æ—FS2äW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%U%õ4UED”äu5õ$Ud”Uuõ$UT•$TB" ¦6Æ72f÷VæDÖ&¶WD6öçG&öÂ„&6TÖöFVÂ“ ¢Væ&ÆVC¢&ööÀ ¤ævWB‚rö’÷&W6V&6‚öf÷VæBÖÖ&¶WG2r¦FVbf÷VæEöÖ&¶WE÷7FGW2‚“ ¢&WGW&âf÷VæEöÖ&¶WE÷v÷&¶W"ç7FGW2‚ ¤ç÷7B‚rö’÷&W6V&6‚öf÷VæBÖÖ&¶WG2ö6öçG&öÂr¦FVbf÷VæEöÖ&¶WEö6öçG&öÂ†6ÖC¤f÷VæDÖ&¶WD6öçG&öÂ“ ¢v—F‚öVæv–æUö6öçG&öÅöÆö6³ ¢–bæ÷B6ÖBæVæ&ÆVC§&WGW&âf÷VæEöÖ&¶WE÷v÷&¶W"ç7F÷‚¢–b÷W6VC§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒu5D%Eõõ%DÅôd•%5Br¢G'“§&WGW&âf÷VæEöÖ&¶WE÷v÷&¶W"ç7F'B†ÆÖ&F¦æ÷B÷W6VB¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Å'VçF–ÖTW'&÷"“§&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC’ÆFWF–ÃÒtD•44õdU%•ôd”ä•4„”äuôõ%õTäd”Ä$ÄRr ¤ç÷7B‚"ö’÷÷'FÂ"¦FVb÷'FÂ†6ÖC¥÷'FÄ6öÖÖæB“ ¢vÆö&Â÷W6V@¢–b6ÖBæw&çVÆ&—G’æ÷B–âƒ3Ã3cÃƒcC“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓC#"ÆFWF–ÃÒ%$Te$U4…õ44õR"¢v—F‚öVæv–æUö6öçG&öÅöÆö6³ ¢–b6ÖBæVæ&ÆVBæBæ÷B÷W6VC§&WGW&â7FGW2‚’2–FV×÷FVçC²æWfW"&RÖ&Ò'VFvWG2öâ&WG'’à¢–bæ÷B6ÖBæVæ&ÆVC ¢÷W6VCÕG'VR2fVæ6R6öæ7W'&VçFÇ’&W&–ærVævvR&Vf÷&Rv—F–ærà¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢–b6ÖBæVæ&ÆVC ¢÷W6VCÔfÇ6P¢G'“ ¢ö'6W'fW"ç7F'B‚¢&Vg&W6…÷v÷&¶W"ç7F'B†6ÖBç&öGV7BÆ6ÖBæw&çVÆ&—G’Æ6ÖBæÆ–Ö—BÀ¢FÖ—76–öãÖÆÖ&F¦æ÷B÷W6VBÆ6öÆÆV7E÷V&Æ–5ö†—7F÷'“Ö6ÖBæ6öÆÆV7E÷V&Æ–5ö†—7F÷'’¢–b6ÖBæF—66÷fW%öÖ&¶WG3 ¢G'“¦f÷VæEöÖ&¶WE÷v÷&¶W"ç7F'B†ÆÖ&F¦æ÷B÷W6VB¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Å'VçF–ÖTW'&÷"“¦f÷VæEöÖ&¶WE÷v÷&¶W"ç7F÷‚tõD”ôäÅôD•44õdU%•õTäd”Ä$ÄRr¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"Å'VçF–ÖTW'&÷"“ ¢÷W6VCÕG'VP¢f÷VæEöÖ&¶WE÷v÷&¶W"ç7F÷‚uõ%DÅõ5D%Eôd”ÄTBr¢G'“§&Vg&W6…÷v÷&¶W"ç7F÷‚uõ%DÅõ5D%Eôd”ÄTBr¢f–æÆÇ“¦ö'6W'fW"ç7F÷‚¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ%$U4T$4…õ5D%Eôd”ÄTEôTät”äUôôdb"¢VÇ6S ¢G'“§W%öf÷'v&E÷'VææW"ç7F÷‚uõ%DÅõU4Rr¢f–æÆÇ“ ¢G'“¦ö'6W'fW"ç7F÷‚¢f–æÆÇ“ ¢G'“§&Vg&W6…÷v÷&¶W"ç7F÷‚uõ%DÅõU4Rr¢f–æÆÇ“¦f÷VæEöÖ&¶WE÷v÷&¶W"ç7F÷‚uõ%DÅõU4Rr¢VF—B‚w÷'FÅ÷&W7VÖVBr–b6ÖBæVæ&ÆVBVÇ6Rw÷'FÅ÷W6VBr¢&WGW&â7FGW2‚ ¦FVbö6ö×ÆWFUöVÖW&vVæ7•ö7WB‚“ ¢v—F‚÷G&F–æuö6öçG&öÅöÆö6³ ¢G'“ ¢2–æFWVæFVçB–çFVçB&V6VFW25Æ—FRâ7F–ÆÂ†ÇB÷7F÷–b—G2F—6²w&—FRf–Ç2à¢G'“¦VÖW&vVæ7•ö–çFVçBçW'6—7B‡&W6V&6…ö÷&FW%övFWv’å÷F‚‚’¢f–æÆÇ“ ¢G'“§&W6V&6…ö÷&FW%övFWv’æVÖW&vVæ7•ö†ÇB‚¢f–æÆÇ“§W%öf÷'v&E÷'VææW"ç7F÷‚tTÔU$tTä5•õD…$õEDÄUô5UBr¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"Å'VçF–ÖTW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$„ÅDTEôÄô4ÄÅ•ô5UEô4ÄTåUõTä4ôäd•$ÔTB"¢G'“¦VF—B‚vVÖW&vVæ7•ö†ÇBr¢W†6WB…fÇVTW'&÷"Äõ4W'&÷"Ç7Æ—FS2äW'&÷"“ ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS2ÆFWF–ÃÒ$„ÅDTEôU„T5UD”ôåõ5DõTEôTD•EõTäd”Ä$ÄR"  ¦FVböVÖW&vVæ7•ö7WEöFöæR†gWGW&R“ ¢vÆö&Âö7WE÷VæF–ærÅö7WEö6öæf—&ÖV@¢26ö×ÆWF–öâÂæ÷Bâ…EE6ÆÆW"w2Æ–fWF–ÖRÂ&VÆV6W2F†RVæF–ær&W6WBfVæ6Rà¢v—F‚ö7WEöÆö6³ ¢–bö7WEögWGW&R—2gWGW&S ¢ö7WE÷VæF–æsÓ ¢ö7WEö6öæf—&ÖVCÖæ÷BgWGW&Ræ6æ6VÆÆVB‚’æBgWGW&RæW†6WF–öâ‚’—2æöæP  ¦FVbö6öç7VÖUö6öçG&öÅ÷&W7VÇB†gWGW&R“ ¢2F—66öææV7FVBö6æ6VÆÆVB6ÆÆW"×W7Bæ÷BÆVfRâVæö'6W'fVB7–æ2W†6WF–öâà¢–bæ÷BgWGW&Ræ6æ6VÆÆVB‚“¦gWGW&RæW†6WF–öâ‚  ¦FVböG&–åöVÖW&vVæ7•öÆæR‚“ ¢vÆö&Âö7WEöW†V7WF÷"Åö7WEögWGW&RÅö7WEöÆæUö6Æ÷6–æp¢v—F‚ö7WEöÆö6³ ¢ö7WEöÆæUö6Æ÷6–æsÕG'VP¢W†V7WF÷#Õö7WEöW†V7WF÷ ¢G'“ ¢–bW†V7WF÷"—2æ÷BæöæS¦W†V7WF÷"ç6‡WFF÷vâ‡v—CÕG'VRÆ6æ6VÅögWGW&W3ÔfÇ6R¢f–æÆÇ“ ¢v—F‚ö7WEöÆö6³ ¢–bö7WEöW†V7WF÷"—2W†V7WF÷# ¢ö7WEöW†V7WF÷#ÔæöæP¢ö7WEögWGW&SÔæöæP¢ö7WEöÆæUö6Æ÷6–æsÔfÇ6P  ¦FVb÷6‡WFF÷våöÆ–6F–öå÷v÷&¶W'2‚“ ¢""$G&–âF†R–æFWVæFVçB7WB&Vf÷&Rç’÷&F–æ'’Æ–6F–öâ6ÆVçWâ"" ¢G'“¥öG&–åöVÖW&vVæ7•öÆæR‚¢f–æÆÇ“ ¢G'“§W%öf÷'v&E÷'VææW"ç7F÷‚tÄ”4D”ôåõ4…UDDõtâr¢f–æÆÇ“ ¢G'“¦ö'6W'fW"ç7F÷‚¢f–æÆÇ“ ¢G'“§&Vg&W6…÷v÷&¶W"ç6‡WFF÷vâ‚¢f–æÆÇ“¦f÷VæEöÖ&¶WE÷v÷&¶W"ç6‡WFF÷vâ‚  ¤ç÷7B‚"ö’÷6–×VÆFRö†ÇB"¦7–æ2FVb†ÇB‚“ ¢vÆö&Âö†ÇFVBÅö7WEöWö6‚Åö7WE÷VæF–ærÅö7WEöW†V7WF÷"Åö7WEögWGW&RÅö7WEö6öæf—&ÖV@¢æWuö¦ö#ÔfÇ6P¢v—F‚ö7WEöÆö6³ ¢ö†ÇFVCÕG'VP¢ö7WEö6öæf—&ÖVCÔfÇ6P¢ö7WEöWö6‚³Ó¢W%öf÷'v&E÷'VææW"ç&WVW7Eö7WB‚’2æöæ&ÆÚ±î¸Â¸­yêë¢°k¢G§¦*^