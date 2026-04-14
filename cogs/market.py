import asyncio
from datetime import datetime, timedelta, timezone

import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))

INDICES = [
    ("🇺🇸 S&P 500", "^GSPC"),
    ("🇺🇸 나스닥", "^IXIC"),
    ("🇺🇸 다우존스", "^DJI"),
    ("🇰🇷 코스피", "^KS11"),
    ("🇰🇷 코스닥", "^KQ11"),
]

COMMODITIES = [
    ("🥇 금", "GC=F"),
    ("🛢️ WTI 유가", "CL=F"),
    ("🥈 은", "SI=F"),
]

CURRENCIES = [
    ("💵 USD/KRW", "USDKRW=X"),
    ("💴 JPY/KRW", "JPYKRW=X"),
    ("💶 EUR/KRW", "EURKRW=X"),
]


def _fmt_price(n: float | None, decimals: int = 2) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1000:
        return f"{n:,.{decimals}f}"
    return f"{n:.{decimals}f}"


def _fetch_quote(symbol: str) -> dict | None:
    try:
        t = yf.Ticker(symbol)
        info = t.info
        price = info.get("regularMarketPrice")
        if price is None:
            return None
        prev = info.get("regularMarketPreviousClose", price)
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {
            "price": price,
            "change": change,
            "change_pct": change_pct,
        }
    except Exception:
        return None


def _fetch_all() -> dict:
    results = {}
    all_items = INDICES + COMMODITIES + CURRENCIES
    for label, symbol in all_items:
        results[(label, symbol)] = _fetch_quote(symbol)
    return results


def _format_line(label: str, data: dict | None, decimals: int = 2) -> str:
    if data is None:
        return f"{label}: 데이터 없음"
    sign = "+" if data["change"] >= 0 else ""
    icon = "🟢" if data["change"] >= 0 else "🔴"
    return (
        f"{icon} **{label}**: {_fmt_price(data['price'], decimals)}"
        f"  ({sign}{data['change_pct']:.2f}%)"
    )


class Market(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="market", description="글로벌 시장 개요를 한눈에 봅니다")
    async def market(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        data = await asyncio.to_thread(_fetch_all)

        embed = discord.Embed(
            title="🌍 글로벌 시장 개요",
            color=discord.Color.dark_blue(),
            timestamp=datetime.now(KST),
        )

        # 주요 지수
        index_lines = []
        for label, symbol in INDICES:
            index_lines.append(_format_line(label, data.get((label, symbol))))
        embed.add_field(name="📊 주요 지수", value="\n".join(index_lines), inline=False)

        # 원자재
        commodity_lines = []
        for label, symbol in COMMODITIES:
            commodity_lines.append(_format_line(label, data.get((label, symbol))))
        embed.add_field(name="🏗️ 원자재", value="\n".join(commodity_lines), inline=False)

        # 환율
        currency_lines = []
        for label, symbol in CURRENCIES:
            currency_lines.append(_format_line(label, data.get((label, symbol))))
        embed.add_field(name="💱 환율", value="\n".join(currency_lines), inline=False)

        embed.set_footer(text="Yahoo Finance · 실시간 데이터와 차이가 있을 수 있습니다")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Market(bot))
