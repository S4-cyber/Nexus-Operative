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

app = FastAPI(title="Nexus Operative", version="0.340.42",lifespan=application_lifespan)
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

@app.get("/api/paper/ai-provider/public-preview/{product}")
def public_ai_research_preview(product: str):
    try:return ai_evidence_preview(product)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/ai-provider/public-preview/send")
def public_ai_research_send(consent:PublicAiConsent,request:Request):
    _require_local_portal(request)
    try:return ai_authorize_preview(consent.product,consent.preview_sha256)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/paper/ai-provider/advisory-benchmark")
def ai_advisory_benchmark():
    return advisory_contract_benchmark()

@app.get("/api/paper/ai-provider/status")
def ai_provider_status():
    return JSONResponse(provider_research_status(),headers={"Cache-Control":"no-store","Pragma":"no-cache"})

@app.post("/api/paper/ai-provider/{role}/research")
def ai_provider_research(role: str, snapshot: dict):
    # Legacy arbitrary-payload route is fail-closed; use reviewed consent.
    raise HTTPException(status_code=403, detail="Use reviewed public-preview consent workflow")

@app.post("/api/paper/model-council")
def model_council_preview(snapshot: dict):
    try:
        return coordinate_model_advisors(snapshot)
    except (ValueError,TypeError,OverflowError) as exc:
        raise HTTPException(status_code=422,detail="Invalid or oversized advisory snapshot")

@app.get("/api/latency")
def latency_status():
    return latency_observatory.summary()

@app.post("/api/paper/evidence-audit")
def evidence_audit(evidence:dict):
    try:
        return latency_observatory.measure("evidence_audit",lambda:audit_evidence(evidence))
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/council")
def paper_council(evidence: dict):
    try:
        # Audit is independent: no asserted 'verified' flag can bypass it.
        audit=latency_observatory.measure('evidence_audit',lambda:audit_evidence(evidence))
        result=latency_observatory.measure('council_decision',lambda:council(evidence))
        result['evidence_audit']=audit
        result['consensus']='REJECT' if audit['decision']!='VERIFIED' else result['consensus']
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/tournament")
def paper_tournament(value: HistoricalReplay):
    try:
        return tournament(value.closes,value.capital)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/walk-forward")
def paper_walk_forward(value: HistoricalReplay):
    try:
        return walk_forward(value.closes,value.capital,value.tier,value.direction)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/replay/record")
def record_historical_replay(value: HistoricalReplay):
    try:
        request=value.model_dump()
        result=replay(value.closes,value.capital,value.tier,value.direction)
        return record_replay(request,result)
    except ValueError as exc:
        if str(exc)=='PAPER_REPLAY_JOURNAL_DATABASE_BUSY':
            raise HTTPException(status_code=503,detail=str(exc))
        raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/paper/replay/history")
def replay_history(limit: int = 20):
    try:
        return {"mode":"LOCAL_PAPER_JOURNAL","simulation_only":True,"runs":recent_replays(limit)}
    except ValueError as exc:
        if str(exc)=='PAPER_REPLAY_JOURNAL_DATABASE_BUSY':
            raise HTTPException(status_code=503,detail=str(exc))
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/risk-screen")
def risk_screen(value: dict):
    # A rejected/missing claim is never accepted as evidence of market profitability.
    return risk_evaluate(value)

class StressRequest(BaseModel):
    equity: float = Field(gt=0, le=1000000)
    notional: float = Field(gt=0, le=1000000)
    direction: str = Field(pattern="^(UP|DOWN)$")
    daily_loss: float = Field(ge=0, le=1000000)

@app.post("/api/paper/stress")
def paper_stress(value: StressRequest):
    try:
        return stress_screen(value.equity,value.notional,value.direction,value.daily_loss)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

class DepositPreview(BaseModel):
    amount: float = Field(gt=0, le=1000000)
    cadence: str = Field(pattern="^(once|weekly|monthly)$")

