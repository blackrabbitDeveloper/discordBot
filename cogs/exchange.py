import asyncio
from datetime import datetime

import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands

from cogs.utils.constants import KST

PAIRS = [
    ("🇺🇸 USD/KRW", "USDKRW=X", "달러/원"),
    ("🇯🇵 JPY/KRW", "JPYKRW=X", "엔/원 (100엔)"),
    ("🇪🇺 EUR/KRW", "EURKRW=X", "유로/원"),
    ("🇬🇧 GBP/KRW", "GBPKRW=X", "파운드/원"),
    ("🇨🇳 CNY/KRW", "CNYKRW=X", "위안/원"),
    ("🇺🇸 EUR/USD", "EURUSD=X", "유로/달러"),
    ("🇺🇸 USD/JPY", "USDJPY=X", "달러/엔"),
]


def _fetch_rate(symbol: str) -> dict | None:
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
            "high": info.get("regularMarketDayHigh"),
            "low": info.get("regularMarketDayLow"),
        }
    except Exception:
        return None


def _fetch_all_rates() -> dict:
    results = {}
    for _, symbol, _ in PAIRS:
        results[symbol] = _fetch_rate(symbol)
    return results


class Exchange(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="exchange", description="주요 환율을 조회합니다")
    async def exchange(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        data = await asyncio.to_thread(_fetch_all_rates)

        embed = discord.Embed(
            title="💱 주요 환율",
            color=discord.Color.teal(),
            timestamp=datetime.now(KST),
        )

        lines = []
        for label, symbol, _ in PAIRS:
            d = data.get(symbol)
            if d is None:
                lines.append(f"⚪ **{label}**: 데이터 없음")
                continue
            sign = "+" if d["change"] >= 0 else ""
            icon = "🔺" if d["change"] > 0 else "🔻" if d["change"] < 0 else "➖"
            lines.append(
                f"{icon} **{label}**: {d['price']:,.2f}"
                f"  ({sign}{d['change_pct']:.2f}%)"
            )

        embed.add_field(name="환율 현황", value="\n".join(lines), inline=False)

        # USD/KRW 상세
        usd = data.get("USDKRW=X")
        if usd and usd["high"] and usd["low"]:
            embed.add_field(
                name="💵 USD/KRW 상세",
                value=f"고가: {usd['high']:,.2f}\n저가: {usd['low']:,.2f}",
                inline=True,
            )

        embed.set_footer(text="Yahoo Finance · 실시간 데이터와 차이가 있을 수 있습니다")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="exchange-calc", description="환율 계산기")
    @app_commands.describe(
        amount="금액",
        source="원본 통화 (예: USD, EUR, JPY)",
        target="대상 통화 (예: KRW, USD, EUR)",
    )
    async def exchange_calc(
        self,
        interaction: discord.Interaction,
        amount: float,
        source: str,
        target: str,
    ):
        await interaction.response.defer(ephemeral=True)

        symbol = f"{source.upper()}{target.upper()}=X"
        data = await asyncio.to_thread(_fetch_rate, symbol)

        if data is None:
            await interaction.followup.send(
                f"`{source.upper()}/{target.upper()}` 환율을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        result = amount * data["price"]
        embed = discord.Embed(
            title="💱 환율 계산",
            color=discord.Color.teal(),
            timestamp=datetime.now(KST),
        )
        embed.add_field(
            name="변환 결과",
            value=(
                f"**{amount:,.2f} {source.upper()}**\n"
                f"= **{result:,.2f} {target.upper()}**\n\n"
                f"적용 환율: {data['price']:,.4f}"
            ),
            inline=False,
        )
        embed.set_footer(text="Yahoo Finance")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Exchange(bot))
