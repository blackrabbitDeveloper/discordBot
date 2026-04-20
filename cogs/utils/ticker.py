"""Ticker resolution, KRX loading, and autocomplete utilities."""

import csv
import os

import discord
import requests
import yfinance as yf
from discord import app_commands

from cogs.utils.constants import DATA_DIR, YAHOO_HEADERS

_krx_cache: list[dict] | None = None


def load_krx() -> list[dict]:
    """Load KRX stock list from local CSV."""
    global _krx_cache
    if _krx_cache is not None:
        return _krx_cache

    stocks = []
    csv_path = os.path.join(DATA_DIR, "krx_stocks.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                stocks.append({"name": row["name"], "symbol": row["symbol"]})
    except FileNotFoundError:
        pass

    _krx_cache = stocks
    return stocks


def has_korean(text: str) -> bool:
    """Check if text contains Korean characters."""
    return any("\uac00" <= c <= "\ud7a3" for c in text)


def search_krx(query: str) -> list[dict]:
    """Search KRX stocks by Korean name."""
    stocks = load_krx()
    return [s for s in stocks if query in s["name"]][:5]


def search_ticker(query: str) -> list[dict]:
    """Search tickers via Yahoo Finance API."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 5, "newsCount": 0},
            headers=YAHOO_HEADERS,
            timeout=5,
        )
        if r.status_code != 200:
            return []
        return [
            {"symbol": q["symbol"], "name": q.get("shortname", q["symbol"])}
            for q in r.json().get("quotes", [])
            if q.get("quoteType") == "EQUITY"
        ]
    except Exception:
        return []


def resolve_ticker(query: str) -> str | None:
    """Resolve ticker from Korean name, English name, or ticker symbol."""
    query = query.strip()

    if has_korean(query):
        results = search_krx(query)
        return results[0]["symbol"] if results else None

    upper = query.upper()
    if upper.replace(".", "").replace("-", "").isalnum():
        t = yf.Ticker(upper)
        if t.info.get("regularMarketPrice") is not None:
            return upper

    results = search_ticker(query)
    return results[0]["symbol"] if results else None


async def ticker_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for ticker parameter (Korean → KRX, English → Yahoo)."""
    if len(current) < 1:
        return []
    if has_korean(current):
        results = search_krx(current)
    else:
        results = search_ticker(current)
    return [
        app_commands.Choice(name=f"{r['name']} ({r['symbol']})", value=r["symbol"])
        for r in results[:25]
    ]
