import asyncio
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
import matplotlib
import mplfinance as mpf
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

matplotlib.use("Agg")

KST = timezone(timedelta(hours=9))


class ChartPeriod(discord.Enum):
    one_month = "1mo"
    three_months = "3mo"
    six_months = "6mo"
    one_year = "1y"


PERIOD_LABELS = {
    "1mo": "1개월",
    "3mo": "3개월",
    "6mo": "6개월",
    "1y": "1년",
}


def _fmt_price(n: float | None) -> str:
    return f"{n:,.0f}" if n is not None else "N/A"


def _fmt_number(n: float | None) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1_0000_0000:
        return f"{n / 1_0000_0000:,.1f}억"
    if abs(n) >= 1_0000:
        return f"{n / 1_0000:,.1f}만"
    return f"{n:,.0f}"


def _fmt_pct(n: float | None) -> str:
    return f"{n:.2f}%" if n is not None else "N/A"


# --- ETF 검색 ---

def _search_etf_yahoo(query: str) -> list[dict]:
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 10, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code != 200:
            return []
        return [
            {"symbol": q["symbol"], "name": q.get("shortname", q["symbol"])}
            for q in r.json().get("quotes", [])
            if q.get("quoteType") == "ETF"
        ][:5]
    except Exception:
        return []


