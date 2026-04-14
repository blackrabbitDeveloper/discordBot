import asyncio
import os
import csv
from datetime import datetime, timedelta, timezone

import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_krx_cache: list[dict] | None = None


def _load_krx() -> list[dict]:
    global _krx_cache
    if _krx_cache is not None:
        return _krx_cache
    stocks = []
    csv_path = os.path.join(_DATA_DIR, "krx_stocks.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                stocks.append({"name": row["name"], "symbol": row["symbol"]})
    except FileNotFoundError:
        pass
    _krx_cache = stocks
    return stocks


def _has_korean(text: str) -> bool:
    return any("\uac00" <= c <= "\ud7a3" for c in text)


def _resolve_ticker(query: str) -> str | None:
    query = query.strip()
    if _has_korean(query):
        stocks = _load_krx()
        for s in stocks:
            if query in s["name"]:
                return s["symbol"]
        return None
    upper = query.upper()
    if upper.replace(".", "").replace("-", "").isalnum():
        t = yf.Ticker(upper)
        if t.info.get("regularMarketPrice") is not None:
            return upper
    return None


async def _ticker_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if len(current) < 1:
        return []
    if _has_korean(current):
        stocks = _load_krx()
        matches = [s for s in stocks if current in s["name"]][:10]
        return [
            app_commands.Choice(name=f"{s['name']} ({s['symbol']})", value=s["symbol"])
            for s in matches
        ]
    return []


def _fmt_price(n, decimals=2) -> str:
    if n is None:
        return "N/A"
    return f"{n:,.{decimals}f}"


def _fmt_number(n) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1e12:
        return f"${n / 1e12:,.2f}T"
    if abs(n) >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:,.1f}M"
    return f"${n:,.0f}"


def _fmt_date(d) -> str:
    if d is None:
        return "N/A"
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return str(d)