def simulated_snapshot():
    elapsed = int((time.monotonic()-_started)//5)
    return [
       {"tier":name, "risk":data["risk"], "illustrative_signal_score":max(5,min(85,data["score"]+round(math.sin((elapsed+i*3)*0.35)*5))),
        "score_is_win_probability":False, "maximum_position_fraction":data["max_weight"],
        "example_trade":"Synthetic ETF watchlist: paper research only",
        "current_outcome":"No live position", "strategy_approach":data["approach"]}
       for i,(name,data) in enumerate(_tiers.items())
    ]

@app.get("/api/roadmap")
def release_roadmap():
    from nexus.release_roadmap import snapshot
    return snapshot()

def status():
    _execution_halted()
    with _cut_lock:
        halted=_halted;pending=bool(_cut_pending)
        cut_state='CLEAR' if not halted else 'PENDING' if pending else 'VERIFIED' if _cut_confirmed else 'UNCONFIRMED'
    return {"version":"0.340.42", "mode":"SYNTHETIC_PAPER_ONLY", "portal_enabled":not _paused,
      "halted":halted, "emergency_cut_pending":pending,"emergency_cut_state":cut_state,
      "broker_connected":False,"bank_connected":False,
      "live_orders_enabled":False, "money_movement_enabled":False, "ai_connected":False}

@app.get("/api/status")
async def runtime_status():
    # A bounded latch lstat adopts standalone cuts; no SQLite/feed/general worker.
    return JSONResponse(status(),headers={"Cache-Control":"no-store"})

@app.post("/api/candle-research/{product}/regime-stress")
def candle_regime_stress(product: str, granularity: int = 3600, limit: int = 300):
    try:return analyze_regime_stress(product,granularity,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/candle-research/{product}/execution-stress")
def candle_execution_stress(product: str, granularity: int = 3600, limit: int = 300):
    try:return execution_realism_assess(product,granularity,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/candle-research/{product}/cost-sensitivity")
def candle_cost_sensitivity(product: str, granularity: int = 3600, limit: int = 300):
    try:return evaluate_cost_sensitivity(product,granularity,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/candle-research/{product}/admission")
def candle_research_admission(product: str, granularity: int = 3600, limit: int = 300):
    try:
        report=review_candle_research(product,granularity,limit)
        return {k:v for k,v in report.items() if k!="closes"}
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/candle-research/{product}/evaluate")
def candle_research_evaluate(product: str, granularity: int = 3600, limit: int = 300):
    try:return evaluate_candle_research(product,granularity=granularity,limit=limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/historical-candles/{product}/collect")
def historical_candles_collect(product: str, granularity: int = 300):
    if product not in PRODUCTS:raise HTTPException(status_code=404,detail="Unsupported product")
    try:return collect_history_candles(product,granularity)
    except ValueError:raise HTTPException(status_code=503,detail="Historical candle source unavailable or invalid")

@app.get("/api/historical-candles/{product}")
def historical_candles_history(product: str, granularity: int = 300, limit: int = 300):
    try:return read_history_candles(product,granularity,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/research-ticks/{product}/capture")
def capture_research_tick(product: str):
    if product not in PRODUCTS:raise HTTPException(status_code=404,detail="Unsupported product")
    try:return capture_tick(product)
    except (ValueError,TypeError):raise HTTPException(status_code=503,detail="Research quote unavailable or invalid")

@app.get("/api/research-ticks/{product}")
def get_research_ticks(product: str, limit: int = 250, gap_seconds: float = 60):
    try:return chronological_tick_dataset(product,limit,gap_seconds)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/market-evidence/{product}/quality")
def market_evidence_quality(product: str, limit: int = 100, gap_seconds: float = 60):
    try:return market_data_quality(product,limit,gap_seconds)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/market-evidence")
def market_evidence_history(product: str | None = None, limit: int = 25):
    try:return recent_market_evidence(product,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/market-evidence/{product}/capture")
def market_evidence_capture(product: str):
    if product not in PRODUCTS:raise HTTPException(status_code=404,detail="Unsupported product")
    try:return capture_market_evidence(product)
    except ValueError:raise HTTPException(status_code=503,detail="Read-only market evidence unavailable")

@app.get("/api/public-quote/{product}/cross-venue")
def public_cross_venue(product: str):
    if product not in PRODUCTS:
        raise HTTPException(status_code=404,detail="Market product not enabled")
    return crosscheck(product)

@app.get("/api/public-quote/{product}/quality")
def quote_quality(product: str):
    if product not in PRODUCTS:
        raise HTTPException(status_code=404,detail="Product not allowlisted")
    try:
        return feed_guard.evaluate(read_ticker(product))
    except ValueError:
        return {"component":"SINGLE_SOURCE_FEED_GUARD","product":product,
                "decision":"VETO","reasons":["PROVIDER_UNAVAILABLE"],
                "independent_price_verification":False,"live_orders_enabled":False}

@app.get("/api/public-data/efficiency")
def public_data_efficiency():
    from nexus.public_data import efficiency
    return JSONResponse(efficiency(),headers={"Cache-Control":"no-store"})

@app.get("/api/public-quote/{product}")
def public_quote(product: str):
    if product not in PRODUCTS:
        raise HTTPException(status_code=404,detail="Market product is not enabled")
    try:
        return read_ticker(product)
    except ValueError:
        raise HTTPException(status_code=503,detail="Read-only public quote unavailable or stale")

class PortfolioInit(BaseModel):
    paper_capital: float = Field(gt=0, le=1000000)

class PaperTrade(BaseModel):
    product: str = Field(pattern="^(BTC-USD|ETH-USD|SOL-USD)$")
    notional: float = Field(gt=0, le=1000000)
    tier: str = Field(pattern="^(terrestrial|solar|universal)$")

class PaperClose(BaseModel):
    product: str = Field(pattern="^(BTC-USD|ETH-USD|SOL-USD)$")

@app.get("/api/observer/status")
def observer_status():
    return observer.status()

@app.get("/api/observer/history")
def observer_history(limit:int=30):
    try:return {"mode":"READ_ONLY_OBSERVER","events":observation_history(limit),"orders_enabled":False}
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/strategy-branches")
def strategy_branches():
    return available_branches()

@app.get("/api/paper/portfolio/recovery-report")
def portfolio_recovery_report():
    return recovery_report()

@app.post("/api/paper/portfolio/snapshot")
def portfolio_local_snapshot():
    try:
        return backup_snapshot()
    except ValueError as exc:
        detail=str(exc)
        if not detail.startswith("PAPER_SNAPSHOT_"):
            detail="PAPER_SNAPSHOT_UNAVAILABLE"
        raise HTTPException(status_code=503,detail=detail)
    except (OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=503,detail="PAPER_SNAPSHOT_UNAVAILABLE")

@app.get("/api/paper/portfolio/integrity")
def paper_portfolio_integrity():
    return audit_portfolio()

@app.get("/api/paper/portfolio")
def paper_portfolio():
    try:return paper_portfolio_state()
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail='PAPER_PORTFOLIO_UNAVAILABLE')

@app.post("/api/paper/portfolio/init")
def paper_portfolio_init(data:PortfolioInit):
    try:return init_paper_portfolio(data.paper_capital)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))

@app.post("/api/paper/portfolio/buy")
def paper_portfolio_buy(data:PaperTrade):
    if _execution_halted():raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
    if _paused:raise HTTPException(status_code=409,detail="PORTAL_PAUSED")
    with _cut_lock:epoch=_cut_epoch
    try:
        require_tier_entry(data.tier,data.notional)
        quote=read_ticker(data.product)
        with _trading_control_lock:
            if _execution_halted() or epoch!=_cut_epoch:raise HTTPException(status_code=409,detail="EMERGENCY_CUT_INTERRUPTED_TRADE")
            if _paused:raise HTTPException(status_code=409,detail="PORTAL_PAUSED")
            require_tier_entry(data.tier,data.notional)
            return paper_buy(data.product,data.notional,quote)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/portfolio/sell")
def paper_portfolio_sell(data:PaperClose):
    if _execution_halted():raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
    with _cut_lock:epoch=_cut_epoch
    try:
        quote=read_ticker(data.product)
        with _trading_control_lock:
            if _execution_halted() or epoch!=_cut_epoch:raise HTTPException(status_code=409,detail="EMERGENCY_CUT_INTERRUPTED_TRADE")
            return paper_sell(data.product,quote)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/paper/portfolio/reconcile")
def paper_portfolio_reconcile():
    snapshot=paper_portfolio_state()
    quotes={}
    for position in snapshot["positions"]:
        try:quotes[position["product"]]=read_ticker(position["product"])
        except ValueError:
            return {"reconciled":False,"reason":"Quote unavailable", "simulation_only":True}
    return reconcile_paper(quotes)

@app.get("/api/expansion/roadmap")
def expansion_initiative_roadmap():
    return expansion_roadmap()

@app.post("/api/expansion/{avenue}/screen")
def expansion_initiative_screen(avenue: str, evidence: dict):
    try:return assess_expansion_evidence(avenue,evidence)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/markets/discover")
def discover_public_market_universe(limit: int = 100, offset: int = 0, search: str = ""):
    try:return market_universe_page(limit=limit,offset=offset,search=search)
    except ValueError as exc:
        raise HTTPException(status_code=503 if "UNAVAILABLE" in str(exc) else 422,detail=str(exc))

@app.get("/api/execution/decision-benchmark")
def execution_decision_benchmark(count:int=300):
    try:return offline_decision_benchmark(count)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

class PaperForwardStart(BaseModel):
    model_config={'extra':'forbid'}
    policy_id: str = Field(default='btc_momentum_experiment_v1',min_length=1,max_length=64)
    tier: str = Field(pattern="^(terrestrial|solar|universal)$")
    confirm_experimental_paper: bool = Field(strict=True)

@app.get("/api/paper/forward/policies")
def paper_forward_policies():
    from nexus.paper_policy import catalogue
    return catalogue()

@app.get("/api/paper/forward/sessions/{session_id}/evidence")
def paper_forward_evidence(session_id:str):
    try:return paper_forward_runner.evidence_report(session_id)
    except ValueError as exc:
        if str(exc)=='UNKNOWN_FORWARD_SESSION':raise HTTPException(status_code=404,detail=str(exc))
        if str(exc)=='INVALID_FORWARD_SESSION_ID':raise HTTPException(status_code=422,detail=str(exc))
        raise HTTPException(status_code=503,detail="FORWARD_EVIDENCE_UNAVAILABLE")
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_EVIDENCE_UNAVAILABLE")

@app.get("/api/paper/forward/sessions/{session_id}/review.json")
def paper_forward_review_export(session_id:str):
    import json
    from fastapi.responses import Response
    from nexus.forward_review import build
    report=paper_forward_evidence(session_id)
    return Response(json.dumps(build(report),sort_keys=True,indent=2,allow_nan=False),
                    media_type="application/json",
                    headers={"Content-Disposition":f'attachment; filename="nexus-forward-{session_id}.json"',
                             "Cache-Control":"no-store"})

@app.get("/api/paper/forward/sessions")
def paper_forward_sessions(limit:int=20):
    try:return paper_forward_runner.sessions(limit)
    except ValueError as exc:
        if str(exc)=="INVALID_SESSION_HISTORY_LIMIT":raise HTTPException(status_code=422,detail=str(exc))
        raise HTTPException(status_code=503,detail="FORWARD_SESSIONS_UNAVAILABLE")
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_SESSIONS_UNAVAILABLE")

@app.get("/api/paper/forward/recovery")
def paper_forward_recovery(limit:int=20):
    try:return paper_forward_runner.recovery_status(limit)
    except ValueError as exc:
        if str(exc)=="INVALID_SESSION_HISTORY_LIMIT":raise HTTPException(status_code=422,detail=str(exc))
        raise HTTPException(status_code=503,detail="FORWARD_RECOVERY_UNAVAILABLE")
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_RECOVERY_UNAVAILABLE")

@app.get("/api/paper/forward")
def paper_forward_status():
    try:return {**paper_forward_runner.status(),**paper_forward_runner.diagnostics()}
    except (OSError,sqlite3.Error,ValueError):raise HTTPException(status_code=503,detail="FORWARD_DIAGNOSTICS_UNAVAILABLE")

@app.get('/api/paper/forward/trial-readiness')
def paper_trial_readiness(hours:int=72):
    from nexus.trial_readiness import build,HOURS
    if hours not in HOURS:raise HTTPException(status_code=422,detail='INVALID_TRIAL_WINDOW')
    try:return build(research_order_gateway._path().parent/'paper_forward.sqlite3',hours=hours,
        order_gateway=research_order_gateway)
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,ArithmeticError,MemoryError,RecursionError):
        raise HTTPException(status_code=503,detail='TRIAL_HISTORY_REVIEW_REQUIRED')

@app.get("/api/paper/forward/preflight")
def paper_forward_preflight(tier:str='terrestrial'):
    from nexus.forward_preflight import build,TIERS
    if tier not in TIERS:raise HTTPException(status_code=422,detail='INVALID_PREFLIGHT_TIER')
    try:return build(research_order_gateway,paper_forward_runner,tier=tier,portal_active=not _paused,halted=_execution_halted())
    except (ValueError,OSError,sqlite3.Error):raise HTTPException(status_code=503,detail='PAPER_PREFLIGHT_UNAVAILABLE')

@app.post("/api/paper/forward/start")
def paper_forward_start(data:PaperForwardStart):
    if not data.confirm_experimental_paper:
        raise HTTPException(status_code=422,detail="EXPERIMENTAL_PAPER_CONFIRMATION_REQUIRED")
    with _trading_control_lock:
        if _paused or _execution_halted():raise HTTPException(status_code=409,detail="ACTIVATE_UNHALTED_PORTAL_FIRST")
        try:
            result=paper_forward_runner.start(data.tier,data.policy_id)
            # A cut/pause can arrive while preparation is admitting the session.
            if _execution_halted() or _paused:
                paper_forward_runner.stop('START_INTERRUPTED_BY_CUT_OR_PAUSE')
                raise HTTPException(status_code=409,detail="ENGAGE_INTERRUPTED_BY_CUT_OR_PAUSE")
            return result
        except ValueError as exc:
            code=409 if str(exc) in ('EMERGENCY_CUT_REQUESTED','EMERGENCY_HALT_ACTIVE') else 422
            raise HTTPException(status_code=code,detail=str(exc))
        except (OSError,sqlite3.Error,RuntimeError):raise HTTPException(status_code=503,detail="FORWARD_STORAGE_OR_START_UNAVAILABLE")

@app.post("/api/paper/forward/stop")
def paper_forward_stop():
    with _trading_control_lock:return paper_forward_runner.stop()

@app.post("/api/execution/operation-drill")
def execution_operation_drill(cycles:int=12):
    from nexus.operation_drill import drill
    try:return drill(cycles)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="OPERATION_DRILL_STORAGE_UNAVAILABLE")

@app.post("/api/execution/durable-benchmark")
def execution_durable_benchmark(cycles:int=16):
    from nexus.durable_benchmark import benchmark
    try:return benchmark(cycles)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="DURABLE_BENCHMARK_STORAGE_UNAVAILABLE")

class ArchiveTerminalRequest(BaseModel):
    batch_id: str = Field(min_length=1,max_length=64,pattern="^[A-Za-z0-9_-]+$")
    confirm_archive: bool = Field(strict=True)

@app.post("/api/execution/archive-terminal")
def execution_archive_terminal(data:ArchiveTerminalRequest):
    if data.confirm_archive is not True:
        raise HTTPException(status_code=422,detail="EXPLICIT_ARCHIVE_CONFIRMATION_REQUIRED")
    if not _execution_halted() or not _paused:
        raise HTTPException(status_code=409,detail="ARCHIVAL_REQUIRES_HALTED_PAUSED_PORTAL")
    try:
        return {**research_order_gateway.archive_terminal(data.batch_id),"halted":True,"paused":True,"history_deleted":False}
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="ORDER_ARCHIVAL_STORAGE_UNAVAILABLE")

