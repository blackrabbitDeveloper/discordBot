import asyncio
from datetime import datetime
from io import BytesIO

import os

import discord
import matplotlib.pyplot as plt
import matplotlib.patheffects as patheffects
import matplotlib.font_manager as fm
import squarify
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

from cogs.utils.constants import KST, NAVER_HEADERS, SECTOR_ETFS
from cogs.utils.chart import FONT_NAME  # noqa: F401 – triggers matplotlib setup
from cogs.autopost import _fetch_naver_index

# 히트맵 전용 Black (가장 두꺼운) 폰트
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
_BLACK_FONT_PATH = os.path.join(_PROJECT_ROOT, "assets", "fonts", "NotoSansKR-Black.ttf")
_BLACK_FONT = None
if os.path.exists(_BLACK_FONT_PATH):
    fm.fontManager.addfont(_BLACK_FONT_PATH)
    _BLACK_FONT = fm.FontProperties(fname=_BLACK_FONT_PATH)


# --- 데이터 fetching ---

def _fetch_us_sector_data() -> list[dict]:
    results = []
    for name, symbol, weight in SECTOR_ETFS:
        try:
            t = yf.Ticker(symbol)
            fi = t.fast_info
            price = fi.get("lastPrice") or fi.get("last_price")
            prev = fi.get("previousClose") or fi.get("previous_close")
            if not price or not prev:
                info = t.info
                price = info.get("regularMarketPrice")
                prev = info.get("regularMarketPreviousClose", price)
            if price and prev:
                change_pct = (price - prev) / prev * 100
                results.append({
                    "name": name, "symbol": symbol,
                    "weight": weight, "change_pct": change_pct,
                })
        except Exception:
            continue
    return results


_sp500_cache: list[tuple[str, str]] = []
_sp500_cache_time: float = 0


def _fetch_sp500_list() -> list[tuple[str, str]]:
    """위키피디아에서 S&P 500 종목 목록을 가져온다 (24시간 캐시)."""
    import time as _time
    global _sp500_cache, _sp500_cache_time

    if _sp500_cache and (_time.time() - _sp500_cache_time) < 86400:
        return _sp500_cache

    try:
        from io import StringIO
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        if r.status_code != 200:
            return _sp500_cache or []
        import pandas as pd
        df = pd.read_html(StringIO(r.text))[0]
        _sp500_cache = [
            (sym.replace(".", "-"), name)
            for sym, name in zip(df["Symbol"], df["Security"])
        ]
        _sp500_cache_time = _time.time()
        return _sp500_cache
    except Exception:
        return _sp500_cache or []


_mcap_cache: list[tuple[str, str, float]] = []  # [(symbol, name, marketCap), ...]
_mcap_cache_time: float = 0
_MCAP_CACHE_TTL = 86400  # 24시간


def _fetch_sp500_mcap_ranking(top_n: int = 100) -> list[tuple[str, str, float]]:
    """S&P 500 시가총액 순위를 가져온다 (1시간 캐시)."""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor
    global _mcap_cache, _mcap_cache_time

    if _mcap_cache and (_time.time() - _mcap_cache_time) < _MCAP_CACHE_TTL:
        return _mcap_cache[:top_n]

    sp500 = _fetch_sp500_list()
    if not sp500:
        return _mcap_cache[:top_n] if _mcap_cache else []

    def _get_mcap(item: tuple[str, str]) -> tuple[str, str, float]:
        sym, name = item
        try:
            mc = yf.Ticker(sym).fast_info.get("marketCap") or 0
            return (sym, name, float(mc))
        except Exception:
            return (sym, name, 0)

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(_get_mcap, sp500))

    valid = [(s, n, mc) for s, n, mc in results if mc > 0]
    valid.sort(key=lambda x: x[2], reverse=True)

    _mcap_cache = valid
    _mcap_cache_time = _time.time()
    return valid[:top_n]


def _fetch_sp500_stocks(count: int = 100) -> list[dict]:
    """S&P 500 시가총액 상위 종목 + 실시간 가격 데이터."""
    ranking = _fetch_sp500_mcap_ranking(count)
    if not ranking:
        return []

    symbols = [s for s, _, _ in ranking]
    sym_to_name = {s: n for s, n, _ in ranking}

    try:
        data = yf.download(symbols, period="2d", progress=False, threads=True)
    except Exception:
        return []

    if data.empty or len(data) < 2:
        return []

    close = data["Close"].iloc[-1]
    prev_close = data["Close"].iloc[-2]
    pct_change = ((close - prev_close) / prev_close * 100).dropna()

    results = []
    for sym, name, _ in ranking:
        pct = pct_change.get(sym)
        if pct is None:
            continue
        results.append({
            "name": name,
            "symbol": sym,
            "change_pct": round(float(pct), 2),
        })
    return results