def _fetch_dividend(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("regularMarketPrice") is None:
            return None
        return {
            "name": info.get("shortName", ticker),
            "price": info.get("regularMarketPrice"),
            "dividend_rate": info.get("dividendRate"),
            "dividend_yield": info.get("dividendYield"),
            "ex_date": info.get("exDividendDate"),
            "payout_ratio": info.get("payoutRatio"),
            "trailing_rate": info.get("trailingAnnualDividendRate"),
            "trailing_yield": info.get("trailingAnnualDividendYield"),
            "five_year_avg_yield": info.get("fiveYearAvgDividendYield"),
        }
    except Exception:
        return None


def _fetch_earnings(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("regularMarketPrice") is None:
            return None
        cal = t.calendar
        return {
            "name": info.get("shortName", ticker),
            "earnings_date": cal.get("Earnings Date"),
            "earnings_avg": cal.get("Earnings Average"),
            "earnings_high": cal.get("Earnings High"),
            "earnings_low": cal.get("Earnings Low"),
            "revenue_avg": cal.get("Revenue Average"),
            "revenue_high": cal.get("Revenue High"),
            "revenue_low": cal.get("Revenue Low"),
            "ex_dividend_date": cal.get("Ex-Dividend Date"),
            "dividend_date": cal.get("Dividend Date"),
            "trailing_eps": info.get("trailingEps"),
            "forward_eps": info.get("forwardEps"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
        }
    except Exception:
        return None


# --- 섹터 ---

SECTOR_ETFS = [
    ("기술", "XLK"),
    ("헬스케어", "XLV"),
    ("금융", "XLF"),
    ("소비재", "XLY"),
    ("산업", "XLI"),
    ("통신", "XLC"),
    ("에너지", "XLE"),
    ("필수소비재", "XLP"),
    ("유틸리티", "XLU"),
    ("부동산", "XLRE"),
    ("소재", "XLB"),
]


def _fetch_sector_performance() -> list[dict]:
    results = []
    for name, symbol in SECTOR_ETFS:
        try:
            t = yf.Ticker(symbol)
            info = t.info
            price = info.get("regularMarketPrice")
            prev = info.get("regularMarketPreviousClose", price)
            if price and prev:
                change_pct = (price - prev) / prev * 100
                results.append({"name": name, "symbol": symbol, "change_pct": change_pct})
        except Exception:
            continue
    results.sort(key=lambda x: x["change_pct"], reverse=True)
    return results


class Fundamental(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dividend", description="종목의 배당 정보를 조회합니다")
    @app_commands.describe(ticker="종목코드 또는 회사명")
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def dividend(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = _resolve_ticker(ticker) if _has_korean(ticker) else ticker.upper()
        if resolved is None:
            await interaction.followup.send(f"`{ticker}` 종목을 찾을 수 없습니다.", ephemeral=True)
            return

        data = await asyncio.to_thread(_fetch_dividend, resolved)
        if data is None:
            await interaction.followup.send(f"`{resolved}` 정보를 가져올 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"💰 {data['name']} 배당 정보",
            color=discord.Color.gold(),
            timestamp=datetime.now(KST),
        )

        if data["dividend_rate"]:
            embed.add_field(name="연간 배당금", value=_fmt_price(data["dividend_rate"]), inline=True)
        if data["dividend_yield"]:
            embed.add_field(name="배당 수익률", value=f"{data['dividend_yield']:.2f}%", inline=True)
        if data["payout_ratio"]:
            embed.add_field(name="배당 성향", value=f"{data['payout_ratio'] * 100:.1f}%", inline=True)
        if data["ex_date"]:
            ex_dt = datetime.fromtimestamp(data["ex_date"], tz=KST)
            embed.add_field(name="배당락일", value=ex_dt.strftime("%Y-%m-%d"), inline=True)
        if data["trailing_rate"]:
            embed.add_field(name="지난 12개월 배당", value=_fmt_price(data["trailing_rate"]), inline=True)
        if data["five_year_avg_yield"]:
            embed.add_field(name="5년 평균 수익률", value=f"{data['five_year_avg_yield']:.2f}%", inline=True)

        if not any([data["dividend_rate"], data["dividend_yield"]]):
            embed.description = "배당 데이터가 없습니다 (무배당 종목일 수 있습니다)"

        embed.set_footer(text="Yahoo Finance")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="earnings", description="종목의 실적 발표 일정과 추정치를 조회합니다")
    @app_commands.describe(ticker="종목코드 또는 회사명")
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def earnings(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = _resolve_ticker(ticker) if _has_korean(ticker) else ticker.upper()
        if resolved is None:
            await interaction.followup.send(f"`{ticker}` 종목을 찾을 수 없습니다.", ephemeral=True)
            return

        data = await asyncio.to_thread(_fetch_earnings, resolved)
        if data is None:
            await interaction.followup.send(f"`{resolved}` 정보를 가져올 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📅 {data['name']} 실적 일정",
            color=discord.Color.purple(),
            timestamp=datetime.now(KST),
        )

        # 실적 발표일
        dates = data.get("earnings_date")
        if dates:
            date_str = " ~ ".join(_fmt_date(d) for d in dates)
            embed.add_field(name="실적 발표일", value=date_str, inline=False)

        # EPS 추정
        eps_lines = []
        if data["earnings_avg"]:
            eps_lines.append(f"**컨센서스**: ${data['earnings_avg']:.2f}")
        if data["earnings_high"] and data["earnings_low"]:
            eps_lines.append(f"**범위**: ${data['earnings_low']:.2f} ~ ${data['earnings_high']:.2f}")
        if data["trailing_eps"]:
            eps_lines.append(f"**지난 12개월 EPS**: ${data['trailing_eps']:.2f}")
        if data["forward_eps"]:
            eps_lines.append(f"**예상 EPS**: ${data['forward_eps']:.2f}")
        if eps_lines:
            embed.add_field(name="📊 EPS", value="\n".join(eps_lines), inline=True)

        # 매출 추정
        rev_lines = []
        if data["revenue_avg"]:
            rev_lines.append(f"**컨센서스**: {_fmt_number(data['revenue_avg'])}")
        if data["revenue_high"] and data["revenue_low"]:
            rev_lines.append(f"**범위**: {_fmt_number(data['revenue_low'])} ~ {_fmt_number(data['revenue_high'])}")
        if rev_lines:
            embed.add_field(name="💵 매출", value="\n".join(rev_lines), inline=True)

        # PER
        pe_lines = []
        if data["trailing_pe"]:
            pe_lines.append(f"**현재 PER**: {data['trailing_pe']:.1f}")
        if data["forward_pe"]:
            pe_lines.append(f"**예상 PER**: {data['forward_pe']:.1f}")
        if pe_lines:
            embed.add_field(name="📈 밸류에이션", value="\n".join(pe_lines), inline=True)

        # 배당 일정
        div_lines = []
        if data["ex_dividend_date"]:
            div_lines.append(f"**배당락일**: {_fmt_date(data['ex_dividend_date'])}")
        if data["dividend_date"]:
            div_lines.append(f"**배당 지급일**: {_fmt_date(data['dividend_date'])}")
        if div_lines:
            embed.add_field(name="💰 배당 일정", value="\n".join(div_lines), inline=False)

        embed.set_footer(text="Yahoo Finance")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="sector", description="미국 섹터별 등락률을 조회합니다")
    async def sector(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        data = await asyncio.to_thread(_fetch_sector_performance)
        if not data:
            await interaction.followup.send("섹터 데이터를 가져올 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏭 섹터별 등락률 (미국)",
            color=discord.Color.dark_teal(),
            timestamp=datetime.now(KST),
        )

        lines = []
        for s in data:
            icon = "🟢" if s["change_pct"] >= 0 else "🔴"
            sign = "+" if s["change_pct"] >= 0 else ""
            bar_len = min(int(abs(s["change_pct"]) * 5), 15)
            bar = "█" * bar_len
            lines.append(
                f"{icon} **{s['name']}** ({s['symbol']}): {sign}{s['change_pct']:.2f}% {bar}"
            )

        embed.add_field(name="오늘의 섹터 성과", value="\n".join(lines), inline=False)
        embed.set_footer(text="S&P 500 SPDR Sector ETFs 기준")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fundamental(bot))
