import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, time
from xml.etree import ElementTree

import discord
import requests
from discord.ext import commands, tasks

from cogs.autopost import (
    _fetch_kr_summary, _fetch_us_summary,
    _is_kr_market_open, _is_us_market_open,
)
from cogs.utils.config import get_channel_id
from cogs.utils.constants import KST, NAVER_HEADERS

log = logging.getLogger(__name__)


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
- 브리핑 제목에 제공된 날짜를 반드시 포함
- 사실 기반, 추측 최소화
- 한국어, 존댓말
- 4000자 이내
- 마크다운 볼드(**) 활용하여 핵심 수치/키워드 강조"""


_MODELS = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
_MAX_RETRIES = 3
_RETRY_DELAY = 30  # seconds


def _generate_briefing(market_data: dict, news: list[str], market_type: str) -> str | None:
    """Gemini를 사용하여 시황 브리핑을 생성한다. 503 시 재시도 + 폴백 모델."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("[Briefing] GEMINI_API_KEY not set, skipping briefing")
        return None

    try:
        from google import genai
        from google.genai import types
        from google.genai.errors import ServerError
    except ImportError:
        log.warning("[Briefing] google-genai not installed")
        return None

    client = genai.Client(api_key=api_key)

    market_label = "한국" if market_type == "kr" else "미국"

    # US summary uses tuple keys — convert to serializable dict
    serializable = {}
    for k, v in market_data.items():
        key = f"{k[0]} ({k[1]})" if isinstance(k, tuple) else str(k)
        serializable[key] = v

    _WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%Y년 %m월 %d일") + f" ({_WEEKDAYS[now_kst.weekday()]})"
    user_prompt = (
        f"## 날짜: {today_str}\n\n"
        f"## {market_label} 시장 데이터\n"
        f"```json\n{json.dumps(serializable, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        f"## 오늘의 주요 뉴스\n"
        + ("\n".join(f"- {h}" for h in news) if news else "뉴스 데이터 없음")
    )

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        max_output_tokens=8192,
        temperature=0.7,
    )

    for model in _MODELS:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=model, contents=user_prompt, config=config,
                )
                text = response.text
                if text and text.strip():
                    return text.strip()
                log.warning("[Briefing] %s returned empty response", model)
                break  # empty response — try next model
            except ServerError as e:
                log.warning(
                    "[Briefing] %s attempt %d/%d failed: %s",
                    model, attempt, _MAX_RETRIES, e,
                )
                if attempt < _MAX_RETRIES:
                    import time as _time
                    _time.sleep(_RETRY_DELAY)
            except Exception:
                log.warning("[Briefing] %s unexpected error", model, exc_info=True)
                break  # non-retryable — try next model
        log.warning("[Briefing] %s exhausted, trying next model", model)

    log.error("[Briefing] All models failed")
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
        if not _is_kr_market_open():
            log.info("[Briefing] KR market closed today — skipping briefing")
            return
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
        if not _is_us_market_open():
            log.info("[Briefing] US market closed today — skipping briefing")
            return
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
            ch_id = get_channel_id(guild.id, "market_summary")
            if ch_id:
                channel = guild.get_channel(ch_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Briefing(bot))