def _fetch_kr_top_stocks(count: int = 30) -> list[dict]:
    """네이버에서 코스피 시가총액 상위 종목."""
    try:
        url = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
        r = requests.get(url, headers=NAVER_HEADERS, timeout=10,
                         params={"pageSize": count})
        if r.status_code != 200:
            return []
        data = r.json()
        stocks = data.get("stocks", data) if isinstance(data, dict) else data
        results = []
        for s in stocks[:count]:
            pct = float(s["fluctuationsRatio"])
            direction = s["compareToPreviousPrice"]["name"]
            if direction in ("FALLING", "LOWER_LIMIT"):
                pct = -abs(pct)
            results.append({
                "name": s["stockName"],
                "change_pct": pct,
            })
        return results
    except Exception:
        return []


# --- 히트맵 생성 ---

_COLOR_STOPS_UP = [
    (0.0, (65, 69, 84)),    # 중립 회색 (#414554)
    (0.5, (52, 82, 62)),    # 미세 상승
    (1.0, (43, 122, 62)),   # 약 상승 (#2b7a3e)
    (2.0, (47, 158, 68)),   # 상승 (#2f9e44)
    (3.0, (48, 204, 90)),   # 강 상승 (#30cc5a)
]
_COLOR_STOPS_DN = [
    (0.0, (65, 69, 84)),    # 중립 회색 (#414554)
    (0.5, (120, 55, 55)),   # 미세 하락
    (1.0, (168, 32, 32)),   # 약 하락 (#a82020)
    (2.0, (201, 42, 42)),   # 하락 (#c92a2a)
    (3.0, (224, 82, 82)),   # 강 하락 (#e05252)
]


def _lerp_color(stops: list[tuple], val: float) -> tuple[int, int, int]:
    """색상 정지점 사이를 선형 보간."""
    val = abs(val)
    if val <= stops[0][0]:
        return stops[0][1]
    if val >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        lo_v, lo_c = stops[i]
        hi_v, hi_c = stops[i + 1]
        if lo_v <= val <= hi_v:
            t = (val - lo_v) / (hi_v - lo_v)
            return (
                int(lo_c[0] + t * (hi_c[0] - lo_c[0])),
                int(lo_c[1] + t * (hi_c[1] - lo_c[1])),
                int(lo_c[2] + t * (hi_c[2] - lo_c[2])),
            )
    return stops[-1][1]


def _change_to_color(pct: float) -> str:
    """변동률을 Finviz 스타일 색상으로 변환 (구간별 보간)."""
    if pct > 0:
        r, g, b = _lerp_color(_COLOR_STOPS_UP, pct)
    elif pct < 0:
        r, g, b = _lerp_color(_COLOR_STOPS_DN, pct)
    else:
        r, g, b = 42, 48, 50
    return f"#{r:02x}{g:02x}{b:02x}"


def _render_heatmap(
    items: list[dict], title: str, use_weight: bool = False
) -> BytesIO:
    """트리맵 히트맵 이미지를 생성."""
    if not items:
        # 빈 이미지
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", fontsize=20)
        ax.set_axis_off()
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf

    if use_weight:
        sizes = [item["weight"] for item in items]
    else:
        # 역순위 기반 크기 (1위가 가장 큼)
        sizes = [len(items) - i for i in range(len(items))]

    colors = [_change_to_color(item["change_pct"]) for item in items]

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("#141417")
    ax.set_facecolor("#141417")

    rects = squarify.plot(
        sizes=sizes,
        label=None,
        color=colors,
        ax=ax,
        bar_kwargs={"linewidth": 2, "edgecolor": "#141417"},
    )

    # 각 블록에 텍스트 추가
    blocks = squarify.squarify(squarify.normalize_sizes(sizes, 100, 100), 0, 0, 100, 100)
    for block, item in zip(blocks, items):
        x = block["x"] + block["dx"] / 2
        y = block["y"] + block["dy"] / 2
        w = block["dx"]
        h = block["dy"]

        # 블록이 너무 작으면 텍스트 생략
        area = w * h
        if area < 15:
            continue

        sign = "+" if item["change_pct"] >= 0 else ""
        pct_text = f"{sign}{item['change_pct']:.1f}%"

        # 블록 크기에 따라 폰트 크기 조정
        name_size = max(min(area ** 0.4 * 1.2, 16), 7)
        pct_size = max(min(area ** 0.4 * 1.0, 14), 6)

        symbol = item.get("symbol", "")
        if symbol:
            label = symbol
        else:
            label = item["name"]

        _shadow = [patheffects.withSimplePatchShadow(offset=(1.5, -1.5), shadow_rgbFace="black", alpha=0.6)]
        _fp = {"fontproperties": _BLACK_FONT} if _BLACK_FONT else {"fontweight": "bold"}
        ax.text(x, y - 1, label, ha="center", va="center",
                fontsize=name_size, color="white",
                path_effects=_shadow, **_fp)
        ax.text(x, y + h * 0.15, pct_text, ha="center", va="center",
                fontsize=pct_size, color="white",
                path_effects=_shadow, **_fp)

    ax.set_title(title, fontsize=18, fontweight="bold", color="white", pad=15)
    ax.invert_yaxis()  # 좌상단 = 1위 (시가총액/순위 순)
    ax.set_axis_off()

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150,
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_us_heatmap() -> BytesIO:
    """미국 섹터 히트맵 생성."""
    data = _fetch_us_sector_data()
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return _render_heatmap(data, f"미국 섹터 히트맵 ({now})", use_weight=True)


