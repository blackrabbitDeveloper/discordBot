"""Unified formatting functions for numbers, prices, dates, and volumes."""

from datetime import datetime


def fmt_price(n, decimals=2) -> str:
    """Format price with comma separators."""
    if n is None:
        return "N/A"
    return f"{n:,.{decimals}f}"


def fmt_number(n) -> str:
    """Format large numbers with US units ($T, $B, $M)."""
    if n is None:
        return "N/A"
    if abs(n) >= 1e12:
        return f"${n / 1e12:,.2f}T"
    if abs(n) >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:,.1f}M"
    return f"${n:,.0f}"


def fmt_number_kr(n) -> str:
    """Format large numbers with Korean units (억, 만)."""
    if n is None:
        return "N/A"
    if abs(n) >= 1_0000_0000:
        return f"{n / 1_0000_0000:,.1f}억"
    if abs(n) >= 1_0000:
        return f"{n / 1_0000:,.1f}만"
    return f"{n:,.0f}"


def fmt_date(d) -> str:
    """Format date to YYYY-MM-DD string."""
    if d is None:
        return "N/A"
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


def fmt_pct(n) -> str:
    """Format percentage with sign."""
    if n is None:
        return "N/A"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


def fmt_volume_kr(value: int) -> str:
    """Format Korean volume (억주, 만주)."""
    if value >= 1_0000_0000:
        return f"{value / 1_0000_0000:.1f}억주"
    elif value >= 1_0000:
        return f"{value / 1_0000:.0f}만주"
    return f"{value:,}주"


def fmt_volume_us(value: int) -> str:
    """Format US volume (B, M, K)."""
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    elif value >= 1e6:
        return f"{value / 1e6:.1f}M"
    elif value >= 1e3:
        return f"{value / 1e3:.0f}K"
    return f"{value:,}"


def fmt_market_cap(value: float) -> str:
    """Format market cap in USD ($T, $B, $M)."""
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    elif value >= 1e9:
        return f"${value / 1e9:.1f}B"
    elif value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def fmt_market_cap_kr(value: int) -> str:
    """Format Korean market cap (조, 억)."""
    if value >= 1_0000_0000_0000:
        return f"{value / 1_0000_0000_0000:.1f}조"
    elif value >= 1_0000_0000:
        return f"{value / 1_0000_0000:.0f}억"
    return f"{value:,}원"
