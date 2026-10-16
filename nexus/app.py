"""Nexus Operative development desktop API. Simulation only: no brokerage or money movement."""
import math
import sqlite3
import time
from pathlib import Path
import sys
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from nexus.state import load_settings, save_settings, audit
from nexus.markets import list_markets
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
from nexus.strategy_branches import available_branches
from nexus.observer import observer, history as observation_history
from nexus.tournament import tournament
from nexus.council import council
from nexus.evidence_auditor import audit_evidence
from nexus.latency import latency_observatory
from nexus.model_adapters import coordinate as coordinate_model_advisors
from nexus.safety_benchmark import benchmark as safety_benchmark
from nexus.portfolio_governor import audit_portfolio
from nexus.recovery import recovery_report, backup_snapshot
from nexus.paper_portfolio import initialize as init_paper_portfolio, state as paper_portfolio_state, buy as paper_buy, sell as paper_sell, reconcile as reconcile_paper

app = FastAPI(title="Nexus Operative", version="3.7.0")
_started = time.monotonic()
_paused = True
_halted = False
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

@app.get("/api/status")
def status():
    return {"version":"3.7.0", "mode":"SYNTHETIC_PAPER_ONLY", "portal_enabled":not _paused and not _halted,
      "halted":_halted, "broker_connected":False,"bank_connected":False,
      "live_orders_enabled":False, "money_movement_enabled":False, "ai_connected":False}

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
    try:
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

@app.get("/api/tiers")
def tiers():
    return {"data_mode":"ILLUSTRATIVE", "updated_at":int(time.time()), "tiers":simulated_snapshot()}

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
        observer.stop()
    return status()

@app.post("/api/simulate/halt")
def halt():
    global _halted,_paused
    _halted=True
    _paused=True
    observer.stop()
    audit('emergency_halt')
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
