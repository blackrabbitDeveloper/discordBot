import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, time, timezone
from xml.etree import ElementTree

import discord
import requests
from discord.ext import commands, tasks

from cogs.autopost import _fetch_kr_summary, _fetch_us_summary, _get_channel_id

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}


# --- News fetching ---

def _fetch_naver_news(count: int = 10) -> list[str]:
    """네이버 금융 주요 뉴스 헤드라인을 가져온다.

    Endpoint: https://m.stock.naver.com/api/news/list
    Response: list of objects with 'tit' (title) and 'ohnm' (source) keys.
    """
    try:
        r = requests.get(
            "https://m.stock.naver.com/api/news/list",
            headers=NAVER_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[Briefing] Naver news HTTP %s", r.status_code)
            return []
        data = r.json()
        headlines = []
        for item in data[:count]:
            tit = item.get("tit", "").strip()
            if tit:
                headlines.append(tit)
        return headlines
    except Exception:
        log.warning("[Briefing] Failed to fetch Naver news", exc_info=True)
        return []


def _fetch_google_news(query: str = "stock market Wall Street", count: int = 10) -> list[str]:
    """Google News RSS에서 미국 증시 뉴스 헤드라인을 가져온다."""
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[Briefing] Google News HTTP %s", r.status_code)
            return []
        root = ElementTree.fromstring(r.content)
        items = root.findall(".//item")
        headlines = []
        for item in items[:count]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                headlines.append(title_el.text.strip())
        return headlines
    except Exception:
        log.warning("[Briefing] Failed to fetch Google News", exc_info=True)
        return []


# --- Gemini API ---

_SYSTEM_PROMPT = """너는 투자 커뮤니티의 시황 분석 애널리스트다.
주어진 시장 데이터와 뉴스를 바탕으로 한국어 시황 브리핑을 작성해라.

구조:
1. 시장 요약 (지수 동향, 주요 변동)
2. 주요 이슈 (뉴스 기반, 시장에 영향을 준 이벤트)
3. 섹터/종목 분석 (눈에 띄는 움직임)
4. 내일 주목할 포인트

규칙:
- 사실 기반, 추측 최소화
- 한국어, 존댓말
- 4000자 이내
- 마크다운 볼드(**) 활용하여 핵심 수치/키워드 강조"""


def _generate_briefing(market_data: dict, news: list[str], market_type: str) -> str | None:
    """Gemini 2.5 Flash를 사용하여 시황 브리핑을 생성한다."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("[Briefing] GEMINI_API_KEY not set, skipping briefing")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        market_label = "한국" if market_type == "kr" else "미국"

        # US summary uses tuple keys — convert to serializable dict
        serializable = {}
        for k, v in market_data.items():
            key = f"{k[0]} ({k[1]})" if isinstance(k, tuple) else str(k)
            serializable[key] = v

        user_prompt = (
            f"## {market_label} 시장 데이터\n"
            f"```json\n{json.dumps(serializable, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            f"## 오늘의 주요 뉴스\n"
            + ("\n".join(f"- {h}" for h in news) if news else "뉴스 데이터 없음")
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=4096,
                temperature=0.7,
            ),
        )

        text = response.text
        if not text or not text.strip():
            log.warning("[Briefing] Gemini returned empty response")
            return None
        return text.strip()

    except Exception:
        log.warning("[Briefing] Gemini API call failed", exc_info=True)
        return None


# --- Cog ---

class Briefing(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not os.getenv("GEMINI_API_KEY"):
            log.warning(
                "[Briefing] GEMINI_API_KEY is not set — briefing tasks will not start"
            )
            return
        self.kr_briefing_task.start()
        self.us_briefing_task.start()

    async def cog_unload(self):
        self.kr_briefing_task.cancel()
        self.us_briefing_task.cancel()

    # --- 한국 시장 시황 브리핑 (KST 16:05) ---
    @tasks.loop(time=[time(16, 5, tzinfo=KST)])
    async def kr_briefing_task(self):
        try:
            data, news = await asyncio.gather(
                asyncio.to_thread(_fetch_kr_summary),
                asyncio.to_thread(_fetch_naver_news, 10),
            )

            briefing = await asyncio.to_thread(_generate_briefing, data, news, "kr")
            if not briefing:
                log.warning("[Briefing] KR briefing is empty, skipping post")
                return

            embed = discord.Embed(
                title="📝 🇰🇷 한국 시장 시황 브리핑",
                description=briefing[:4096],
                color=discord.Color.green(),
                timestamp=datetime.now(KST),
            )
            embed.set_footer(text="🤖 Gemini 2.5 Flash · 자동 시황 분석")

            await self._send_to_channels(embed)
        except Exception:
            log.exception("[Briefing] KR briefing task failed")

    @kr_briefing_task.before_loop
    async def before_kr_briefing(self):
        await self.bot.wait_until_ready()

    # --- 미국 시장 시황 브리핑 (KST 06:05) ---
    @tasks.loop(time=[time(6, 5, tzinfo=KST)])
    async def us_briefing_task(self):
        try:
            data, news = await asyncio.gather(
                asyncio.to_thread(_fetch_us_summary),
                asyncio.to_thread(_fetch_google_news, "stock market Wall Street", 10),
            )

            briefing = await asyncio.to_thread(_generate_briefing, data, news, "us")
            if not briefing:
                log.warning("[Briefing] US briefing is empty, skipping post")
                return

            embed = discord.Embed(
                title="📝 🇺🇸 미국 시장 시황 브리핑",
                description=briefing[:4096],
                color=discord.Color.purple(),
                timestamp=datetime.now(KST),
            )
            embed.set_footer(text="🤖 Gemini 2.5 Flash · 자동 시황 분석")

            await self._send_to_channels(embed)
        except Exception:
            log.exception("[Briefing] US briefing task failed")

    @us_briefing_task.before_loop
    async def before_us_briefing(self):
        await self.bot.wait_until_ready()

    # --- Helper ---
    async def _send_to_channels(self, embed: discord.Embed):
        for guild in self.bot.guilds:
            ch_id = _get_channel_id(guild.id, "market_summary")
            if ch_id:
                channel = guild.get_channel(ch_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Briefing(bot))
