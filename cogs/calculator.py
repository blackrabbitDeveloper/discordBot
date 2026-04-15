import asyncio
from datetime import datetime, timedelta, timezone

import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands

from cogs.fundamental import _resolve_ticker, _has_korean, _ticker_autocomplete

KST = timezone(timedelta(hours=9))


def _fmt_wan(n: float) -> str:
    """만원 단위 금액을 읽기 쉽게 포맷. 10,000만원 이상이면 억 단위로 표시."""
    if abs(n) >= 10000:
        eok = n / 10000
        remainder = n % 10000
        if remainder == 0 or abs(remainder) < 1:
            return f"{eok:,.0f}억원"
        return f"{eok:,.1f}억원"
    return f"{n:,.0f}만원"


def _calc_fire(seed: float, monthly: float, annual_return: float, target: float) -> dict:
    """복리 계산으로 FIRE 달성까지의 기간과 자산 추이를 계산."""
    monthly_rate = annual_return / 100 / 12
    balance = seed
    month = 0
    milestones = []  # (년차, 잔액)
    prev_year = 0

    while balance < target and month < 12 * 100:  # 최대 100년
        balance = balance * (1 + monthly_rate) + monthly
        month += 1
        year = month // 12
        if year > prev_year:
            milestones.append((year, balance))
            prev_year = year

    total_invested = seed + monthly * month
    total_interest = balance - total_invested

    return {
        "months": month,
        "years": month / 12,
        "final_balance": balance,
        "total_invested": total_invested,
        "total_interest": total_interest,
        "milestones": milestones,
        "reached": balance >= target,
    }


def _fetch_dividend_info(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        price = info.get("regularMarketPrice")
        if price is None:
            return None
        div_yield = info.get("dividendYield")
        div_rate = info.get("dividendRate")
        if not div_yield and not div_rate:
            return None
        return {
            "name": info.get("shortName", ticker),
            "price": price,
            "dividend_rate": div_rate or 0,
            "dividend_yield": (div_yield or 0) * 100,
            "payout_ratio": info.get("payoutRatio"),
            "currency": info.get("currency", "USD"),
        }
    except Exception:
        return None


class Calculator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="fire", description="FIRE(경제적 자유) 달성 계산기")
    @app_commands.describe(
        seed="초기 투자금 (만원)",
        monthly="월 적립금 (만원)",
        annual_return="연 예상 수익률 (%)",
        target="목표 금액 (만원, 기본: 100,000만원 = 10억)",
    )
    async def fire(
        self,
        interaction: discord.Interaction,
        seed: float,
        monthly: float,
        annual_return: float,
        target: float = 100000.0,
    ):
        await interaction.response.defer(ephemeral=True)

        if annual_return <= 0 or annual_return > 50:
            await interaction.followup.send("연 수익률은 0~50% 사이로 입력해주세요.", ephemeral=True)
            return
        if seed < 0 or monthly < 0 or target <= 0:
            await interaction.followup.send("금액은 0 이상, 목표는 0보다 커야 합니다.", ephemeral=True)
            return

        data = _calc_fire(seed, monthly, annual_return, target)

        embed = discord.Embed(
            title="🔥 FIRE 계산 결과",
            color=discord.Color.orange(),
            timestamp=datetime.now(KST),
        )

        embed.add_field(name="초기 투자금", value=_fmt_wan(seed), inline=True)
        embed.add_field(name="월 적립금", value=_fmt_wan(monthly), inline=True)
        embed.add_field(name="연 수익률", value=f"{annual_return:.1f}%", inline=True)
        embed.add_field(name="목표 금액", value=_fmt_wan(target), inline=True)

        if data["reached"]:
            years = int(data["years"])
            months = int(data["months"] % 12)
            period = f"**{years}년 {months}개월**" if months else f"**{years}년**"
            embed.add_field(name="🎯 달성 기간", value=period, inline=True)
        else:
            embed.add_field(name="🎯 달성 기간", value="100년 이상", inline=True)

        embed.add_field(name="💰 최종 자산", value=_fmt_wan(data['final_balance']), inline=True)
        embed.add_field(name="📥 총 투자 원금", value=_fmt_wan(data['total_invested']), inline=True)
        embed.add_field(name="📈 총 수익", value=_fmt_wan(data['total_interest']), inline=True)

        # 주요 연차별 자산 추이 (5년 간격)
        milestones = data["milestones"]
        if milestones:
            lines = []
            for year, bal in milestones:
                if year % 5 == 0 or year == len(milestones):
                    lines.append(f"`{year:>3}년차` → {_fmt_wan(bal):>10}")
            if lines:
                embed.add_field(
                    name="📊 자산 추이 (5년 간격)",
                    value="\n".join(lines[:10]),
                    inline=False,
                )

        embed.set_footer(text="복리 기준 · 세금/수수료 미반영")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="dividend-calc", description="배당금 계산기 — 투자 시 예상 연간 배당금")
    @app_commands.describe(
        ticker="종목코드 또는 회사명",
        investment="투자금액 (해당 종목 통화 기준)",
    )
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def dividend_calc(
        self,
        interaction: discord.Interaction,
        ticker: str,
        investment: float,
    ):
        await interaction.response.defer(ephemeral=True)

        if investment <= 0:
            await interaction.followup.send("투자금액은 0보다 커야 합니다.", ephemeral=True)
            return

        resolved = _resolve_ticker(ticker) if _has_korean(ticker) else ticker.upper()
        if resolved is None:
            await interaction.followup.send(f"`{ticker}` 종목을 찾을 수 없습니다.", ephemeral=True)
            return

        data = await asyncio.to_thread(_fetch_dividend_info, resolved)
        if data is None:
            await interaction.followup.send(
                f"`{resolved}` 배당 데이터가 없습니다 (무배당 종목일 수 있습니다).",
                ephemeral=True,
            )
            return

        shares = investment / data["price"]
        annual_dividend = shares * data["dividend_rate"]
        monthly_dividend = annual_dividend / 12
        currency = data["currency"]

        embed = discord.Embed(
            title=f"💰 {data['name']} 배당금 계산",
            color=discord.Color.gold(),
            timestamp=datetime.now(KST),
        )

        embed.add_field(name="투자금액", value=f"{currency} {investment:,.2f}", inline=True)
        embed.add_field(name="현재 주가", value=f"{currency} {data['price']:,.2f}", inline=True)
        embed.add_field(name="매수 가능 주수", value=f"{int(shares):,}주", inline=True)

        embed.add_field(name="배당 수익률", value=f"{data['dividend_yield']:.2f}%", inline=True)
        embed.add_field(name="주당 연 배당금", value=f"{currency} {data['dividend_rate']:,.2f}", inline=True)
        if data["payout_ratio"]:
            embed.add_field(name="배당 성향", value=f"{data['payout_ratio'] * 100:.1f}%", inline=True)

        embed.add_field(
            name="📅 예상 연간 배당금",
            value=f"**{currency} {annual_dividend:,.2f}**",
            inline=True,
        )
        embed.add_field(
            name="📅 예상 월 배당금",
            value=f"**{currency} {monthly_dividend:,.2f}**",
            inline=True,
        )

        embed.set_footer(text="Yahoo Finance · 세전 기준 · 배당 변동 가능")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Calculator(bot))