@app.get("/api/execution/intent/{client_order_id}")
def execution_order_history(client_order_id:str):
    try:return research_order_gateway.order_report(client_order_id)
    except ValueError as exc:
        if str(exc)=="UNKNOWN_ORDER_ID":raise HTTPException(status_code=404,detail="UNKNOWN_ORDER_ID")
        raise HTTPException(status_code=503,detail="ORDER_HISTORY_UNAVAILABLE")
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="ORDER_HISTORY_UNAVAILABLE")

@app.get("/api/execution/recovery")
def execution_order_recovery():
    try:return research_order_gateway.recovery_report()
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail="ORDER_RECOVERY_UNAVAILABLE")

@app.get("/api/execution/alpaca-paper/fee-projection-fixture")
def alpaca_paper_fee_fixture():
    from nexus.broker_fee_projection import fixture_report
    try:return JSONResponse(fixture_report(),headers={"Cache-Control":"no-store"})
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail="OFFLINE_BROKER_FEE_FIXTURE_UNAVAILABLE",headers={"Cache-Control":"no-store"})

@app.get("/api/execution/alpaca-paper/request-recovery-fixture")
def alpaca_paper_request_recovery_fixture():
    from nexus.broker_request_journal import fixture_report
    try:return JSONResponse(fixture_report(),headers={"Cache-Control":"no-store"})
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail="OFFLINE_BROKER_RECOVERY_FIXTURE_UNAVAILABLE",headers={"Cache-Control":"no-store"})

