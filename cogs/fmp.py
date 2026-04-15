import asyncio
import os
from datetime import datetime, timedelta, timezone, time

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks

KST = timezone(timedelta(hours=9))

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/api"


# --- API helpers ---

def _fmp_get(path: str, params: dict | None = None) -> list | dict | None:
    if not FMP_API_KEY:
        return None
    try:
        p = {"apikey": FMP_API_KEY}
        if params:
            p.update(params)
        r = requests.get(f"{FMP_BASE}{path}", params=p, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _fetch_earnings_calendar(from_date: str, to_date: str) -> list[dict]:
    data = _fmp_get("/v3/earning_calendar", {"from": from_date, "to": to_date})
    if not data or not isinstance(data, list):
        return []
    results = []
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol or "." in symbol:  # 미국 주요 종목만
            continue
        results.append({
            "symbol": symbol,
            "date": item.get("date", ""),
            "time": item.get("time", ""),  # bmo/amc
            "eps_est": item.get("epsEstimated"),
            "revenue_est": item.get("revenueEstimated"),
        })
    return results


def _fetch_ipo_calendar(from_date: str, to_date: str) -> list[dict]:
    data = _fmp_get("/v3/ipo_calendar", {"from": from_date, "to": to_date})
    if not data or not isinstance(data, list):
        return []
    results = []
    for item in data:
        results.append({
            "symbol": item.get("symbol", ""),
            "company": item.get("company", ""),
            "date": item.get("date", ""),
            "exchange": item.get("exchange", ""),
            "price_range": item.get("priceRange", ""),
            "shares": item.get("shares"),
        })
    return results


def _fetch_insider_trading(symbol: str) -> list[dict]:
    data = _fmp_get("/v4/insider-trading", {"symbol": symbol, "page": "0"})
    if not data or not isinstance(data, list):
        return []
    results = []
    for item in data[:15]:
        results.append({
            "name": item.get("reportingName", ""),
            "type": item.get("transactionType", ""),
            "date": item.get("transactionDate", ""),
            "shares": item.get("securitiesTransacted", 0),
            "price": item.get("price", 0),
            "title": item.get("typeOfOwner", ""),
        })
    return results


def _fmt_number(n) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:,.1f}M"
    return f"${n:,.0f}"


def _time_label(t: str) -> str:
    if t == "bmo":
        return "장전"
    if t == "amc":
        return "장후"
    return ""


# --- Cog ---

