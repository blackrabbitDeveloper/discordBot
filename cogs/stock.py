import asyncio
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

matplotlib.use("Agg")  # non-GUI backend

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


def _fmt_number(n: float | None) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1_0000_0000:
        return f"{n / 1_0000_0000:,.1f}억"
    if abs(n) >= 1_0000:
        return f"{n / 1_0000:,.1f}만"
    return f"{n:,.0f}"


def _fmt_price(n: float | None) -> str:
    return f"{n:,.0f}" if n is not None else "N/A"


def _calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


_krx_cache: list[dict] | None = None

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_krx() -> list[dict]:
    """로컬 CSV에서 KRX 종목 목록을 로딩합니다."""
    global _krx_cache
    if _krx_cache is not None:
        return _krx_cache

    import csv

    stocks = []
    csv_path = os.path.join(_DATA_DIR, "krx_stocks.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stocks.append({"name": row["name"], "symbol": row["symbol"]})
    except FileNotFoundError:
        pass

    _krx_cache = stocks
    return stocks


def _search_krx(query: str) -> list[dict]:
    """한글 종목명으로 KRX 종목을 검색합니다."""
    stocks = _load_krx()
    return [s for s in stocks if query in s["name"]][:5]


def _search_ticker(query: str) -> list[dict]:
    """Yahoo Finance 검색 API로 종목을 찾습니다. 영어만 지원."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 5, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code != 200:
            return []
        return [
            {"symbol": q["symbol"], "name": q.get("shortname", q["symbol"])}
            for q in r.json().get("quotes", [])
            if q.get("quoteType") == "EQUITY"
        ]
    except Exception:
        return []


def _has_korean(text: str) -> bool:
    return any("\uac00" <= c <= "\ud7a3" for c in text)


def _resolve_ticker(query: str) -> str | None:
    """티커, 영문 회사명, 한글 회사명 모두 지원."""
    query = query.strip()

    # 한글이 포함되면 KRX 검색
    if _has_korean(query):
        results = _search_krx(query)
        return results[0]["symbol"] if results else None

    # 영문/숫자면 직접 조회 시도
    upper = query.upper()
    if upper.replace(".", "").replace("-", "").isalnum():
        t = yf.Ticker(upper)
        if t.info.get("regularMarketPrice") is not None:
            return upper

    # 직접 조회 실패 시 Yahoo 검색
    results = _search_ticker(query)
    return results[0]["symbol"] if results else None


async def _ticker_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """ticker 파라미터 자동완성. 한글이면 KRX, 영어면 Yahoo 검색."""
    if len(current) < 1:
        return []
    if _has_korean(current):
        results = _search_krx(current)
    else:
        results = _search_ticker(current)
    return [
        app_commands.Choice(name=f"{r['name']} ({r['symbol']})", value=r["symbol"])
        for r in results[:25]
    ]


class Stock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="stock-search", description="종목명으로 검색합니다 (한글/영어)")
    @app_commands.describe(query="검색어 (예: 삼성전자, samsung, apple)")
    async def stock_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        if _has_korean(query):
            results = await asyncio.to_thread(_search_krx, query)
        else:
            results = await asyncio.to_thread(_search_ticker, query)

        if not results:
            await interaction.followup.send(
                f"`{query}`에 대한 검색 결과가 없습니다.",
                ephemeral=True,
            )
            return

        lines = [f"`{r['symbol']}` — {r['name']}" for r in results]
        await interaction.followup.send(
            f"**검색 결과: {query}**\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="stock", description="종목의 현재 주가 정보를 조회합니다")
    @app_commands.describe(ticker="종목코드 또는 회사명 (예: AAPL, 삼성전자, 005930.KS)")
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def stock(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_ticker, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` 종목을 찾을 수 없습니다. `/stock-search`로 검색해보세요.",
                ephemeral=True,
            )
            return

        info = await asyncio.to_thread(self._fetch_info, resolved)
        if info is None:
            await interaction.followup.send(
                f"`{ticker}` 종목을 찾을 수 없습니다.", ephemeral=True
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
        embed.add_field(name="시가", value=_fmt_price(info["open"]), inline=True)
        embed.add_field(name="고가", value=_fmt_price(info["high"]), inline=True)
        embed.add_field(name="저가", value=_fmt_price(info["low"]), inline=True)
        embed.add_field(name="시가총액", value=_fmt_number(info["market_cap"]), inline=True)
        embed.add_field(name="52주 최고", value=_fmt_price(info["week52_high"]), inline=True)
        embed.add_field(name="52주 최저", value=_fmt_price(info["week52_low"]), inline=True)
        embed.set_footer(text="Yahoo Finance")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="stock-chart", description="종목의 캔들스틱 차트를 생성합니다")
    @app_commands.describe(
        ticker="종목코드 또는 회사명 (예: AAPL, 삼성전자, 005930.KS)",
        period="차트 기간",
    )
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def stock_chart(
        self,
        interaction: discord.Interaction,
        ticker: str,
        period: ChartPeriod = ChartPeriod.three_months,
    ):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_ticker, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` 종목을 찾을 수 없습니다. `/stock-search`로 검색해보세요.",
                ephemeral=True,
            )
            return

        buf = await asyncio.to_thread(self._make_chart, resolved, period.value)
        if buf is None:
            await interaction.followup.send(
                f"`{resolved}` 차트 데이터를 가져올 수 없습니다.", ephemeral=True
            )
            return

        file = discord.File(buf, filename="chart.png")
        label = PERIOD_LABELS[period.value]
        await interaction.followup.send(
            f"**{resolved}** {label} 차트", file=file, ephemeral=True
        )

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="stock-analysis", description="종목의 기술적 분석 + 펀더멘탈을 제공합니다")
    @app_commands.describe(ticker="종목코드 또는 회사명 (예: AAPL, 삼성전자, 005930.KS)")
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def stock_analysis(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = await asyncio.to_thread(_resolve_ticker, ticker)
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` 종목을 찾을 수 없습니다. `/stock-search`로 검색해보세요.",
                ephemeral=True,
            )
            return

        result = await asyncio.to_thread(self._analyze, resolved)
        if result is None:
            await interaction.followup.send(
                f"`{resolved}` 분석 데이터를 가져올 수 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📊 {result['name']} 기술적 분석",
            description=f"현재가: **{_fmt_price(result['price'])}**",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST),
        )

        # 이동평균선
        ma_lines = []
        for label, val, curr in result["ma"]:
            if val is not None:
                status = "🟢 위" if curr > val else "🔴 아래"
                ma_lines.append(f"**{label}**: {_fmt_price(val)} ({status})")
        embed.add_field(name="📈 이동평균선", value="\n".join(ma_lines) or "N/A", inline=False)

        # 골든/데드크로스 이벤트
        if result["cross_events"]:
            embed.add_field(
                name="⚡ 크로스 이벤트",
                value="\n".join(result["cross_events"]),
                inline=False,
            )

        # MACD
        macd = result["macd"]
        if macd:
            hist_icon = "📈" if macd["histogram"] > 0 else "📉"
            macd_text = (
                f"MACD: {macd['macd']:.2f}\n"
                f"시그널: {macd['signal']:.2f}\n"
                f"히스토그램: {hist_icon} {macd['histogram']:.2f}"
            )
            if macd["cross"]:
                macd_text += f"\n{macd['cross']}"
            embed.add_field(name="📊 MACD", value=macd_text, inline=True)

        # RSI
        rsi = result["rsi"]
        if rsi is not None:
            if rsi >= 70:
                rsi_status = "🔴 과매수"
            elif rsi <= 30:
                rsi_status = "🟢 과매도"
            else:
                rsi_status = "⚪ 중립"
            embed.add_field(name="📉 RSI (14)", value=f"{rsi:.1f} — {rsi_status}", inline=True)

        # 볼린저 밴드
        bb = result["bb"]
        if bb:
            bb_text = (
                f"상단: {_fmt_price(bb['upper'])}\n"
                f"중심: {_fmt_price(bb['mid'])}\n"
                f"하단: {_fmt_price(bb['lower'])}\n"
                f"{bb['position']}"
            )
            if bb["squeeze"]:
                bb_text += f"\n{bb['squeeze']}"
            embed.add_field(name="🎯 볼린저 밴드", value=bb_text, inline=True)

        # 지지/저항선
        sr = result["support_resistance"]
        sr_text = (
            f"**20일 저항**: {_fmt_price(sr['r1'])}\n"
            f"**20일 지지**: {_fmt_price(sr['s1'])}"
        )
        if sr["r2"] is not None:
            sr_text += (
                f"\n**60일 저항**: {_fmt_price(sr['r2'])}\n"
                f"**60일 지지**: {_fmt_price(sr['s2'])}"
            )
        embed.add_field(name="🔒 지지/저항선", value=sr_text, inline=True)

        # 거래량 추세
        embed.add_field(name="📦 거래량 추세", value=result["vol_trend"], inline=True)

        # 펀더멘탈
        fd = result.get("fundamental", {})
        fd_lines = []
        if fd.get("per") is not None:
            fd_lines.append(f"**PER**: {fd['per']:.1f}")
        if fd.get("forward_per") is not None:
            fd_lines.append(f"**Forward PER**: {fd['forward_per']:.1f}")
        if fd.get("pbr") is not None:
            fd_lines.append(f"**PBR**: {fd['pbr']:.2f}")
        if fd.get("psr") is not None:
            fd_lines.append(f"**PSR**: {fd['psr']:.2f}")
        if fd.get("roe") is not None:
            fd_lines.append(f"**ROE**: {fd['roe'] * 100:.1f}%")
        if fd.get("profit_margin") is not None:
            fd_lines.append(f"**영업이익률**: {fd['profit_margin'] * 100:.1f}%")
        if fd.get("debt_equity") is not None:
            fd_lines.append(f"**부채비율**: {fd['debt_equity']:.0f}%")
        if fd.get("dividend_yield") is not None:
            fd_lines.append(f"**배당수익률**: {fd['dividend_yield'] * 100:.2f}%")
        if fd_lines:
            embed.add_field(name="💼 펀더멘탈", value="\n".join(fd_lines), inline=True)

        # 시가총액
        mc = fd.get("market_cap")
        if mc:
            if mc >= 1e12:
                mc_str = f"${mc / 1e12:,.2f}T"
            elif mc >= 1e9:
                mc_str = f"${mc / 1e9:,.1f}B"
            else:
                mc_str = f"${mc / 1e6:,.0f}M"
            embed.add_field(name="🏢 시가총액", value=mc_str, inline=True)

        # 종합 신호
        embed.add_field(name="🏁 종합 신호", value=result["signal"], inline=False)
        embed.set_footer(text="Yahoo Finance 기반 · 투자 참고용, 투자 판단의 책임은 본인에게 있습니다")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # --- internal helpers (run in thread) ---

    @staticmethod
    def _fetch_info(ticker: str) -> dict | None:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            if not info or info.get("regularMarketPrice") is None:
                return None
            price = info["regularMarketPrice"]
            prev = info.get("regularMarketPreviousClose", price)
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0
            return {
                "symbol": ticker,
                "name": info.get("shortName", ticker),
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "volume": info.get("regularMarketVolume"),
                "open": info.get("regularMarketOpen"),
                "high": info.get("regularMarketDayHigh"),
                "low": info.get("regularMarketDayLow"),
                "market_cap": info.get("marketCap"),
                "week52_high": info.get("fiftyTwoWeekHigh"),
                "week52_low": info.get("fiftyTwoWeekLow"),
            }
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

    @staticmethod
    def _analyze(ticker: str) -> dict | None:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period="1y")
            if df.empty or len(df) < 26:
                return None

            info = t.info
            name = info.get("shortName", ticker)
            close = df["Close"]
            high = df["High"]
            low = df["Low"]
            curr = close.iloc[-1]

            # --- 이동평균선 ---
            ma_values = {}
            ma_data = []
            for days, label in [(5, "5일"), (20, "20일"), (60, "60일"), (120, "120일"), (200, "200일")]:
                if len(close) >= days:
                    series = close.rolling(window=days).mean()
                    val = series.iloc[-1]
                    ma_values[days] = series
                    ma_data.append((label, val, curr))
                else:
                    ma_data.append((label, None, curr))

            # --- RSI ---
            rsi = None
            if len(close) >= 15:
                rsi_series = _calc_rsi(close)
                rsi = rsi_series.iloc[-1]

            # --- MACD ---
            macd_data = None
            if len(close) >= 26:
                ema12 = close.ewm(span=12).mean()
                ema26 = close.ewm(span=26).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9).mean()
                histogram = macd_line - signal_line
                macd_val = macd_line.iloc[-1]
                signal_val = signal_line.iloc[-1]
                hist_val = histogram.iloc[-1]
                # 교차 감지 (최근 5일)
                macd_cross = None
                for i in range(-5, -1):
                    prev_diff = macd_line.iloc[i] - signal_line.iloc[i]
                    curr_diff = macd_line.iloc[i + 1] - signal_line.iloc[i + 1]
                    if prev_diff <= 0 < curr_diff:
                        macd_cross = "🟢 골든크로스 (매수 신호)"
                    elif prev_diff >= 0 > curr_diff:
                        macd_cross = "🔴 데드크로스 (매도 신호)"
                macd_data = {
                    "macd": macd_val,
                    "signal": signal_val,
                    "histogram": hist_val,
                    "cross": macd_cross,
                }

            # --- 볼린저 밴드 ---
            bb_data = None
            if len(close) >= 20:
                ma20 = close.rolling(window=20).mean()
                std20 = close.rolling(window=20).std()
                upper_band = (ma20 + 2 * std20).iloc[-1]
                lower_band = (ma20 - 2 * std20).iloc[-1]
                mid_band = ma20.iloc[-1]
                bandwidth = (upper_band - lower_band) / mid_band * 100
                if curr >= upper_band:
                    bb_position = "🔴 상단 밴드 돌파 (과매수)"
                elif curr <= lower_band:
                    bb_position = "🟢 하단 밴드 돌파 (과매도)"
                elif curr > mid_band:
                    bb_position = "⚪ 중심선 위"
                else:
                    bb_position = "⚪ 중심선 아래"
                if bandwidth < 10:
                    bb_squeeze = "⚠️ 밴드 수축 — 큰 변동 임박 가능"
                else:
                    bb_squeeze = None
                bb_data = {
                    "upper": upper_band,
                    "mid": mid_band,
                    "lower": lower_band,
                    "position": bb_position,
                    "squeeze": bb_squeeze,
                }

            # --- 골든크로스 / 데드크로스 (이평선) ---
            cross_events = []
            cross_pairs = [(5, 20, "5일/20일"), (20, 60, "20일/60일"), (60, 120, "60일/120일")]
            for short_d, long_d, label in cross_pairs:
                if short_d in ma_values and long_d in ma_values:
                    short_ma = ma_values[short_d]
                    long_ma = ma_values[long_d]
                    for i in range(-10, -1):
                        if i >= -len(short_ma) and i >= -len(long_ma):
                            prev = short_ma.iloc[i] - long_ma.iloc[i]
                            curr_diff = short_ma.iloc[i + 1] - long_ma.iloc[i + 1]
                            if prev <= 0 < curr_diff:
                                cross_events.append(f"🟢 **{label} 골든크로스** (최근 10일 내)")
                            elif prev >= 0 > curr_diff:
                                cross_events.append(f"🔴 **{label} 데드크로스** (최근 10일 내)")

            # --- 지지선 / 저항선 ---
            recent_low = low.tail(20).min()
            recent_high = high.tail(20).max()
            month3_low = low.tail(60).min() if len(low) >= 60 else None
            month3_high = high.tail(60).max() if len(high) >= 60 else None
            support_resistance = {
                "s1": recent_low,
                "r1": recent_high,
                "s2": month3_low,
                "r2": month3_high,
            }

            # --- 거래량 추세 ---
            vol = df["Volume"]
            vol_avg_5 = vol.tail(5).mean()
            vol_avg_20 = vol.tail(20).mean()
            if vol_avg_5 > vol_avg_20 * 1.5:
                vol_trend = "📈 급증 (5일 평균 > 20일 평균 × 1.5)"
            elif vol_avg_5 > vol_avg_20:
                vol_trend = "📈 증가"
            elif vol_avg_5 < vol_avg_20 * 0.5:
                vol_trend = "📉 급감"
            else:
                vol_trend = "📉 감소"

            # --- 종합 신호 ---
            bullish, bearish = 0, 0
            for _, val, c in ma_data:
                if val is not None:
                    if c > val:
                        bullish += 1
                    else:
                        bearish += 1
            if rsi is not None:
                if rsi <= 30:
                    bullish += 1
                elif rsi >= 70:
                    bearish += 1
            if macd_data:
                if macd_data["histogram"] > 0:
                    bullish += 1
                else:
                    bearish += 1
                if macd_data["cross"] and "골든" in macd_data["cross"]:
                    bullish += 1
                elif macd_data["cross"] and "데드" in macd_data["cross"]:
                    bearish += 1
            if bb_data:
                if "과매도" in bb_data["position"]:
                    bullish += 1
                elif "과매수" in bb_data["position"]:
                    bearish += 1

            total = bullish + bearish
            if total > 0:
                bull_pct = bullish / total * 100
            else:
                bull_pct = 50

            if bull_pct >= 70:
                signal = f"🟢 **강한 매수 신호** ({bull_pct:.0f}% 긍정)"
            elif bull_pct >= 55:
                signal = f"🟢 **매수 우세** ({bull_pct:.0f}% 긍정)"
            elif bull_pct <= 30:
                signal = f"🔴 **강한 매도 신호** ({100-bull_pct:.0f}% 부정)"
            elif bull_pct <= 45:
                signal = f"🔴 **매도 우세** ({100-bull_pct:.0f}% 부정)"
            else:
                signal = f"⚪ **중립** ({bull_pct:.0f}% 긍정 / {100-bull_pct:.0f}% 부정)"

            # --- 펀더멘탈 ---
            fundamental = {
                "per": info.get("trailingPE"),
                "forward_per": info.get("forwardPE"),
                "psr": info.get("priceToSalesTrailing12Months"),
                "pbr": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),
                "profit_margin": info.get("profitMargins"),
                "debt_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"),
                "market_cap": info.get("marketCap"),
                "dividend_yield": info.get("dividendYield"),
            }

            return {
                "name": name,
                "price": curr,
                "ma": ma_data,
                "rsi": rsi,
                "macd": macd_data,
                "bb": bb_data,
                "cross_events": cross_events,
                "support_resistance": support_resistance,
                "vol_trend": vol_trend,
                "signal": signal,
                "fundamental": fundamental,
            }
        except Exception:
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(Stock(bot))