@app.get("/api/execution/alpaca-paper/contract")
def alpaca_paper_contract():
    from nexus.alpaca_paper_contract import catalogue
    return catalogue()

@app.get("/api/execution/connectors")
def execution_connector_catalog():
    return research_order_gateway.connections()

class SimulatedOrderIntent(BaseModel):
    client_order_id: str = Field(min_length=1,max_length=64)
    product: str = Field(pattern="^(BTC-USD|ETH-USD|SOL-USD)$")
    side: str = Field(pattern="^(BUY|SELL)$")
    notional: float = Field(gt=0,le=200)
    provider: str = "nexus_local_paper"
    tier: str = Field(pattern="^(terrestrial|solar|universal)$")

@app.post("/api/execution/intent/simulate")
def simulate_order_intent(data:SimulatedOrderIntent):
    with _trading_control_lock:
        if _execution_halted():raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
        if data.provider!="nexus_local_paper":raise HTTPException(status_code=422,detail="EXTERNAL_BROKER_NOT_AUTHORIZED")
        if data.side=="BUY":
            if _paused:raise HTTPException(status_code=409,detail="PORTAL_PAUSED")
            try:require_tier_entry(data.tier,data.notional)
            except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
        # SELL intent is allowed even if a tier is disengaged, so risk-reducing
        # exit requests are not blocked. This gateway only records a mock ACK.
        try:return research_order_gateway.submit(data.client_order_id,data.product,data.side,data.notional,data.provider,tier=data.tier)
        except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
        except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="ORDER_STORAGE_UNAVAILABLE")

