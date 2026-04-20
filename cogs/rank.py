import asyncio
from datetime import datetime, timedelta, timezone

import discord
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 미국 대형주 후보 (시가총액 상위 가능성 높은 종목들)
US_LARGE_CAPS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "MA", "HD", "PG", "JNJ", "COST", "ABBV", "BAC",
    "CRM", "MRK", "AVGO", "KO", "PEP", "TMO", "WMT", "CSCO", "ACN",
    "MCD", "ABT", "LLY", "NEE", "LIN", "ADBE", "TXN", "PM", "NKE",
    "ORCL", "NFLX", "AMD", "INTC", "DIS", "QCOM", "LOW", "GS", "MS",
    "AXP", "CAT", "DE", "UPS", "PLTR",
]

# --- 네이버 KR 데이터 ---

_NAVER_ENDPOINTS = {
    "market_cap": "https://m.stock.naver.com/api/stocks/marketValue/KOSPI",
    "gainers": "https://m.stock.naver.com/api/stocks/up/KOSPI",
    "losers": "https://m.stock.naver.com/api/stocks/down/KOSPI",
    "volume": "https://m.stock.naver.com/api/stocks/volume/KOSPI",
}


def _parse_naver_stock(s: dict) -> dict:
    """네이버 종목 데이터 파싱."""
    pct = float(s.get("fluctuationsRatio", 0))
    direction = s.get("compareToPreviousPrice", {}).get("name", "")
    if direction in ("FALLING", "LOWER_LIMIT"):
        pct = -abs(pct)
    mcap_raw = s.get("marketValue") or s.get("marketCap", "0")
    try:
        mcap = int(str(mcap_raw).replace(",", ""))
    except (ValueError, TypeError):
        mcap = 0
    volume_raw = s.get("accumulatedTradingVolume", "0")
    try:
        volume = int(str(volume_raw).replace(",", ""))
    except (ValueError, TypeError):
        volume = 0
    return {
        "name": s.get("stockName", ""),
        "symbol": s.get("itemCode", ""),
        "market_cap": mcap,
        "change_pct": pct,
        "volume": volume,
    }


