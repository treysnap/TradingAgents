"""
TradingAgents FastAPI service.

Wraps the multi-agent pipeline and Alpaca execution behind HTTP endpoints
so the Dashboard Trade Assist Node.js backend can call it.

Endpoints:
  POST /analyze          — start an analysis job, returns { job_id }
  GET  /status/{job_id} — poll job status + result
  GET  /positions        — current Alpaca paper positions
  GET  /logs             — recent trade log entries
  GET  /health           — health check

Start:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

import json
import os
import uuid
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradingagents.api")

app = FastAPI(title="TradingAgents API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your Dashboard domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store — sufficient for personal/single-user use
jobs: dict[str, dict] = {}

# Thread pool for running the blocking TradingAgents pipeline
executor = ThreadPoolExecutor(max_workers=2)

LOG_DIR = Path(__file__).parent / "trade_logs"
LOG_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    symbol: str
    date: Optional[str] = None   # defaults to today
    dry_run: bool = True          # safe default — must opt in to real orders


class JobStatus(BaseModel):
    job_id: str
    status: str                   # "pending" | "running" | "complete" | "failed"
    symbol: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Core analysis runner (runs in thread pool, not async)
# ---------------------------------------------------------------------------

def _run_analysis(job_id: str, symbol: str, trade_date: str, dry_run: bool) -> None:
    jobs[job_id]["status"] = "running"
    symbol = symbol.upper()

    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
        from tradingagents.broker import AlpacaBroker

        config = {
            **DEFAULT_CONFIG,
            "llm_provider": "anthropic",
            "deep_think_llm": "claude-opus-4-7",
            "quick_think_llm": "claude-haiku-4-5-20251001",
        }

        logger.info(f"[{job_id}] Starting pipeline for {symbol} on {trade_date}")
        ta = TradingAgentsGraph(config=config)
        final_state, decision_str = ta.propagate(symbol, trade_date)
        logger.info(f"[{job_id}] Pipeline complete. Decision: {decision_str}")

        portfolio_decision = final_state.get("portfolio_decision_obj")
        trader_proposal = final_state.get("trader_proposal_obj")

        # Fallback: parse rating from string if structured object not in state
        if portfolio_decision is None:
            rating_map = {
                "buy": PortfolioRating.BUY,
                "overweight": PortfolioRating.OVERWEIGHT,
                "hold": PortfolioRating.HOLD,
                "underweight": PortfolioRating.UNDERWEIGHT,
                "sell": PortfolioRating.SELL,
            }
            rating = rating_map.get((decision_str or "hold").lower(), PortfolioRating.HOLD)
            portfolio_decision = PortfolioDecision(
                rating=rating,
                executive_summary=final_state.get("final_trade_decision", ""),
                investment_thesis="",
            )

        broker = AlpacaBroker(paper=True)
        acct = broker.get_account()
        order_result = broker.execute_decision(
            symbol=symbol,
            portfolio_decision=portfolio_decision,
            trader_proposal=trader_proposal,
            dry_run=dry_run,
        )

        result = {
            "symbol": symbol,
            "date": trade_date,
            "decision": decision_str,
            "account": acct,
            "order": order_result,
            "trader_proposal": {
                "action": trader_proposal.action.value if trader_proposal else None,
                "entry_price": trader_proposal.entry_price if trader_proposal else None,
                "stop_loss": trader_proposal.stop_loss if trader_proposal else None,
                "position_sizing": trader_proposal.position_sizing if trader_proposal else None,
            } if trader_proposal else None,
            "portfolio_decision": {
                "rating": portfolio_decision.rating.value,
                "executive_summary": portfolio_decision.executive_summary,
                "price_target": portfolio_decision.price_target,
                "time_horizon": portfolio_decision.time_horizon,
            },
            "analysis": {
                "market_report": final_state.get("market_report", ""),
                "sentiment_report": final_state.get("sentiment_report", ""),
                "news_report": final_state.get("news_report", ""),
                "fundamentals_report": final_state.get("fundamentals_report", ""),
                "final_trade_decision": final_state.get("final_trade_decision", ""),
            },
        }

        # Save log
        log_file = LOG_DIR / f"{symbol}_{trade_date}_{datetime.now().strftime('%H%M%S')}.json"
        log_file.write_text(json.dumps(result, indent=2))

        jobs[job_id]["status"] = "complete"
        jobs[job_id]["result"] = result
        jobs[job_id]["completed_at"] = datetime.now().isoformat()
        logger.info(f"[{job_id}] Done. Log: {log_file}")

    except Exception as e:
        logger.error(f"[{job_id}] Failed: {e}", exc_info=True)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["completed_at"] = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/analyze", response_model=JobStatus)
async def analyze(req: AnalyzeRequest):
    trade_date = req.date or datetime.now().strftime("%Y-%m-%d")
    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "symbol": req.symbol.upper(),
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, _run_analysis, job_id, req.symbol, trade_date, req.dry_run)

    logger.info(f"[{job_id}] Job queued for {req.symbol.upper()} on {trade_date} (dry_run={req.dry_run})")
    return jobs[job_id]


@app.get("/status/{job_id}", response_model=JobStatus)
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/positions")
def positions():
    try:
        from tradingagents.broker import AlpacaBroker
        broker = AlpacaBroker(paper=True)
        raw = broker.client.get_all_positions()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "avg_entry_price": float(p.avg_entry_price),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "side": p.side.value,
                }
                for p in raw
            ],
            "account": broker.get_account(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs")
def logs(limit: int = 20):
    try:
        files = sorted(LOG_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        entries = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text())
                entries.append({
                    "file": f.name,
                    "symbol": data.get("symbol"),
                    "date": data.get("date"),
                    "decision": data.get("decision"),
                    "action": data.get("order", {}).get("action"),
                    "qty": data.get("order", {}).get("qty"),
                    "price": data.get("order", {}).get("price"),
                    "order_id": data.get("order", {}).get("order_id"),
                    "dry_run": data.get("order", {}).get("dry_run"),
                })
            except Exception:
                continue
        return {"logs": entries, "total": len(files)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