@app.post("/api/execution/intent/{client_order_id}/cancel")
def cancel_order_intent(client_order_id:str):
    try:return research_order_gateway.cancel(client_order_id)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="ORDER_STORAGE_UNAVAILABLE")

@app.get("/api/markets")
def market_catalog():
    return list_markets()

_hub_build_lock=BUILD_LOCK

class RefreshRequest(BaseModel):
    product:str = Field(pattern="^(BTC-USD|ETH-USD|SOL-USD)$")
    granularity:int = 3600
    collect_public_history:bool = False
    limit:int = Field(default=1000,ge=240,le=1000)

@app.get("/api/matrix/hub/refresh")
def hub_refresh_status():return refresh_worker.status()

@app.post("/api/matrix/hub/refresh/start")
def hub_refresh_start(value:RefreshRequest):
    try:return refresh_worker.start(value.product,value.granularity,value.limit,admission=lambda:not _paused,collect_public_history=value.collect_public_history)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))
    except (OSError,RuntimeError):raise HTTPException(status_code=503,detail='REFRESH_STORAGE_OR_START_UNAVAILABLE')

@app.post("/api/matrix/hub/refresh/stop")
def hub_refresh_stop():return refresh_worker.stop()

@app.get("/api/matrix/hub")
def matrix_hub_status():
    from nexus.market_hub import status
    try:return status()
    except (ValueError,OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="HUB_STORAGE_UNAVAILABLE")

