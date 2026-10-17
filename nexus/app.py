"""Nexus Operative development desktop API. Simulation only: no brokerage or money movement."""
import math
import asyncio
from contextlib import asynccontextmanager
import sqlite3
import time
from pathlib import Path
import sys
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from nexus.state import load_settings, save_settings, audit
from nexus.markets import list_markets
from nexus.market_universe import page as market_universe_page
from nexus.expansion_initiative import roadmap as expansion_roadmap, assess_evidence as assess_expansion_evidence
from nexus.durable_orders import gateway as research_order_gateway
from nexus.paper_forward import runner as paper_forward_runner
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
from nexus import credential_vault
from nexus.ai_evidence_preview import build as ai_evidence_preview, authorize_preview as ai_authorize_preview
from nexus.safety_benchmark import benchmark as safety_benchmark
from nexus.portfolio_governor import audit_portfolio
from nexus.recovery import recovery_report, backup_snapshot
from nexus.paper_portfolio import initialize as init_paper_portfolio, state as paper_portfolio_state, buy as paper_buy, sell as paper_sell, reconcile as reconcile_paper

@asynccontextmanager
async def application_lifespan(application):
    try:yield
    finally:
        await asyncio.to_thread(paper_forward_runner.stop,'APPLICATION_SHUTDOWN')
        await asyncio.to_thread(observer.stop)

app = FastAPI(title="Nexus Operative", version="0.61.0",lifespan=application_lifespan)
_started = time.monotonic()
_paused = True
try:
    _halted = research_order_gateway.recovery_report()["halted"]
except (ValueError,OSError,sqlite3.Error):
    _halted = True  # Recovery failure must not silently reopen intake.

_tiers = {
    "terrestrial": {"risk":"low", "max_weight":0.01, "score":28, "approach":"Prioritize liquid assets, modest position exposure and defined exits."},
    "solar": {"risk":"moderate", "max_weight":0.03, "score":38, "approach":"Research trend signals, confirmation and volatility before considering entries."},
    "universal": {"risk":"high", "max_weight":0.05, "score":19, "approach":"Stress-test correlated drawdowns and tail exposure before risking capital."},
}
class PortalCommand(BaseModel):
    enabled: bool

class PaperSettings(BaseModel):
    paper_budget: float = Field(gt=0, le=1000000)
    terrestrial_allocation: int = Field(ge=0, le=100)
    solar_allocation: int = Field(ge=0, le=100)
    universal_allocation: int = Field(ge=0, le=100)

@app.get("/api/paper/settings")
def paper_settings():
    return {"simulation_only": True, **load_settings()}

@app.put("/api/paper/settings")
def update_paper_settings(value: PaperSettings):
    try:
        settings = save_settings(value.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
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

@app.get("/api/onboarding/readiness")
def onboarding_readiness():
    return local_capability_inventory()

class VaultKeySubmission(BaseModel):
    api_key: str = Field(min_length=20,max_length=256)

def _require_local_portal(request:Request):
    from urllib.parse import urlsplit
    # Prevent cross-site form CSRF and off-machine browser requests.
    origin=request.headers.get("origin","")
    target=request.url
    expected=f"{target.scheme}://{target.netloc}"
    if origin!=expected or target.hostname not in ("localhost","127.0.0.1") or request.client is None or request.client.host not in ("127.0.0.1","::1"):
        raise HTTPException(status_code=403,detail="Use the local Nexus PORTAL to change connector settings")

@app.post("/api/paper/ai-provider/vault/connect")
def ai_vault_connect(data:VaultKeySubmission,request:Request):
    _require_local_portal(request)
    try:return credential_vault.install(data.api_key)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/ai-provider/vault/disconnect")
def ai_vault_disconnect(request:Request):
    _require_local_portal(request)
    try:return credential_vault.disconnect()
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

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
    return provider_research_status()

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
        raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/paper/replay/history")
def replay_history(limit: int = 20):
    try:
        return {"mode":"LOCAL_PAPER_JOURNAL","simulation_only":True,"runs":recent_replays(limit)}
    except ValueError as exc:
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

@app.get("/api/status")
def status():
    return {"version":"0.61.0", "mode":"SYNTHETIC_PAPER_ONLY", "portal_enabled":not _paused and not _halted,
      "halted":_halted, "broker_connected":False,"bank_connected":False,
      "live_orders_enabled":False, "money_movement_enabled":False, "ai_connected":False}

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
    except (OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=503,detail="Local paper snapshot unavailable")

@app.get("/api/paper/portfolio/integrity")
def paper_portfolio_integrity():
    return audit_portfolio()

@app.get("/api/paper/portfolio")
def paper_portfolio():
    return paper_portfolio_state()

@app.post("/api/paper/portfolio/init")
def paper_portfolio_init(data:PortfolioInit):
    try:return init_paper_portfolio(data.paper_capital)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))