_etf_cache: list[dict] | None = None

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_etf_list() -> list[dict]:
    global _etf_cache
    if _etf_cache is not None:
        return _etf_cache

    import csv

    etfs = []
    csv_path = os.path.join(_DATA_DIR, "etf_list.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                etfs.append({"name": row["name"], "symbol": row["symbol"]})
    except FileNotFoundError:
        pass

    _etf_cache = etfs
    return etfs


def _search_etf_local(query: str) -> list[dict]:
    etfs = _load_etf_list()
    q = query.upper()
    return [e for e in etfs if q in e["name"].upper() or q in e["symbol"].upper()][:5]


def _resolve_etf(query: str) -> str | None:
    query = query.strip()

    # 로컬 CSV 먼저 검색
    results = _search_etf_local(query)
    if results:
        return results[0]["symbol"]

    # 직접 티커 조회
    upper = query.upper()
    if upper.replace(".", "").replace("-", "").isalnum():
        t = yf.Ticker(upper)
        info = t.info
        if info.get("regularMarketPrice") is not None:
            return upper

    # Yahoo 검색 fallback
    results = _search_etf_yahoo(query)
    return results[0]["symbol"] if results else None


async def _etf_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if len(current) < 1:
        return []
    # 로컬 먼저, 부족하면 Yahoo fallback
    results = _search_etf_local(current)
    if not results:
        results = _search_etf_yahoo(current)
    return [
        app_commands.Choice(name=f"{r['name']} ({r['symbol']})", value=r["symbol"])
        for r in results[:25]
    ]


class ETF(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="etf", description="ETF 기본 정보를 조회합니다")
    @app_commands.describe(ticker="ETF 코드 또는 이름 (예: SPY, KODEX 200)")
    @app_commands.autocomplete(ticker=_etf_autocomplete)
    async def etf(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_etf, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` ETF를 찾을 수 없습니다. `/etf-search`로 검색해보세요.",
                ephemeral=True,
            )
            return

        info = await asyncio.to_thread(self._fetch_etf_info, resolved)
        if info is None:
            await interaction.followup.send(
                f"`{resolved}` 정보를 가져올 수 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{info['name']} ({info['symbol']})",
            color=discord.Color.green() if info["change"] >= 0 else discord.Color.red(),
            timestamp=datetime.now(KST),
        )
        sign = "+" if info["change"] >= 0 else ""
        embed.add_field(name="현재가", value=_fmt_price(info["price"]), inline=True)
        embed.add_field(
            name="등락",
            value=f"{sign}{_fmt_price(info['change'])} ({sign}{info['change_pct']:.2f}%)",
            inline=True,
        )
        embed.add_field(name="거래량", value=_fmt_number(info["volume"]), inline=True)
        if info["expense_ratio"] is not None:
            embed.add_field(name="운용보수", value=_fmt_pct(info["expense_ratio"]), inline=True)
        if info["total_assets"] is not None:
            embed.add_field(name="순자산", value=_fmt_number(info["total_assets"]), inline=True)
        if info["nav"] is not None:
            embed.add_field(name="NAV", value=_fmt_price(info["nav"]), inline=True)
        embed.add_field(name="52주 최고", value=_fmt_price(info["week52_high"]), inline=True)
        embed.add_field(name="52주 최저", value=_fmt_price(info["week52_low"]), inline=True)
        if info["category"]:
            embed.add_field(name="카테고리", value=info["category"], inline=True)

        # 수익률
        perf = info["performance"]
        if any(v is not None for v in perf.values()):
            perf_lines = []
            for label, val in perf.items():
                if val is not None:
                    icon = "🟢" if val >= 0 else "🔴"
                    perf_lines.append(f"**{label}**: {icon} {val:+.2f}%")
            if perf_lines:
                embed.add_field(name="📈 수익률", value="\n".join(perf_lines), inline=False)

        embed.set_footer(text="Yahoo Finance")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="etf-holdings", description="ETF 상위 구성종목을 조회합니다")
    @app_commands.describe(ticker="ETF 코드 또는 이름 (예: SPY, KODEX 200)")
    @app_commands.autocomplete(ticker=_etf_autocomplete)
    async def etf_holdings(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_etf, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` ETF를 찾을 수 없습니다.", ephemeral=True
            )
            return

        data = await asyncio.to_thread(self._fetch_holdings, resolved)
        if data is None:
            await interaction.followup.send(
                f"`{resolved}` 구성종목 정보를 가져올 수 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📋 {data['name']} 상위 구성종목",
            color=discord.Color.gold(),
            timestamp=datetime.now(KST),
        )

        if data["holdings"]:
            lines = []
            for i, h in enumerate(data["holdings"], 1):
                lines.append(f"`{i:2d}.` **{h['name']}** — {h['weight']:.2f}%")
            embed.add_field(name="Top Holdings", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Top Holdings", value="데이터 없음", inline=False)

        if data["sectors"]:
            sector_lines = [f"**{s['name']}**: {s['weight']:.1f}%" for s in data["sectors"][:5]]
            embed.add_field(name="📊 섹터 비중", value="\n".join(sector_lines), inline=False)

        embed.set_footer(text="Yahoo Finance")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="etf-chart", description="ETF 차트를 생성합니다")
    @app_commands.describe(
        ticker="ETF 코드 또는 이름 (예: SPY, KODEX 200)",
        period="차트 기간",
    )
    @app_commands.autocomplete(ticker=_etf_autocomplete)
    async def etf_chart(
        self,
        interaction: discord.Interaction,
        ticker: str,
        period: ChartPeriod = ChartPeriod.three_months,
    ):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_etf, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` ETF를 찾을 수 없습니다.", ephemeral=True
            )
            return

        buf = await asyncio.to_thread(self._make_chart, resolved, period.value)
        if buf is None:
            await interaction.followup.send(
                f"`{resolved}` 차트 데이터를 가져올 수 없습니다.", ephemeral=True
            )
            return

        file = discord.File(buf, filename="etf_chart.png")
        label = PERIOD_LABELS[period.value]
        await interaction.followup.send(
            f"**{resolved}** {label} 차트", file=file, ephemeral=True
        )

    @app_commands.command(name="etf-search", description="ETF를 검색합니다")
    @app_commands.describe(query="검색어 (예: KODEX, TIGER, SPY, NASDAQ)")
    async def etf_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        results = await asyncio.to_thread(_search_etf_local, query)
        if not results:
            results = await asyncio.to_thread(_search_etf_yahoo, query)

        if not results:
            await interaction.followup.send(
                f"`{query}`에 대한 ETF 검색 결과가 없습니다.", ephemeral=True
            )
            return

        lines = [f"`{r['symbol']}` — {r['name']}" for r in results]
        await interaction.followup.send(
            f"**ETF 검색 결과: {query}**\n" + "\n".join(lines), ephemeral=True
        )

    # --- internal helpers ---

    @staticmethod
    def _fetch_etf_info(ticker: str) -> dict | None:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            if not info or info.get("regularMarketPrice") is None:
                return None

            price = info["regularMarketPrice"]
            prev = info.get("regularMarketPreviousClose", price)
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0

            # 수익률 계산
            hist = t.history(period="1y")
            performance = {}
            if not hist.empty:
                curr_price = hist["Close"].iloc[-1]
                for days, label in [(5, "1주"), (21, "1개월"), (63, "3개월"), (126, "6개월"), (252, "1년")]:
                    if len(hist) > days:
                        past = hist["Close"].iloc[-days - 1]
                        performance[label] = (curr_price - past) / past * 100
                    else:
                        performance[label] = None

            return {
                "symbol": ticker,
                "name": info.get("shortName", ticker),
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "volume": info.get("regularMarketVolume"),
                "expense_ratio": info.get("annualReportExpenseRatio"),
                "total_assets": info.get("totalAssets"),
                "nav": info.get("navPrice"),
                "week52_high": info.get("fiftyTwoWeekHigh"),
                "week52_low": info.get("fiftyTwoWeekLow"),
                "category": info.get("category"),
                "performance": performance,
            }
        except Exception:
            return None

    @staticmethod
    def _fetch_holdings(ticker: str) -> dict | None:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            name = info.get("shortName", ticker)

            # 상위 구성종목
            holdings = []
            top = t.info.get("holdings", [])
            if not top:
                # yfinance의 다른 방식으로 시도
                try:
                    fund_data = t.funds_data
                    top_holdings = fund_data.top_holdings
                    if top_holdings is not None:
                        for idx, row in top_holdings.iterrows():
                            holdings.append({
                                "name": str(idx),
                                "weight": float(row.get("Holding Percent", 0)) * 100,
                            })
                except Exception:
                    pass
            else:
                for h in top[:10]:
                    holdings.append({
                        "name": h.get("holdingName", "Unknown"),
                        "weight": h.get("holdingPercent", 0) * 100,
                    })

            # 섹터 비중
            sectors = []
            try:
                fund_data = t.funds_data
                sector_weights = fund_data.sector_weightings
                if sector_weights:
                    for sector_dict in sector_weights:
                        for sector_name, weight in sector_dict.items():
                            sectors.append({
                                "name": sector_name,
                                "weight": weight * 100,
                            })
                    sectors.sort(key=lambda x: x["weight"], reverse=True)
            except Exception:
                pass

            return {"name": name, "holdings": holdings, "sectors": sectors}
        except Exception:
            return None

    @staticmethod
    def _make_chart(ticker: str, period: str) -> BytesIO | None:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period)
            if df.empty:
                return None

            buf = BytesIO()
            mpf.plot(
                df,
                type="candle",
                style="charles",
                volume=True,
                mav=(5, 20),
                title=f"\n{ticker}",
                figsize=(10, 6),
                savefig=dict(fname=buf, dpi=100, bbox_inches="tight"),
            )
            buf.seek(0)
            return buf
        except Exception:
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(ETF(bot))