@app.post("/api/matrix/hub/prepare")
def matrix_hub_prepare(product:str="BTC-USD",granularity:int=3600,limit:int=1000):
    from nexus.hub_prediction import prepare
    if not _hub_build_lock.acquire(False):raise HTTPException(status_code=409,detail='HUB_BUILD_ALREADY_RUNNING')
    try:return prepare(product,granularity,limit)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="HUB_STORAGE_UNAVAILABLE")
    finally:_hub_build_lock.release()

@app.post("/api/matrix/hub/build")
def matrix_hub_build(product:str="BTC-USD",granularity:int=3600,limit:int=1000):
    from nexus.market_hub import backfill
    from nexus.hub_prediction import prepare
    if product not in PRODUCTS or granularity not in (300,3600,86400) or not 240<=limit<=10000:raise HTTPException(status_code=422,detail="HUB_BUILD_SCOPE")
    if not _hub_build_lock.acquire(False):raise HTTPException(status_code=409,detail='HUB_BUILD_ALREADY_RUNNING')
    try:
        collected=backfill(product,granularity)
        return dict(collected=collected,**prepare(product,granularity,limit))
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="HUB_SOURCE_OR_STORAGE_UNAVAILABLE")
    finally:_hub_build_lock.release()

@app.get("/api/matrix/hub/signal")
def matrix_hub_signal(product:str="BTC-USD",granularity:int=3600):
    from nexus.market_hub import latest_model
    from nexus.hub_prediction import signal
    try:return signal(latest_model(product,granularity))
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="HUB_MODEL_UNAVAILABLE")

@app.get("/api/matrix/account")
def matrix_account_history(span:str="1d"):
    from nexus.matrix_account import history
    try:return history(span)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="MATRIX_STORAGE_UNAVAILABLE")

@app.post("/api/matrix/account/observe")
def matrix_account_observe():
    if _paused:raise HTTPException(status_code=409,detail="PORTAL_MUST_BE_ACTIVE")
    from nexus.matrix_account import capture
    try:return capture()
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="MATRIX_STORAGE_UNAVAILABLE")

@app.get("/api/matrix")
def matrix():
    return {
      "mode":"SYNTHETIC_TELEMETRY",
      "live_orders":False,
      "active":not _paused,
      "events":[
        {"tier":t["tier"],"message":t["example_trade"],"score":t["illustrative_signal_score"],
         "outcome":t["current_outcome"],"synthetic":True}
        for t in simulated_snapshot()
      ]
    }

class TierPermissionCommand(BaseModel):
    enabled: bool

@app.get("/api/tiers/performance")
def tier_historical_performance(product:str="BTC-USD"):
    if product not in PRODUCTS:raise HTTPException(status_code=422,detail="Unsupported research product")
    try:return tier_performance(product)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/tiers/permissions")
def tier_permission_state():
    try:return tier_permissions_current()
    except ValueError as exc:
        detail=str(exc) if str(exc)=='PAPER_SETTINGS_DATABASE_BUSY' else 'PAPER_SETTINGS_REVIEW_REQUIRED'
        raise HTTPException(status_code=503,detail=detail)
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="PAPER_SETTINGS_REVIEW_REQUIRED")

@app.put("/api/tiers/{tier}/permission")
def set_tier_permission(tier:str,cmd:TierPermissionCommand):
    if _execution_halted() and cmd.enabled:raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
    try:return configure_tier_permission(tier,cmd.enabled)
    except ValueError as exc:
        status=503 if str(exc)=='PAPER_SETTINGS_DATABASE_BUSY' else 422
        raise HTTPException(status_code=status,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="PAPER_SETTINGS_REVIEW_REQUIRED")

@app.get("/api/tiers")
def tiers():
    try:return {"data_mode":"ILLUSTRATIVE", "updated_at":int(time.time()), "tiers":simulated_snapshot(),"tier_permissions":tier_permissions_current()["tiers"]}
    except ValueError as exc:
        detail=str(exc) if str(exc)=='PAPER_SETTINGS_DATABASE_BUSY' else 'PAPER_SETTINGS_REVIEW_REQUIRED'
        raise HTTPException(status_code=503,detail=detail)
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="PAPER_SETTINGS_REVIEW_REQUIRED")