@app.post("/api/paper/portfolio/buy")
def paper_portfolio_buy(data:PaperTrade):
    if _halted:raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
    if _paused:raise HTTPException(status_code=409,detail="PORTAL_PAUSED")
    try:
        require_tier_entry(data.tier,data.notional)
        quote=read_ticker(data.product)
        return paper_buy(data.product,data.notional,quote)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/paper/portfolio/sell")
def paper_portfolio_sell(data:PaperClose):
    try:
        quote=read_ticker(data.product)
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
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_SESSIONS_UNAVAILABLE")

@app.get("/api/paper/forward")
def paper_forward_status():
    try:return {**paper_forward_runner.status(),"history":paper_forward_runner.history()}
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_DIAGNOSTICS_UNAVAILABLE")

@app.post("/api/paper/forward/start")
def paper_forward_start(data:PaperForwardStart):
    if not data.confirm_experimental_paper:
        raise HTTPException(status_code=422,detail="EXPERIMENTAL_PAPER_CONFIRMATION_REQUIRED")
    if _paused or _halted:raise HTTPException(status_code=409,detail="ACTIVATE_UNHALTED_PORTAL_FIRST")
    try:return paper_forward_runner.start(data.tier,data.policy_id)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except (OSError,sqlite3.Error):raise HTTPException(status_code=503,detail="FORWARD_STORAGE_UNAVAILABLE")

@app.post("/api/paper/forward/stop")
def paper_forward_stop():
    return paper_forward_runner.stop()

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
    if not _halted or not _paused:
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
    if _halted:raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
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

@app.get("/api/matrix")
def matrix():
    return {
      "mode":"SYNTHETIC_TELEMETRY",
      "live_orders":False,
      "active":not _paused and not _halted,
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
    return tier_permissions_current()

@app.put("/api/tiers/{tier}/permission")
def set_tier_permission(tier:str,cmd:TierPermissionCommand):
    if _halted and cmd.enabled:raise HTTPException(status_code=409,detail="EMERGENCY_HALT_ACTIVE")
    try:return configure_tier_permission(tier,cmd.enabled)
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

@app.get("/api/tiers")
def tiers():
    return {"data_mode":"ILLUSTRATIVE", "updated_at":int(time.time()), "tiers":simulated_snapshot(),"tier_permissions":tier_permissions_current()["tiers"]}

@app.post("/api/portal")
def portal(cmd:PortalCommand):
    global _paused
    if _halted and cmd.enabled:
        raise HTTPException(409,"Emergency halt engaged; cannot activate.")
    _paused=not cmd.enabled
    audit('portal_resumed' if cmd.enabled else 'portal_paused')
    if cmd.enabled:
        observer.start()
    else:
        paper_forward_runner.stop('PORTAL_PAUSE')
        observer.stop()
    return status()

@app.post("/api/simulate/halt")
def halt():
    global _halted,_paused
    _halted=True
    _paused=True
    paper_forward_runner.stop('PORTAL_HALT_OR_RESET')
    observer.stop()
    try:research_order_gateway.emergency_halt()
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail="HALTED_LOCALLY_ORDER_STORAGE_UNAVAILABLE")
    audit('emergency_halt')
    return status()

class HaltResetConfirmation(BaseModel):
    confirm_reset: bool

@app.post("/api/simulate/halt/reset")
def reset_emergency_halt(confirmation:HaltResetConfirmation):
    global _halted,_paused
    if confirmation.confirm_reset is not True:
        raise HTTPException(status_code=422,detail="Explicit halt reset confirmation required")
    if not _halted:
        raise HTTPException(status_code=409,detail="Emergency halt is not active")
    # RESET clears only the simulation latch, never resumes background work.
    _paused=True
    paper_forward_runner.stop('PORTAL_HALT_OR_RESET')
    observer.stop()
    try:research_order_gateway.reset()
    except (ValueError,OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail="HALT_RESET_STORAGE_UNAVAILABLE")
    _halted=False
    audit("emergency_halt_reset_paused")
    return status()

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

