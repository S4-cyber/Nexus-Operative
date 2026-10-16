"""Nexus Operative development desktop API. Simulation only: no brokerage or money movement."""
import math
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

app = FastAPI(title="Nexus Operative", version="1.9.0")
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
    return {"version":"1.9.0", "mode":"SYNTHETIC_PAPER_ONLY", "portal_enabled":not _paused and not _halted,
      "halted":_halted, "broker_connected":False,"bank_connected":False,
      "live_orders_enabled":False, "money_movement_enabled":False, "ai_connected":False}

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
    return status()

@app.post("/api/simulate/halt")
def halt():
    global _halted,_paused
    _halted=True
    _paused=True
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