class FoundMarketControl(BaseModel):
    enabled: bool

@app.get('/api/research/found-markets')
def found_market_status():
    return found_market_worker.status()

@app.post('/api/research/found-markets/control')
def found_market_control(cmd:FoundMarketControl):
    with _engine_control_lock:
        if not cmd.enabled:return found_market_worker.stop()
        if _paused:raise HTTPException(status_code=409,detail='START_PORTAL_FIRST')
        try:return found_market_worker.start(lambda:not _paused)
        except (ValueError,OSError,RuntimeError):raise HTTPException(status_code=409,detail='DISCOVERY_FINISHING_OR_UNAVAILABLE')

@app.post("/api/portal")
def portal(cmd:PortalCommand):
    global _paused
    if cmd.granularity not in (300,3600,86400):
        raise HTTPException(status_code=422,detail="REFRESH_SCOPE")
    with _engine_control_lock:
        if cmd.enabled and not _paused:return status()  # Idempotent; never re-arm budgets on a retry.
        if not cmd.enabled:
            _paused=True  # Fence a concurrently preparing Engage before waiting.
        with _trading_control_lock:
            if cmd.enabled:
                _paused=False
                try:
                    observer.start()
                    refresh_worker.start(cmd.product,cmd.granularity,cmd.limit,
                        admission=lambda:not _paused,collect_public_history=cmd.collect_public_history)
                    if cmd.discover_markets:
                        try:found_market_worker.start(lambda:not _paused)
                        except (ValueError,OSError,RuntimeError):found_market_worker.stop('OPTIONAL_DISCOVERY_UNAVAILABLE')
                except (ValueError,OSError,sqlite3.Error,RuntimeError):
                    _paused=True
                    found_market_worker.stop('PORTAL_START_FAILED')
                    try:refresh_worker.stop('PORTAL_START_FAILED')
                    finally:observer.stop()
                    raise HTTPException(status_code=503,detail="RESEARCH_START_FAILED_ENGINE_OFF")
            else:
                try:paper_forward_runner.stop('PORTAL_PAUSE')
                finally:
                    try:observer.stop()
                    finally:
                        try:refresh_worker.stop('PORTAL_PAUSE')
                        finally:found_market_worker.stop('PORTAL_PAUSE')
            audit('portal_resumed' if cmd.enabled else 'portal_paused')
            return status()

def _complete_emergency_cut():
    with _trading_control_lock:
        try:
            # Independent intent precedes SQLite. Still halt/stop if its disk write fails.
            try:emergency_intent.persist(research_order_gateway._path())
            finally:
                try:research_order_gateway.emergency_halt()
                finally:paper_forward_runner.stop('EMERGENCY_THROTTLE_CUT')
        except (ValueError,OSError,sqlite3.Error,RuntimeError):
            raise HTTPException(status_code=503,detail="HALTED_LOCALLY_CUT_CLEANUP_UNCONFIRMED")
        try:audit('emergency_halt')
        except (ValueError,OSError,sqlite3.Error):
            raise HTTPException(status_code=503,detail="HALTED_EXECUTION_STOPPED_AUDIT_UNAVAILABLE")


def _emergency_cut_done(future):
    global _cut_pending,_cut_confirmed
    # Completion, not an HTTP caller's lifetime, releases the pending reset fence.
    with _cut_lock:
        if _cut_future is future:
            _cut_pending=0
            _cut_confirmed=not future.cancelled() and future.exception() is None


def _consume_control_result(future):
    # A disconnected/cancelled caller must not leave an unobserved async exception.
    if not future.cancelled():future.exception()


def _drain_emergency_lane():
    global _cut_executor,_cut_future,_cut_lane_closing
    with _cut_lock:
        _cut_lane_closing=True
        executor=_cut_executor
    try:
        if executor is not None:executor.shutdown(wait=True,cancel_futures=False)
    finally:
        with _cut_lock:
            if _cut_executor is executor:
                _cut_executor=None
                _cut_future=None
            _cut_lane_closing=False


def _shutdown_application_workers():
    """Drain the independent cut before any ordinary application cleanup."""
    try:_drain_emergency_lane()
    finally:
        try:paper_forward_runner.stop('APPLICATION_SHUTDOWN')
        finally:
            try:observer.stop()
            finally:
                try:refresh_worker.shutdown()
                finally:found_market_worker.shutdown()