def generate_sp500_heatmap(count: int = 30, total: int = 100) -> tuple[BytesIO, list[dict]]:
    """S&P 500 시가총액 히트맵 + 나머지 종목 리스트 반환."""
    all_stocks = _fetch_sp500_stocks(total)
    heatmap_stocks = all_stocks[:count]
    remaining = all_stocks[count:]
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    buf = _render_heatmap(heatmap_stocks, f"S&P 500 시가총액 TOP {count} ({now})", use_weight=False)
    return buf, remaining


def generate_kr_heatmap(count: int = 30) -> BytesIO:
    """한국 시가총액 히트맵 생성."""
    data = _fetch_kr_top_stocks(count)
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return _render_heatmap(data, f"코스피 시가총액 TOP {count} ({now})", use_weight=False)


# --- Cog ---

class HeatmapMarket(discord.Enum):
    us = "us"
    kr = "kr"


class Heatmap(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="heatmap", description="시장 히트맵을 생성합니다")
    @app_commands.describe(
        market="시장 선택 (us: 미국 섹터, kr: 한국 시가총액)",
        count="한국 시장 히트맵 종목 수 (기본 30, 최대 50)",
    )
    async def heatmap(
        self,
        interaction: discord.Interaction,
        market: HeatmapMarket = HeatmapMarket.us,
        count: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)
        count = max(10, min(count, 50))

        if market == HeatmapMarket.kr:
            # 히트맵용 + 텍스트 목록용 데이터 병렬 fetch
            all_stocks, kospi, kosdaq, buf = await asyncio.gather(
                asyncio.to_thread(_fetch_kr_top_stocks, 100),
                asyncio.to_thread(_fetch_naver_index, "KOSPI"),
                asyncio.to_thread(_fetch_naver_index, "KOSDAQ"),
                asyncio.to_thread(generate_kr_heatmap, count),
            )
            filename = "kr_heatmap.png"

            # Embed 1: 시장 요약 + 히트맵
            summary_lines = []
            for name, idx in [("코스피", kospi), ("코스닥", kosdaq)]:
                if idx:
                    sign = "+" if idx["direction"] in ("RISING", "UPPER_LIMIT") else ""
                    icon = "🟢" if idx["direction"] in ("RISING", "UPPER_LIMIT") else "🔴" if idx["direction"] in ("FALLING", "LOWER_LIMIT") else "⚪"
                    summary_lines.append(f"{icon} **{name}** {idx['close']}  ({sign}{idx['change_pct']}%)")
            summary = "\n".join(summary_lines) if summary_lines else ""

            embed1 = discord.Embed(
                title=f"🗺️ 코스피 시가총액 TOP {count}",
                description=f"📊 **시장 요약**\n{summary}" if summary else None,
                color=discord.Color.dark_teal(),
                timestamp=datetime.now(KST),
            )
            embed1.set_image(url=f"attachment://{filename}")
            embed1.set_footer(text="초록=상승 | 빨강=하락")

            # Embed 2: 나머지 종목 텍스트 (count+1 ~ 100위)
            remaining = all_stocks[count:]
            embeds = [embed1]
            if remaining:
                lines = []
                for i, s in enumerate(remaining, start=count + 1):
                    sign = "+" if s["change_pct"] >= 0 else ""
                    lines.append(f"`{i:>3}.` {s['name']}  **{sign}{s['change_pct']:.1f}%**")
                text = "\n".join(lines)
                # Discord embed description 4096자 제한 대응
                if len(text) > 4096:
                    text = text[:4090] + "\n..."
                embed2 = discord.Embed(
                    title=f"📋 {count + 1}~{count + len(remaining)}위",
                    description=text,
                    color=discord.Color.dark_teal(),
                )
                embeds.append(embed2)

            file = discord.File(buf, filename=filename)
            await interaction.followup.send(embeds=embeds, file=file, ephemeral=True)
        else:
            buf, remaining = await asyncio.to_thread(generate_sp500_heatmap, count, 100)
            filename = "us_heatmap.png"

            embed1 = discord.Embed(
                title=f"🗺️ S&P 500 시가총액 TOP {count}",
                color=discord.Color.dark_teal(),
                timestamp=datetime.now(KST),
            )
            embed1.set_image(url=f"attachment://{filename}")
            embed1.set_footer(text="초록=상승 | 빨강=하락")

            embeds = [embed1]
            if remaining:
                lines = []
                for i, s in enumerate(remaining, start=count + 1):
                    sign = "+" if s["change_pct"] >= 0 else ""
                    lines.append(f"`{i:>3}.` {s['name']} ({s['symbol']})  **{sign}{s['change_pct']:.1f}%**")
                text = "\n".join(lines)
                if len(text) > 4096:
                    text = text[:4090] + "\n..."
                embed2 = discord.Embed(
                    title=f"📋 {count + 1}~{count + len(remaining)}위",
                    description=text,
                    color=discord.Color.dark_teal(),
                )
                embeds.append(embed2)

            file = discord.File(buf, filename=filename)
            await interaction.followup.send(embeds=embeds, file=file, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Heatmap(bot))
