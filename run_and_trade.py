"""
run_and_trade.py — Full TradingAgents pipeline with Alpaca execution.

Usage:
    python run_and_trade.py NVDA
    python run_and_trade.py NVDA --dry-run
    python run_and_trade.py NVDA --date 2026-05-25
    python run_and_trade.py NVDA --live   # REAL money — use with caution
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_and_trade")

LOG_DIR = Path(__file__).parent / "trade_logs"
LOG_DIR.mkdir(exist_ok=True)


def parse_args():
    p = argparse.ArgumentParser(description="TradingAgents + Alpaca paper trading runner")
    p.add_argument("symbol", help="Ticker symbol, e.g. NVDA")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Analysis date (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true", help="Analyse but don't submit orders")
    p.add_argument("--live", action="store_true", help="Use live trading account (real money!)")
    return p.parse_args()


def main():
    args = parse_args()
    symbol = args.symbol.upper()
    trade_date = args.date
    dry_run = args.dry_run
    paper = not args.live

    if not paper:
        confirm = input(f"\n*** LIVE TRADING MODE — real money will be used for {symbol}. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    mode_tag = "DRY-RUN" if dry_run else ("PAPER" if paper else "LIVE")
    logger.info(f"Starting analysis: {symbol} on {trade_date} [{mode_tag}]")

    # --- Step 1: Run TradingAgents pipeline ---
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = {
        **DEFAULT_CONFIG,
        "llm_provider": "anthropic",
        "deep_think_llm": "claude-opus-4-7",
        "quick_think_llm": "claude-haiku-4-5-20251001",
    }

    logger.info("Initializing TradingAgents pipeline...")
    ta = TradingAgentsGraph(config=config)
    logger.info(f"Running multi-agent analysis for {symbol}...")
    final_state, decision_str = ta.propagate(symbol, trade_date)

    logger.info(f"Pipeline complete. Decision: {decision_str}")

    # Extract structured objects from final state
    portfolio_decision = final_state.get("portfolio_decision_obj")
    trader_proposal = final_state.get("trader_proposal_obj")

    # Fallback: if structured objects aren't in state, parse rating from string
    if portfolio_decision is None:
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
        rating_map = {
            "buy": PortfolioRating.BUY,
            "overweight": PortfolioRating.OVERWEIGHT,
            "hold": PortfolioRating.HOLD,
            "underweight": PortfolioRating.UNDERWEIGHT,
            "sell": PortfolioRating.SELL,
        }
        rating_str = decision_str.lower().strip() if decision_str else "hold"
        rating = rating_map.get(rating_str, PortfolioRating.HOLD)
        portfolio_decision = PortfolioDecision(
            rating=rating,
            executive_summary=final_state.get("final_trade_decision", ""),
            investment_thesis="",
        )

    # --- Step 2: Execute via Alpaca ---
    from tradingagents.broker import AlpacaBroker

    broker = AlpacaBroker(paper=paper)
    acct = broker.get_account()
    logger.info(f"Account — Portfolio: ${acct['portfolio_value']:,.2f} | Buying Power: ${acct['buying_power']:,.2f}")

    order_result = broker.execute_decision(
        symbol=symbol,
        portfolio_decision=portfolio_decision,
        trader_proposal=trader_proposal,
        dry_run=dry_run,
    )

    # --- Step 3: Log results ---
    log_entry = {
        "symbol": symbol,
        "date": trade_date,
        "mode": mode_tag,
        "decision": decision_str,
        "account_snapshot": acct,
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
    }

    log_file = LOG_DIR / f"{symbol}_{trade_date}_{datetime.now().strftime('%H%M%S')}.json"
    log_file.write_text(json.dumps(log_entry, indent=2))
    logger.info(f"Log saved: {log_file}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print(f"  {symbol} | {trade_date} | {mode_tag}")
    print("=" * 60)
    print(f"  Decision:   {decision_str}")
    print(f"  Action:     {order_result['action']}")
    if order_result["qty"]:
        print(f"  Qty:        {order_result['qty']} shares @ ~${order_result['price']:.2f}")
    if order_result["order_id"]:
        print(f"  Order ID:   {order_result['order_id']}")
    if order_result["error"]:
        print(f"  Note:       {order_result['error']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
