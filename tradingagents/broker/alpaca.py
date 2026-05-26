"""
Alpaca paper/live trading execution layer.

Maps TradingAgents PortfolioDecision → Alpaca market order.
Defaults to paper trading. Set ALPACA_PAPER=false for live.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, TraderProposal

logger = logging.getLogger(__name__)

# Ratings that trigger a BUY order
BUY_RATINGS = {PortfolioRating.BUY, PortfolioRating.OVERWEIGHT}
# Ratings that trigger a SELL order (close existing position)
SELL_RATINGS = {PortfolioRating.SELL, PortfolioRating.UNDERWEIGHT}
# Default position size as % of buying power when sizing can't be parsed
DEFAULT_POSITION_PCT = 0.05


class AlpacaBroker:
    """Wraps Alpaca trading client for paper/live order execution."""

    def __init__(self, paper: bool = True):
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables must be set."
            )

        self.paper = paper
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.data_client = StockHistoricalDataClient(api_key, secret_key)
        mode = "PAPER" if paper else "LIVE"
        logger.info(f"AlpacaBroker initialized ({mode})")

    def get_account(self) -> dict:
        acct = self.client.get_account()
        return {
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "cash": float(acct.cash),
            "currency": acct.currency,
        }

    def get_position(self, symbol: str) -> Optional[dict]:
        try:
            pos = self.client.get_open_position(symbol.upper())
            return {
                "symbol": pos.symbol,
                "qty": float(pos.qty),
                "market_value": float(pos.market_value),
                "unrealized_pl": float(pos.unrealized_pl),
                "avg_entry_price": float(pos.avg_entry_price),
                "side": pos.side.value,
            }
        except Exception:
            return None

    def get_latest_price(self, symbol: str) -> float:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
        quotes = self.data_client.get_stock_latest_quote(req)
        quote = quotes[symbol.upper()]
        return float(quote.ask_price or quote.bid_price)

    def _parse_position_pct(self, sizing_str: Optional[str]) -> float:
        """Extract a percentage from sizing guidance like '5% of portfolio'."""
        if not sizing_str:
            return DEFAULT_POSITION_PCT
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", sizing_str)
        if match:
            return float(match.group(1)) / 100.0
        return DEFAULT_POSITION_PCT

    def execute_decision(
        self,
        symbol: str,
        portfolio_decision: PortfolioDecision,
        trader_proposal: Optional[TraderProposal] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Execute an Alpaca order based on a PortfolioDecision.

        Args:
            symbol: Ticker symbol (e.g. "NVDA")
            portfolio_decision: Final decision from Portfolio Manager
            trader_proposal: Optional Trader output for sizing guidance
            dry_run: If True, compute the order but don't submit it

        Returns:
            Result dict with order details or hold reason
        """
        rating = portfolio_decision.rating
        symbol = symbol.upper()

        result = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "rating": rating.value,
            "paper": self.paper,
            "dry_run": dry_run,
            "action": None,
            "order_id": None,
            "qty": None,
            "price": None,
            "error": None,
        }

        if rating in BUY_RATINGS:
            result["action"] = "BUY"
            try:
                acct = self.get_account()
                buying_power = acct["buying_power"]
                price = self.get_latest_price(symbol)

                sizing_str = trader_proposal.position_sizing if trader_proposal else None
                pct = self._parse_position_pct(sizing_str)
                dollar_amount = buying_power * pct
                qty = int(dollar_amount // price)

                if qty < 1:
                    result["action"] = "SKIPPED"
                    result["error"] = f"Calculated qty < 1 (${dollar_amount:.2f} at ${price:.2f})"
                    logger.warning(f"[{symbol}] Skipping BUY — insufficient buying power for 1 share")
                    return result

                result["qty"] = qty
                result["price"] = price
                logger.info(f"[{symbol}] BUY {qty} shares @ ~${price:.2f} ({pct*100:.1f}% of ${buying_power:.2f})")

                if not dry_run:
                    order_req = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY,
                    )
                    order = self.client.submit_order(order_req)
                    result["order_id"] = str(order.id)
                    logger.info(f"[{symbol}] Order submitted: {order.id}")

            except Exception as e:
                result["error"] = str(e)
                logger.error(f"[{symbol}] BUY failed: {e}")

        elif rating in SELL_RATINGS:
            result["action"] = "SELL"
            try:
                position = self.get_position(symbol)
                if not position:
                    result["action"] = "SKIPPED"
                    result["error"] = f"No open position in {symbol} to sell"
                    logger.info(f"[{symbol}] SELL skipped — no position held")
                    return result

                qty = int(position["qty"])
                result["qty"] = qty
                result["price"] = self.get_latest_price(symbol)
                logger.info(f"[{symbol}] SELL {qty} shares (close position)")

                if not dry_run:
                    order_req = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                    order = self.client.submit_order(order_req)
                    result["order_id"] = str(order.id)
                    logger.info(f"[{symbol}] Order submitted: {order.id}")

            except Exception as e:
                result["error"] = str(e)
                logger.error(f"[{symbol}] SELL failed: {e}")

        else:
            result["action"] = "HOLD"
            logger.info(f"[{symbol}] HOLD — no order submitted")

        return result
