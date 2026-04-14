import asyncio
from datetime import datetime, timedelta, timezone

import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))

CRYPTOS = [
    ("₿ 비트코인", "BTC-USD"),
    ("Ξ 이더리움", "ETH-USD"),
    ("◎ 솔라나", "SOL-USD"),
    ("✕ 리플", "XRP-USD"),
    ("🐕 도지코인", "DOGE-USD"),
    ("⬡ 에이다", "ADA-USD"),
    ("🔷 폴리곤", "MATIC-USD"),
    ("⚡ 아발란체", "AVAX-USD"),
]


def _fmt_price(n: float | None) -> str:
    if n is None:
        return "N/A"
    if n >= 1000:
        return f"${n:,.2f}"
    if n >= 1:
        return f"${n:.4f}"
    return f"${n:.6f}"


def _fmt_number(n: float | None) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1e12:
        return f"${n / 1e12:,.2f}T"
    if abs(n) >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:,.1f}M"
    return f"${n:,.0f}"


def _fetch_crypto(symbol: str) -> dict | None:
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
            "market_cap": info.get("marketCap"),
            "volume_24h": info.get("regularMarketVolume"),
        }
    except Exception:
        return None


def _fetch_all_crypto() -> dict:
    results = {}
    for label, symbol in CRYPTOS:
        results[symbol] = _fetch_crypto(symbol)
    return results


class Crypto(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="crypto", description="주요 암호화폐 시세를 조회합니다")
    async def crypto(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        data = await asyncio.to_thread(_fetch_all_crypto)

        embed = discord.Embed(
            title="🪙 암호화폐 시세",
            color=discord.Color.orange(),
            timestamp=datetime.now(KST),
        )

        lines = []
        for label, symbol in CRYPTOS:
            d = data.get(symbol)
            if d is None:
                lines.append(f"⚪ **{label}**: 데이터 없음")
                continue
            sign = "+" if d["change"] >= 0 else ""
            icon = "🟢" if d["change"] >= 0 else "🔴"
            lines.append(
                f"{icon} **{label}**: {_fmt_price(d['price'])}"
                f"  ({sign}{d['change_pct']:.2f}%)"
            )

        embed.add_field(name="시세", value="\n".join(lines), inline=False)

        # BTC 상세 정보
        btc = data.get("BTC-USD")
        if btc:
            embed.add_field(name="₿ BTC 시총", value=_fmt_number(btc["market_cap"]), inline=True)
            embed.add_field(name="₿ BTC 24h 거래량", value=_fmt_number(btc["volume_24h"]), inline=True)

        embed.set_footer(text="Yahoo Finance · 실시간 데이터와 차이가 있을 수 있습니다")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="crypto-detail", description="특정 암호화폐의 상세 정보를 조회합니다")
    @app_commands.describe(coin="암호화폐 (예: BTC-USD, ETH-USD)")
    async def crypto_detail(self, interaction: discord.Interaction, coin: str):
        await interaction.response.defer(ephemeral=True)

        symbol = coin.upper().strip()
        if not symbol.endswith("-USD"):
            symbol = f"{symbol}-USD"

        data = await asyncio.to_thread(_fetch_crypto, symbol)
        if data is None:
            await interaction.followup.send(
                f"`{symbol}` 데이터를 가져올 수 없습니다.", ephemeral=True
            )
            return

        sign = "+" if data["change"] >= 0 else ""
        embed = discord.Embed(
            title=f"🪙 {symbol}",
            color=discord.Color.green() if data["change"] >= 0 else discord.Color.red(),
            timestamp=datetime.now(KST),
        )
        embed.add_field(name="현재가", value=_fmt_price(data["price"]), inline=True)
        embed.add_field(
            name="등락",
            value=f"{sign}{data['change_pct']:.2f}%",
            inline=True,
        )
        embed.add_field(name="시가총액", value=_fmt_number(data["market_cap"]), inline=True)
        embed.add_field(name="24h 거래량", value=_fmt_number(data["volume_24h"]), inline=True)
        embed.set_footer(text="Yahoo Finance")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Crypto(bot))