@app.post("/api/simulate/halt")
async def halt():
    global _halted,_cut_epoch,_cut_pending,_cut_executor,_cut_future,_cut_confirmed
    new_job=False
    with _cut_lock:
        _halted=True
        _cut_confirmed=False
        _cut_epoch+=1
        paper_forward_runner.request_cut()  # Nonblocking execution veto before any durable wait.
        if _cut_lane_closing:
            raise HTTPException(status_code=503,detail="HALTED_LOCALLY_APPLICATION_SHUTTING_DOWN")
        if _cut_pending:
            future=_cut_future  # Coalesce repeats; no unbounded executor queue.
        else:
            _cut_pending=1
            try:
                if _cut_executor is None:
                    _cut_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nexus-emergency-cut')
                future=_cut_executor.submit(_complete_emergency_cut)
                _cut_future=future
                new_job=True
            except Exception:
                # submit() may enqueue before thread creation fails: drain that item too.
                failed_executor=_cut_executor
                _cut_executor=None
                _cut_future=None
                _cut_pending=0
                if failed_executor is not None:
                    failed_executor.shutdown(wait=False,cancel_futures=True)
                raise HTTPException(status_code=503,detail="HALTED_LOCALLY_CONTROL_WORKER_UNAVAILABLE")
    # Register outside _cut_lock: an already-completed Future calls this inline.
    if new_job:future.add_done_callback(_emergency_cut_done)
    waiting=asyncio.wrap_future(future)
    waiting.add_done_callback(_consume_control_result)
    await asyncio.shield(waiting)  # Client cancellation never cancels durable cleanup.
    return status()

class HaltResetConfirmation(BaseModel):
    confirm_reset: bool

def _complete_halt_reset(epoch):
    global _halted,_cut_confirmed
    with _trading_control_lock:
        with _cut_lock:
            if _cut_pending or epoch!=_cut_epoch:
                raise HTTPException(status_code=409,detail="EMERGENCY_CUT_IN_PROGRESS")
            if not _halted:raise HTTPException(status_code=409,detail="Emergency halt is not active")
            _cut_confirmed=False  # Reset may commit before a later intent-clear failure.
        try:
            paper_forward_runner.stop('THROTTLE_RESET_STAYS_DISENGAGED')
            research_order_gateway.reset()
            # Serialized with cut persistence; a newer queued cut writes after this reset.
            emergency_intent.clear(research_order_gateway._path())
        except (ValueError,OSError,sqlite3.Error):
            # reset() may already have committed and clear() may have unlinked the
            # intent before its directory sync fails. Re-establish both durable
            # barriers before reporting the reset as unconfirmed. Each operation
            # is attempted even if the other fails; the process-local cut remains.
            try:
                try:emergency_intent.persist(research_order_gateway._path())
                finally:research_order_gateway.emergency_halt()
            except (ValueError,OSError,sqlite3.Error,RuntimeError):
                pass
            raise HTTPException(status_code=503,detail="HALT_RESET_STORAGE_UNAVAILABLE")
        with _cut_lock:
            interrupted=epoch!=_cut_epoch or bool(_cut_pending)
            if not interrupted:
                paper_forward_runner.clear_cut_signal()
                _halted=False
                _cut_confirmed=False
        if interrupted:
            # A newer cut always wins, including one received during durable reset.
            try:
                try:emergency_intent.persist(research_order_gateway._path())
                finally:research_order_gateway.emergency_halt()
            except (ValueError,OSError,sqlite3.Error):
                raise HTTPException(status_code=503,detail="HALTED_LOCALLY_ORDER_STORAGE_UNAVAILABLE")
            raise HTTPException(status_code=409,detail="RESET_INTERRUPTED_BY_EMERGENCY_CUT")
        audit("emergency_halt_reset_disengaged")
        return status()

@app.post("/api/simulate/halt/reset")
async def reset_emergency_halt(confirmation:HaltResetConfirmation):
    if confirmation.confirm_reset is not True:
        raise HTTPException(status_code=422,detail="Explicit halt reset confirmation required")
    # Adopt a standalone latch before capturing the reset epoch.
    _execution_halted()
    # Capture intent before general-worker scheduling, not after a queued request wakes.
    with _cut_lock:
        epoch=_cut_epoch
        if _cut_pending:raise HTTPException(status_code=409,detail="EMERGENCY_CUT_IN_PROGRESS")
        if not _halted:raise HTTPException(status_code=409,detail="Emergency halt is not active")
    return await asyncio.to_thread(_complete_halt_reset,epoch)

@app.post("/api/funding/preview")
def funding_preview(data:DepositPreview):
    return {"amount":round(data.amount,2), "cadence":data.cadence, "status":"PREVIEW_ONLY",
      "bank_link_created":False,"transfer_scheduled":False}

def portal_html_path():
    bundle=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[1]))
    return bundle/"dashboard"/"portal_preview.html"

@app.get("/portal",response_class=HTMLResponse)
def portal_view():
    path=portal_html_path()
    if not path.is_file():raise HTTPException(503,"PORTAL assets missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))

@app.get("/",response_class=HTMLResponse)
def home():
    return '<meta http-equiv="refresh" content="0;url=/portal">'

