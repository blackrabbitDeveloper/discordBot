import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import squarify
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

matplotlib.use("Agg")

KST = timezone(timedelta(hours=9))

NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 미국 섹터 ETF (시가총액 가중치 근사)
US_SECTORS = [
    ("기술", "XLK", 30),
    ("헬스케어", "XLV", 13),
    ("금융", "XLF", 13),
    ("소비재", "XLY", 10),
    ("통신", "XLC", 9),
    ("산업", "XLI", 8),
    ("필수소비재", "XLP", 6),
    ("에너지", "XLE", 4),
    ("유틸리티", "XLU", 3),
    ("부동산", "XLRE", 2),
    ("소재", "XLB", 2),
]


# --- 한글 폰트 설정 ---

def _get_korean_font():
    """시스템에서 한글 폰트를 찾아 반환."""
    korean_fonts = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic"]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in korean_fonts:
        if name in available:
            return name
    return None


_KOREAN_FONT = _get_korean_font()
if _KOREAN_FONT:
    plt.rcParams["font.family"] = _KOREAN_FONT
plt.rcParams["axes.unicode_minus"] = False


# --- 데이터 fetching ---

def _fetch_us_sector_data() -> list[dict]:
    results = []
    for name, symbol, weight in US_SECTORS:
        try:
            t = yf.Ticker(symbol)
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


def _fetch_kr_top_stocks(count: int = 20) -> list[dict]:
    """네이버에서 코스피 시가총액 상위 종목."""
    try:
        url = f"https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
        r = requests.get(url, headers=NAVER_HEADERS, timeout=10)
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
            # 시가총액 근사 (순위 기반 가중치)
            results.append({
                "name": s["stockName"],
                "change_pct": pct,
            })
        return results
    except Exception:
        return []


# --- 히트맵 생성 ---

def _change_to_color(pct: float) -> str:
    """변동률을 빨강-회색-초록 색상으로 변환."""
    intensity = min(abs(pct) / 3.0, 1.0)  # ±3% 에서 최대 채도
    if pct > 0:
        r = int(40 + (1 - intensity) * 80)
        g = int(120 + intensity * 135)
        b = int(40 + (1 - intensity) * 80)
    elif pct < 0:
        r = int(120 + intensity * 135)
        g = int(40 + (1 - intensity) * 80)
        b = int(40 + (1 - intensity) * 80)
    else:
        r, g, b = 128, 128, 128
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
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    rects = squarify.plot(
        sizes=sizes,
        label=None,
        color=colors,
        ax=ax,
        bar_kwargs={"linewidth": 2, "edgecolor": "#1a1a2e"},
    )

    # 각 블록에 텍스트 추가
    blocks = squarify.squarify(squarify.normalize_sizes(sizes, 100, 100), 0, 0, 100, 100)
    for block, item in zip(blocks, items):
        x = block["x"] + block["dx"] / 2
        y = block["y"] + block["dy"] / 2
        w = block["dx"]
        h = block["dy"]

        sign = "+" if item["change_pct"] >= 0 else ""
        pct_text = f"{sign}{item['change_pct']:.1f}%"

        # 블록 크기에 따라 폰트 크기 조정
        area = w * h
        name_size = max(min(area ** 0.4 * 1.2, 16), 7)
        pct_size = max(min(area ** 0.4 * 1.0, 14), 6)

        symbol = item.get("symbol", "")
        if symbol and w > 12:
            label = f"{item['name']}\n{symbol}"
        else:
            label = item["name"]

        ax.text(x, y + 1, label, ha="center", va="center",
                fontsize=name_size, fontweight="bold", color="white")
        ax.text(x, y - h * 0.15, pct_text, ha="center", va="center",
                fontsize=pct_size, color="white", alpha=0.9)

    ax.set_title(title, fontsize=18, fontweight="bold", color="white", pad=15)
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


def generate_kr_heatmap() -> BytesIO:
    """한국 시가총액 히트맵 생성."""
    data = _fetch_kr_top_stocks(20)
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return _render_heatmap(data, f"코스피 시가총액 TOP 20 ({now})", use_weight=False)


# --- Cog ---

class HeatmapMarket(discord.Enum):
    us = "us"
    kr = "kr"


class Heatmap(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(name="heatmap", description="시장 히트맵을 생성합니다")
    @app_commands.describe(market="시장 선택 (us: 미국 섹터, kr: 한국 시가총액)")
    async def heatmap(
        self,
        interaction: discord.Interaction,
        market: HeatmapMarket = HeatmapMarket.us,
    ):
        await interaction.response.defer(ephemeral=True)

        if market == HeatmapMarket.kr:
            buf = await asyncio.to_thread(generate_kr_heatmap)
            filename = "kr_heatmap.png"
        else:
            buf = await asyncio.to_thread(generate_us_heatmap)
            filename = "us_heatmap.png"

        file = discord.File(buf, filename=filename)
        embed = discord.Embed(
            title="🗺️ 시장 히트맵",
            color=discord.Color.dark_teal(),
            timestamp=datetime.now(KST),
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="초록=상승 | 빨강=하락")

        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Heatmap(bot))
