"""Shared constants used across multiple cogs."""

import os
from datetime import timedelta, timezone

# Timezones
KST = timezone(timedelta(hours=9))

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

# HTTP Headers
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
SEC_HEADERS = {"User-Agent": "DiscordBot admin@example.com", "Accept": "application/json"}

# US Large-cap tickers (used by rank, fmp, etc.)
US_MAJOR_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "MA", "HD", "PG", "JNJ", "COST", "ABBV", "BAC",
    "CRM", "MRK", "AVGO", "KO", "PEP", "TMO", "WMT", "CSCO", "ACN",
    "MCD", "ABT", "LLY", "NEE", "LIN", "ADBE", "TXN", "PM", "NKE",
    "ORCL", "NFLX", "AMD", "INTC", "DIS", "QCOM", "LOW", "GS", "MS",
    "AXP", "CAT", "DE", "UPS", "PLTR",
]

# Sector ETFs (name, symbol, weight for heatmap)
SECTOR_ETFS = [
    ("기술", "XLK", 30),
    ("헬스케어", "XLV", 13),
    ("금융", "XLF", 13),
    ("소비재", "XLY", 10),
    ("통신", "XLC", 9),
    ("산업", "XLI", 8),
    ("필수소비재", "XLP", 6),
    ("에너지", "XLE", 4),
    ("유틸리티", "XLU", 3),
    ("부동산", "XLRE", 2),
    ("소재", "XLB", 2),
]

# Chart periods
PERIOD_LABELS = {
    "1mo": "1개월",
    "3mo": "3개월",
    "6mo": "6개월",
    "1y": "1년",
}
