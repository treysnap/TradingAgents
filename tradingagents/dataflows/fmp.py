"""
Financial Modeling Prep (FMP) data provider
Provides fundamental financial data via FMP API
"""

import os
import requests
import pandas as pd
from typing import Annotated
from datetime import datetime
import time

# FMP API configuration
FMP_BASE_URL = "https://financialmodelingprep.com/stable"


def _get_api_key() -> str:
    key = os.getenv("FMP_API_KEY")
    if not key:
        raise ValueError(
            "FMP_API_KEY environment variable not set. "
            "Set it with: [Environment]::SetEnvironmentVariable('FMP_API_KEY', 'your-key', 'User')"
        )
    return key


def _fmp_request(endpoint: str, params: dict = None) -> dict:
    """
    Make a request to FMP API with retry logic and error handling.

    Args:
        endpoint: API endpoint (e.g., "/profile/AAPL")
        params: Optional query parameters

    Returns:
        JSON response from FMP
    """
    if params is None:
        params = {}

    # Add API key to params
    params["apikey"] = _get_api_key()

    url = f"{FMP_BASE_URL}/{endpoint.lstrip('/')}"

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        raise Exception(f"FMP API timeout for {endpoint}")
    except requests.exceptions.HTTPError as e:
        if response.status_code == 429:
            # Rate limit - wait and retry
            time.sleep(2)
            response = requests.get(url, params=params, timeout=10)
            return response.json()
        raise Exception(f"FMP API error {response.status_code}: {response.text}")
    except Exception as e:
        raise Exception(f"FMP request failed for {endpoint}: {str(e)}")


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used for FMP)"] = None
) -> str:
    """Get company fundamentals overview from FMP."""
    try:
        data = _fmp_request("/profile", {"symbol": ticker.upper()})

        if not data or (isinstance(data, list) and len(data) == 0):
            return f"No fundamentals data found for symbol '{ticker}'"

        # Handle both single object and list responses
        if isinstance(data, list):
            info = data[0] if data else {}
        else:
            info = data

        if not info:
            return f"No fundamentals data found for symbol '{ticker}'"

        fields = [
            ("Name", info.get("companyName")),
            ("Symbol", info.get("symbol")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Website", info.get("website")),
            ("Description", info.get("description")),
            ("CEO", info.get("ceo")),
            ("Market Cap", info.get("mktCap")),
            ("Employee Count", info.get("employees")),
            ("Price", info.get("price")),
            ("52 Week High", info.get("52WeekHigh")),
            ("52 Week Low", info.get("52WeekLow")),
            ("PE Ratio", info.get("pe")),
            ("PEG Ratio", info.get("pegRatio")),
            ("Price to Book", info.get("pb")),
            ("EPS TTM", info.get("epsTtm")),
            ("Dividend Yield", info.get("dividendYield")),
            ("Beta", info.get("beta")),
            ("Debt to Equity", info.get("debtToEquity")),
            ("Current Ratio", info.get("currentRatio")),
            ("Quick Ratio", info.get("quickRatio")),
            ("ROE", info.get("roe")),
            ("ROA", info.get("roa")),
            ("ROIC", info.get("roic")),
            ("Gross Margin", info.get("grossMargin")),
            ("Operating Margin", info.get("operatingMargin")),
            ("Net Margin", info.get("netMargin")),
            ("Asset Turnover", info.get("assetTurnover")),
            ("Free Cash Flow", info.get("freeCashflow")),
            ("Operating Cash Flow", info.get("operatingCashflow")),
        ]

        lines = []
        for label, value in fields:
            if value is not None and value != "":
                lines.append(f"{label}: {value}")

        header = f"# Company Fundamentals for {ticker.upper()}\n"
        header += f"# Data retrieved from FMP on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get balance sheet data from FMP."""
    try:
        params = {"symbol": ticker.upper()}
        params["period"] = "annual" if freq.lower() == "annual" else "quarterly"
        data = _fmp_request("/balance-sheet-statement", params)

        if not data or (isinstance(data, list) and len(data) == 0):
            return f"No balance sheet data found for symbol '{ticker}'"

        # Convert to DataFrame
        df = pd.DataFrame(data)

        if df.empty:
            return f"No balance sheet data found for symbol '{ticker}'"

        # Select key columns if they exist
        key_cols = ["date", "symbol", "totalAssets", "totalLiabilities", "totalEquity",
                   "totalCurrentAssets", "totalCurrentLiabilities", "cash", "inventory",
                   "goodwill", "intangibleAssets", "longTermDebt", "shortTermDebt"]

        available_cols = [col for col in key_cols if col in df.columns]
        if available_cols:
            df = df[available_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Balance Sheet data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved from FMP on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get cash flow statement data from FMP."""
    try:
        params = {"symbol": ticker.upper()}
        params["period"] = "annual" if freq.lower() == "annual" else "quarterly"
        data = _fmp_request("/cash-flow-statement", params)

        if not data or (isinstance(data, list) and len(data) == 0):
            return f"No cash flow data found for symbol '{ticker}'"

        df = pd.DataFrame(data)

        if df.empty:
            return f"No cash flow data found for symbol '{ticker}'"

        # Select key columns
        key_cols = ["date", "symbol", "operatingCashFlow", "investingCashFlow",
                   "financingCashFlow", "netChangeInCash", "capitalExpenditure",
                   "freeCashFlow", "depreciationAndAmortization"]

        available_cols = [col for col in key_cols if col in df.columns]
        if available_cols:
            df = df[available_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Cash Flow Statement data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved from FMP on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get income statement data from FMP."""
    try:
        params = {"symbol": ticker.upper()}
        params["period"] = "annual" if freq.lower() == "annual" else "quarterly"
        data = _fmp_request("/income-statement", params)

        if not data or (isinstance(data, list) and len(data) == 0):
            return f"No income statement data found for symbol '{ticker}'"

        df = pd.DataFrame(data)

        if df.empty:
            return f"No income statement data found for symbol '{ticker}'"

        # Select key columns
        key_cols = ["date", "symbol", "revenue", "costOfRevenue", "grossProfit",
                   "operatingExpenses", "operatingIncome", "interestExpense",
                   "incomeTaxExpense", "netIncome", "eps", "epsDiluted",
                   "researchAndDevelopment", "sellingGeneralAndAdministrative"]

        available_cols = [col for col in key_cols if col in df.columns]
        if available_cols:
            df = df[available_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Income Statement data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved from FMP on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"]
) -> str:
    """Get insider transactions data from FMP."""
    try:
        data = _fmp_request("/insider-trading", {"symbol": ticker.upper()})

        if not data or (isinstance(data, list) and len(data) == 0):
            return f"No insider transactions data found for symbol '{ticker}'"

        df = pd.DataFrame(data)

        if df.empty:
            return f"No insider transactions data found for symbol '{ticker}'"

        csv_string = df.to_csv(index=False)

        header = f"# Insider Transactions data for {ticker.upper()}\n"
        header += f"# Data retrieved from FMP on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"
