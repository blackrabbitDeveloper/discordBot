import asyncio
import os
import csv
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import discord
import requests
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# KRX 종목 캐시 (한글 → 종목명 변환용)
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


def _resolve_name(query: str) -> str:
    """티커가 입력되면 회사명으로 변환 (뉴스 검색용)."""
    query = query.strip()
    if _has_korean(query):
        return query
    # 티커로 KRX에서 이름 찾기
    upper = query.upper().replace(".KS", "").replace(".KQ", "")
    stocks = _load_krx()
    for s in stocks:
        code = s["symbol"].split(".")[0]
        if code == upper:
            return s["name"]
    return query


def _fetch_news(query: str, count: int = 5) -> list[dict]:
    try:
        search_query = f"{query} 주식"
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": search_query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.status_code != 200:
            return []

        root = ElementTree.fromstring(r.content)
        items = root.findall(".//item")

        results = []
        for item in items[:count]:
            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")
            source = item.find("source")
            results.append({
                "title": title.text if title is not None else "제목 없음",
                "link": link.text if link is not None else "",
                "date": pub_date.text if pub_date is not None else "",
                "source": source.text if source is not None else "알 수 없음",
            })
        return results
    except Exception:
        return []


def _format_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
        dt_kst = dt.replace(tzinfo=timezone.utc).astimezone(KST)
        return dt_kst.strftime("%m/%d %H:%M")
    except Exception:
        return ""


async def _news_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if len(current) < 1:
        return []
    if _has_korean(current):
        stocks = _load_krx()
        matches = [s for s in stocks if current in s["name"]][:10]
        return [
            app_commands.Choice(name=f"{s['name']} ({s['symbol']})", value=s["name"])
            for s in matches
        ]
    return []


class News(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="stock-news", description="종목 관련 한국어 뉴스를 검색합니다")
    @app_commands.describe(
        query="종목명 또는 키워드 (예: 삼성전자, 반도체, 코스피)",
        count="뉴스 개수 (기본 5, 최대 10)",
    )
    @app_commands.autocomplete(query=_news_autocomplete)
    async def stock_news(
        self,
        interaction: discord.Interaction,
        query: str,
        count: int = 5,
    ):
        count = min(max(count, 1), 10)
        await interaction.response.defer(ephemeral=True)

        search_name = _resolve_name(query)
        articles = await asyncio.to_thread(_fetch_news, search_name, count)

        if not articles:
            await interaction.followup.send(
                f"`{query}`에 대한 뉴스를 찾을 수 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📰 {search_name} 관련 뉴스",
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(KST),
        )

        for i, article in enumerate(articles, 1):
            date_str = _format_date(article["date"])
            embed.add_field(
                name=f"{i}. {article['source']}  {date_str}",
                value=f"[{article['title']}]({article['link']})",
                inline=False,
            )

        embed.set_footer(text="Google News")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(News(bot))
