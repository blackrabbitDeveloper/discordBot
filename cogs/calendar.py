import asyncio
import json
import os
from datetime import datetime, timedelta, time, timezone, date

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks

KST = timezone(timedelta(hours=9))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_CALENDAR_PATH = os.path.join(_DATA_DIR, "economic_calendar.json")

FMP_API_KEY = os.getenv("FMP_API_KEY")

COUNTRY_FLAG = {"US": "\U0001f1fa\U0001f1f8", "KR": "\U0001f1f0\U0001f1f7"}
IMPORTANCE_ICON = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\u26aa"}


# --- Data loading ---

def _load_calendar() -> list[dict]:
    try:
        with open(_CALENDAR_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("events", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _fetch_fmp_events(from_date: str, to_date: str) -> list[dict]:
    """FMP API에서 경제 캘린더 보강 데이터를 가져온다."""
    if not FMP_API_KEY:
        return []
    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={"from": from_date, "to": to_date, "apikey": FMP_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        events = []
        for item in r.json():
            country = item.get("country", "")
            if country not in ("US", "KR"):
                continue
            impact = item.get("impact", "").lower()
            if impact not in ("high", "medium"):
                continue
            event_name = item.get("event", "")
            dt_str = item.get("date", "")
            if not dt_str:
                continue
            try:
                dt = datetime.fromisoformat(dt_str)
            except ValueError:
                continue
            events.append({
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "country": country,
                "name": event_name,
                "importance": impact,
                "description": event_name,
                "source": "fmp",
            })
        return events
    except Exception:
        return []


def _get_events_for_range(start: date, end: date) -> list[dict]:
    """지정 기간의 이벤트를 JSON + FMP에서 가져와 날짜순 정렬."""
    events = []

    # JSON 로컬 데이터
    for ev in _load_calendar():
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (ValueError, KeyError):
            continue
        if start <= ev_date <= end:
            events.append(ev)

    # FMP 보강
    fmp_events = _fetch_fmp_events(start.isoformat(), end.isoformat())
    # 중복 제거: 같은 날짜 + 같은 이름은 JSON 우선
    existing = {(e["date"], e["name"]) for e in events}
    for fmp_ev in fmp_events:
        key = (fmp_ev["date"], fmp_ev["name"])
        if key not in existing:
            events.append(fmp_ev)

    events.sort(key=lambda e: (e["date"], e.get("time", "00:00")))
    return events


def _format_event_line(ev: dict) -> str:
    flag = COUNTRY_FLAG.get(ev.get("country", ""), "\U0001f310")
    icon = IMPORTANCE_ICON.get(ev.get("importance", ""), "\u26aa")
    time_str = ev.get("time", "")
    time_part = f" `{time_str}`" if time_str else ""
    return f"{icon} {flag} **{ev['name']}**{time_part}\n　{ev.get('description', '')}"


def _build_calendar_embed(
    title: str, events: list[dict], color: discord.Color
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        color=color,
        timestamp=datetime.now(KST),
    )

    if not events:
        embed.description = "해당 기간에 예정된 주요 경제 일정이 없습니다."
        embed.set_footer(text="경제 캘린더")
        return embed

    # 날짜별로 그룹핑
    grouped: dict[str, list[dict]] = {}
    for ev in events:
        d = ev["date"]
        grouped.setdefault(d, []).append(ev)

    for date_str, day_events in grouped.items():
        try:
            dt = date.fromisoformat(date_str)
            weekday = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
            header = f"\U0001f4c5 {dt.month}/{dt.day} ({weekday})"
        except ValueError:
            header = f"\U0001f4c5 {date_str}"

        lines = [_format_event_line(ev) for ev in day_events]
        value = "\n".join(lines)
        if len(value) > 1024:
            value = value[:1020] + "..."
        embed.add_field(name=header, value=value, inline=False)

    source = "JSON"
    if FMP_API_KEY:
        source += " + FMP"
    embed.set_footer(text=f"경제 캘린더 \u00b7 {source}")
    return embed


# --- Alert helpers ---

def _get_events_for_date(target: date) -> list[dict]:
    return _get_events_for_range(target, target)


def _build_alert_embed(
    label: str, target: date, events: list[dict]
) -> discord.Embed | None:
    if not events:
        return None
    weekday = ["월", "화", "수", "목", "금", "토", "일"][target.weekday()]
    title = f"\U0001f4e2 {label} 주요 경제 일정 ({target.month}/{target.day} {weekday})"
    return _build_calendar_embed(title, events, discord.Color.dark_gold())


# --- Cog ---

class CalendarPeriod(discord.Enum):
    week = "week"
    month = "month"


class Calendar(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.alert_task.start()

    async def cog_unload(self):
        self.alert_task.cancel()

    @app_commands.command(name="calendar", description="주요 경제 일정을 조회합니다")
    @app_commands.describe(period="조회 기간 (기본: 이번 주)")
    async def calendar(
        self,
        interaction: discord.Interaction,
        period: CalendarPeriod = CalendarPeriod.week,
    ):
        await interaction.response.defer(ephemeral=True)

        today = datetime.now(KST).date()

        if period == CalendarPeriod.month:
            start = today
            if today.month < 12:
                end = date(today.year, today.month + 1, 1) - timedelta(days=1)
            else:
                end = date(today.year, 12, 31)
            title = f"\U0001f4c6 이번 달 경제 일정 ({today.month}월)"
        else:
            start = today
            end = today + timedelta(days=7)
            title = "\U0001f4c6 향후 7일 경제 일정"

        events = await asyncio.to_thread(_get_events_for_range, start, end)
        embed = _build_calendar_embed(title, events, discord.Color.teal())
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --- 자동 알림: 매일 D-1(21:00) + 당일(08:00) ---
    @tasks.loop(time=[time(8, 0, tzinfo=KST), time(21, 0, tzinfo=KST)])
    async def alert_task(self):
        now = datetime.now(KST)
        current_hour = now.hour

        if current_hour == 21:
            # D-1 알림: 내일 일정
            target = now.date() + timedelta(days=1)
            label = "내일"
        else:
            # 당일 알림: 오늘 일정
            target = now.date()
            label = "오늘"

        events = _get_events_for_date(target)
        embed = _build_alert_embed(label, target, events)
        if embed is None:
            return

        await self._send_to_channels(embed)

    @alert_task.before_loop
    async def before_alert(self):
        await self.bot.wait_until_ready()

    async def _send_to_channels(self, embed: discord.Embed):
        from cogs.autopost import _get_channel_id

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
    await bot.add_cog(Calendar(bot))