def _fetch_kr_ranking(criteria: str, count: int = 20) -> list[dict]:
    """네이버에서 코스피 종목 순위 조회."""
    url = _NAVER_ENDPOINTS.get(criteria)
    if not url:
        return []
    try:
        r = requests.get(url, headers=NAVER_HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        stocks = data.get("stocks", data) if isinstance(data, dict) else data
        return [_parse_naver_stock(s) for s in stocks[:count]]
    except Exception:
        return []


# --- 미국 데이터 ---

def _fetch_us_market_cap(count: int = 20) -> list[dict]:
    """yfinance로 미국 시가총액 상위 종목 조회."""
    results = []
    for symbol in US_LARGE_CAPS:
        try:
            t = yf.Ticker(symbol)
            info = t.fast_info
            mcap = info.get("marketCap") or info.get("market_cap")
            if not mcap:
                mcap = t.info.get("marketCap")
            if mcap:
                price = info.get("lastPrice") or info.get("last_price")
                prev = info.get("previousClose") or info.get("previous_close")
                change_pct = ((price - prev) / prev * 100) if price and prev else 0.0
                results.append({
                    "symbol": symbol,
                    "market_cap": mcap,
                    "change_pct": change_pct,
                })
        except Exception:
            continue
    results.sort(key=lambda x: x["market_cap"], reverse=True)
    return results[:count]


def _fetch_us_movers(criteria: str, count: int = 20) -> list[dict]:
    """Yahoo Finance screener로 미국 급등/급락/거래량 조회."""
    scr_ids = {
        "gainers": "day_gainers",
        "losers": "day_losers",
        "volume": "most_actives",
    }
    scr_id = scr_ids.get(criteria)
    if not scr_id:
        return []
    try:
        url = (
            f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
            f"?scrIds={scr_id}&count={count}"
        )
        r = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        quotes = (
            data.get("finance", {})
            .get("result", [{}])[0]
            .get("quotes", [])
        )
        results = []
        for q in quotes[:count]:
            results.append({
                "symbol": q.get("symbol", ""),
                "name": q.get("shortName", ""),
                "market_cap": q.get("marketCap", 0),
                "change_pct": q.get("regularMarketChangePercent", 0.0),
                "volume": q.get("regularMarketVolume", 0),
            })
        return results
    except Exception:
        return []


# --- 포맷 유틸 ---

def _format_cap(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    elif value >= 1e9:
        return f"${value / 1e9:.1f}B"
    elif value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def _format_kr_cap(value: int) -> str:
    if value >= 1_0000_0000_0000:
        return f"{value / 1_0000_0000_0000:.1f}조"
    elif value >= 1_0000_0000:
        return f"{value / 1_0000_0000:.0f}억"
    return f"{value:,}원"


def _format_volume(value: int) -> str:
    if value >= 1_0000_0000:
        return f"{value / 1_0000_0000:.1f}억주"
    elif value >= 1_0000:
        return f"{value / 1_0000:.0f}만주"
    return f"{value:,}주"


def _format_us_volume(value: int) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    elif value >= 1e6:
        return f"{value / 1e6:.1f}M"
    elif value >= 1e3:
        return f"{value / 1e3:.0f}K"
    return f"{value:,}"


# --- Cog ---

CRITERIA_TITLES = {
    "market_cap": ("시가총액", "시가총액 기준"),
    "gainers": ("급상승", "등락률 상위"),
    "losers": ("급하락", "등락률 하위"),
    "volume": ("거래량", "거래량 상위"),
}

CRITERIA_EMOJI = {
    "market_cap": "💰",
    "gainers": "🚀",
    "losers": "📉",
    "volume": "🔥",
}


class Rank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rank", description="종목 순위를 조회합니다 (시가총액/급상승/급하락/거래량)")
    @app_commands.describe(
        market="시장 선택",
        criteria="순위 기준",
        count="표시할 종목 수 (기본 20, 최대 30)",
    )
    @app_commands.choices(
        market=[
            app_commands.Choice(name="🇺🇸 미국", value="us"),
            app_commands.Choice(name="🇰🇷 한국", value="kr"),
        ],
        criteria=[
            app_commands.Choice(name="💰 시가총액", value="market_cap"),
            app_commands.Choice(name="🚀 급상승", value="gainers"),
            app_commands.Choice(name="📉 급하락", value="losers"),
            app_commands.Choice(name="🔥 거래량", value="volume"),
        ],
    )
    async def rank(
        self,
        interaction: discord.Interaction,
        market: app_commands.Choice[str],
        criteria: app_commands.Choice[str] = None,
        count: int = 20,
    ):
        count = max(1, min(count, 30))
        crit = criteria.value if criteria else "market_cap"
        await interaction.response.defer()

        market_flag = "🇺🇸" if market.value == "us" else "🇰🇷"
        market_name = "미국" if market.value == "us" else "한국"
        crit_title, crit_desc = CRITERIA_TITLES[crit]
        emoji = CRITERIA_EMOJI[crit]

        # 데이터 조회
        if market.value == "us":
            if crit == "market_cap":
                data = await asyncio.to_thread(_fetch_us_market_cap, count)
            else:
                data = await asyncio.to_thread(_fetch_us_movers, crit, count)
        else:
            data = await asyncio.to_thread(_fetch_kr_ranking, crit, count)

        if not data:
            await interaction.followup.send(f"❌ {market_name} {crit_title} 데이터를 가져올 수 없습니다.")
            return

        # 포맷팅
        lines = []
        for i, item in enumerate(data, 1):
            sign = "🔴" if item["change_pct"] < 0 else "🟢"
            pct = f"{item['change_pct']:+.2f}%"

            if market.value == "us":
                name = item.get("symbol", "")
                if crit == "volume":
                    extra = _format_us_volume(item.get("volume", 0))
                    lines.append(f"`{i:>2}.` {sign} **{name}** — {extra} ({pct})")
                else:
                    cap = _format_cap(item.get("market_cap", 0))
                    lines.append(f"`{i:>2}.` {sign} **{name}** — {cap} ({pct})")
            else:
                name = item.get("name", "")
                if crit == "volume":
                    extra = _format_volume(item.get("volume", 0))
                    lines.append(f"`{i:>2}.` {sign} **{name}** — {extra} ({pct})")
                elif crit == "market_cap":
                    cap = _format_kr_cap(item["market_cap"]) if item.get("market_cap") else ""
                    lines.append(f"`{i:>2}.` {sign} **{name}** — {cap} ({pct})" if cap else f"`{i:>2}.` {sign} **{name}** ({pct})")
                else:
                    lines.append(f"`{i:>2}.` {sign} **{name}** ({pct})")

        title = f"{market_flag} {market_name} {crit_title} Top {len(data)} {emoji}"
        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=0x2F3136,
            timestamp=datetime.now(KST),
        )
        embed.set_footer(text=crit_desc)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rank(bot))