class FMP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._earnings_cache: list[dict] = []
        self._ipo_cache: list[dict] = []
        self._cache_date: str = ""

    async def cog_load(self):
        if FMP_API_KEY:
            self.refresh_cache_task.start()

    async def cog_unload(self):
        self.refresh_cache_task.cancel()

    # 매일 07:00 KST에 캐시 갱신
    @tasks.loop(time=[time(7, 0, tzinfo=KST)])
    async def refresh_cache_task(self):
        await self._refresh_cache()

    @refresh_cache_task.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()
        # 시작 시 즉시 1회 로드
        await self._refresh_cache()

    async def _refresh_cache(self):
        today = datetime.now(KST).date()
        end = today + timedelta(days=7)
        from_str = today.isoformat()
        to_str = end.isoformat()

        earnings = await asyncio.to_thread(_fetch_earnings_calendar, from_str, to_str)
        ipo = await asyncio.to_thread(_fetch_ipo_calendar, from_str, to_str)

        self._earnings_cache = earnings
        self._ipo_cache = ipo
        self._cache_date = from_str

    def _no_api_key_embed(self) -> discord.Embed:
        return discord.Embed(
            title="⚠️ FMP API 키 필요",
            description="이 기능은 FMP API 키가 필요합니다.\n`FMP_API_KEY` 환경변수를 설정해주세요.",
            color=discord.Color.yellow(),
        )

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(
        name="earnings-calendar", description="이번 주 실적 발표 예정 종목"
    )
    async def earnings_calendar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not FMP_API_KEY:
            await interaction.followup.send(embed=self._no_api_key_embed(), ephemeral=True)
            return

        if not self._earnings_cache:
            await self._refresh_cache()

        data = self._earnings_cache
        embed = discord.Embed(
            title="📊 이번 주 실적 발표 일정",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST),
        )

        if not data:
            embed.description = "이번 주 예정된 실적 발표가 없습니다."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # 날짜별 그룹핑
        grouped: dict[str, list[dict]] = {}
        for item in data:
            grouped.setdefault(item["date"], []).append(item)

        count = 0
        for date_str in sorted(grouped.keys()):
            if count >= 20:
                break
            items = grouped[date_str]
            lines = []
            for item in items[:10]:
                time_str = _time_label(item["time"])
                eps = f"EPS est: ${item['eps_est']:.2f}" if item["eps_est"] else ""
                rev = _fmt_number(item["revenue_est"]) if item["revenue_est"] else ""
                parts = [f"**{item['symbol']}**"]
                if time_str:
                    parts.append(f"({time_str})")
                if eps:
                    parts.append(f"· {eps}")
                if rev:
                    parts.append(f"· 매출 {rev}")
                lines.append(" ".join(parts))
            if len(items) > 10:
                lines.append(f"... 외 {len(items) - 10}개")

            try:
                from datetime import date
                dt = date.fromisoformat(date_str)
                weekday = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
                header = f"📅 {dt.month}/{dt.day} ({weekday})"
            except ValueError:
                header = f"📅 {date_str}"

            embed.add_field(name=header, value="\n".join(lines), inline=False)
            count += 1

        embed.set_footer(text="Financial Modeling Prep · 캐시 데이터")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="ipo", description="신규 상장(IPO) 예정 종목")
    async def ipo(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not FMP_API_KEY:
            await interaction.followup.send(embed=self._no_api_key_embed(), ephemeral=True)
            return

        if not self._ipo_cache:
            await self._refresh_cache()

        data = self._ipo_cache
        embed = discord.Embed(
            title="🆕 신규 상장(IPO) 예정",
            color=discord.Color.green(),
            timestamp=datetime.now(KST),
        )

        if not data:
            embed.description = "이번 주 예정된 IPO가 없습니다."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lines = []
        for item in data[:15]:
            parts = [f"**{item['symbol']}** — {item['company']}"]
            if item["date"]:
                parts.append(f"📅 {item['date']}")
            if item["price_range"]:
                parts.append(f"💰 {item['price_range']}")
            if item["exchange"]:
                parts.append(f"🏛️ {item['exchange']}")
            lines.append("\n".join(parts))

        embed.description = "\n\n".join(lines)
        if len(data) > 15:
            embed.description += f"\n\n... 외 {len(data) - 15}개"

        embed.set_footer(text="Financial Modeling Prep · 캐시 데이터")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(
        name="insider", description="종목의 내부자(임원) 거래 내역을 조회합니다"
    )
    @app_commands.describe(ticker="종목 코드 (예: AAPL)")
    async def insider(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        if not FMP_API_KEY:
            await interaction.followup.send(embed=self._no_api_key_embed(), ephemeral=True)
            return

        symbol = ticker.upper().strip()
        data = await asyncio.to_thread(_fetch_insider_trading, symbol)

        embed = discord.Embed(
            title=f"🕵️ {symbol} 내부자 거래",
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(KST),
        )

        if not data:
            embed.description = "내부자 거래 데이터가 없습니다."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lines = []
        for item in data:
            tx_type = item["type"]
            if "Purchase" in tx_type or "Buy" in tx_type:
                icon = "🟢 매수"
            elif "Sale" in tx_type or "Sell" in tx_type:
                icon = "🔴 매도"
            else:
                icon = f"⚪ {tx_type}"

            shares = f"{item['shares']:,.0f}주" if item["shares"] else ""
            price = f"@ ${item['price']:,.2f}" if item["price"] else ""
            name = item["name"] or "Unknown"
            title_str = f" ({item['title']})" if item["title"] else ""

            lines.append(
                f"{icon} **{name}**{title_str}\n"
                f"　{item['date']} · {shares} {price}"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="Financial Modeling Prep")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(FMP(bot))
