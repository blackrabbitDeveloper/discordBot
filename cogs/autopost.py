import asyncio
import json
import os
from datetime import datetime, timedelta, time, timezone

import discord
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands, tasks

KST = timezone(timedelta(hours=9))

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "channels.json")

NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 감시할 지수
WATCH_INDICES = [
    ("S&P 500", "^GSPC"),
    ("나스닥", "^IXIC"),
    ("코스피", "^KS11"),
    ("코스닥", "^KQ11"),
]

# 미국 장 마감 요약 항목
US_SUMMARY = [
    ("🇺🇸 S&P 500", "^GSPC"),
    ("🇺🇸 나스닥", "^IXIC"),
    ("🇺🇸 다우존스", "^DJI"),
    ("🥇 금", "GC=F"),
    ("🛢️ WTI", "CL=F"),
    ("📊 VIX", "^VIX"),
    ("🏦 미국 10년물", "^TNX"),
    ("₿ BTC", "BTC-USD"),
]

ALERT_THRESHOLD = 2.0


# --- Config ---

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _get_channel_id(guild_id: int, channel_type: str) -> int | None:
    config = _load_config()
    return config.get(str(guild_id), {}).get(channel_type)


def _set_channel_id(guild_id: int, channel_type: str, channel_id: int):
    config = _load_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key][channel_type] = channel_id
    _save_config(config)


# --- Yahoo Finance ---

def _fetch_quote(symbol: str) -> dict | None:
    try:
        t = yf.Ticker(symbol)
        info = t.info
        price = info.get("regularMarketPrice")
        if price is None:
            return None
        prev = info.get("regularMarketPreviousClose", price)
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {"price": price, "change": change, "change_pct": change_pct}
    except Exception:
        return None


def _fetch_us_summary() -> dict:
    results = {}
    for label, symbol in US_SUMMARY:
        results[(label, symbol)] = _fetch_quote(symbol)
    return results


# --- Naver Finance (한국) ---

def _fetch_naver_index(market: str) -> dict | None:
    """네이버에서 코스피/코스닥 지수 가져오기."""
    try:
        r = requests.get(
            f"https://m.stock.naver.com/api/index/{market}/price",
            headers=NAVER_HEADERS, timeout=5,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        today = data[0]
        return {
            "close": today["closePrice"],
            "change": today["compareToPreviousClosePrice"],
            "change_pct": today["fluctuationsRatio"],
            "direction": today["compareToPreviousPrice"]["name"],
            "high": today["highPrice"],
            "low": today["lowPrice"],
        }
    except Exception:
        return None


def _fetch_naver_top_stocks(market: str, sort: str, count: int = 5) -> list[dict]:
    """네이버에서 시가총액/상승률 상위 종목 가져오기."""
    try:
        url = f"https://m.stock.naver.com/api/stocks/{sort}/{market}"
        r = requests.get(url, headers=NAVER_HEADERS, timeout=5)
        if r.status_code != 200:
            return []
        data = r.json()
        stocks = data.get("stocks", data) if isinstance(data, dict) else data
        results = []
        for s in stocks[:count]:
            results.append({
                "name": s["stockName"],
                "price": s["closePrice"],
                "change": s["compareToPreviousClosePrice"],
                "change_pct": s["fluctuationsRatio"],
                "direction": s["compareToPreviousPrice"]["name"],
            })
        return results
    except Exception:
        return []


def _fetch_kr_summary() -> dict:
    return {
        "kospi": _fetch_naver_index("KOSPI"),
        "kosdaq": _fetch_naver_index("KOSDAQ"),
        "top_market_cap": _fetch_naver_top_stocks("KOSPI", "marketValue", 5),
        "top_gainers": _fetch_naver_top_stocks("KOSPI", "up", 5),
        "usdkrw": _fetch_quote("USDKRW=X"),
    }


def _naver_icon(direction: str) -> str:
    if direction in ("RISING", "UPPER_LIMIT"):
        return "🟢"
    elif direction in ("FALLING", "LOWER_LIMIT"):
        return "🔴"
    return "⚪"


def _naver_sign(direction: str) -> str:
    if direction in ("RISING", "UPPER_LIMIT"):
        return "+"
    return ""


# --- Cog ---

class ChannelType(discord.Enum):
    장마감요약 = "market_summary"
    급등급락알림 = "price_alert"


class AutoPost(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_alert: dict[str, float] = {}

    async def cog_load(self):
        self.kr_summary_task.start()
        self.us_summary_task.start()
        self.price_alert_task.start()

    async def cog_unload(self):
        self.kr_summary_task.cancel()
        self.us_summary_task.cancel()
        self.price_alert_task.cancel()

    @app_commands.command(name="set-channel", description="자동 포스팅 채널을 설정합니다 (관리자)")
    @app_commands.describe(
        channel_type="포스팅 종류",
        channel="포스팅할 채널",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel_type: ChannelType,
        channel: discord.TextChannel,
    ):
        _set_channel_id(interaction.guild_id, channel_type.value, channel.id)
        label = "장 마감 요약" if channel_type == ChannelType.장마감요약 else "급등/급락 알림"
        await interaction.response.send_message(
            f"✅ **{label}** 채널이 {channel.mention}으로 설정되었습니다.",
            ephemeral=True,
        )

    @set_channel.error
    async def set_channel_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "관리자 권한이 필요합니다.", ephemeral=True
            )

    # --- 한국 장 마감 요약 (KST 16:00) ---
    @tasks.loop(time=[time(16, 0, tzinfo=KST)])
    async def kr_summary_task(self):
        data = await asyncio.to_thread(_fetch_kr_summary)

        embed = discord.Embed(
            title="🇰🇷 한국 장 마감 요약",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST),
        )

        # 지수
        index_lines = []
        for name, idx_data in [("코스피", data["kospi"]), ("코스닥", data["kosdaq"])]:
            if idx_data:
                icon = _naver_icon(idx_data["direction"])
                sign = _naver_sign(idx_data["direction"])
                index_lines.append(
                    f"{icon} **{name}**: {idx_data['close']}"
                    f" ({sign}{idx_data['change_pct']}%)"
                    f"\n　고가 {idx_data['high']} / 저가 {idx_data['low']}"
                )
        if index_lines:
            embed.add_field(name="📊 지수", value="\n".join(index_lines), inline=False)

        # 환율
        usdkrw = data["usdkrw"]
        if usdkrw:
            sign = "+" if usdkrw["change"] >= 0 else ""
            embed.add_field(
                name="💵 환율",
                value=f"USD/KRW: {usdkrw['price']:,.2f} ({sign}{usdkrw['change_pct']:.2f}%)",
                inline=False,
            )

        # 시가총액 상위
        top_cap = data["top_market_cap"]
        if top_cap:
            lines = []
            for s in top_cap:
                icon = _naver_icon(s["direction"])
                sign = _naver_sign(s["direction"])
                lines.append(f"{icon} **{s['name']}**: {s['price']} ({sign}{s['change_pct']}%)")
            embed.add_field(name="🏢 시가총액 상위", value="\n".join(lines), inline=False)

        # 상승률 상위
        gainers = data["top_gainers"]
        if gainers:
            lines = []
            for s in gainers:
                lines.append(f"🚀 **{s['name']}**: {s['price']} (+{s['change_pct']}%)")
            embed.add_field(name="📈 상승률 상위", value="\n".join(lines), inline=False)

        embed.set_footer(text="네이버 금융 · 자동 요약")
        await self._send_to_channels("market_summary", embed)

    @kr_summary_task.before_loop
    async def before_kr_summary(self):
        await self.bot.wait_until_ready()

    # --- 미국 장 마감 요약 (KST 06:00) ---
    @tasks.loop(time=[time(6, 0, tzinfo=KST)])
    async def us_summary_task(self):
        data = await asyncio.to_thread(_fetch_us_summary)

        embed = discord.Embed(
            title="🇺🇸 미국 장 마감 요약",
            color=discord.Color.dark_blue(),
            timestamp=datetime.now(KST),
        )

        lines = []
        for label, symbol in US_SUMMARY:
            d = data.get((label, symbol))
            if d is None:
                lines.append(f"⚪ **{label}**: 데이터 없음")
                continue
            sign = "+" if d["change"] >= 0 else ""
            icon = "🟢" if d["change"] >= 0 else "🔴"
            lines.append(
                f"{icon} **{label}**: {d['price']:,.2f} ({sign}{d['change_pct']:.2f}%)"
            )

        embed.add_field(name="시장 현황", value="\n".join(lines), inline=False)
        embed.set_footer(text="Yahoo Finance · 자동 요약")
        await self._send_to_channels("market_summary", embed)

    @us_summary_task.before_loop
    async def before_us_summary(self):
        await self.bot.wait_until_ready()

    # --- 급등/급락 알림 (5분마다) ---
    @tasks.loop(minutes=5)
    async def price_alert_task(self):
        for name, symbol in WATCH_INDICES:
            data = _fetch_quote(symbol)
            if data is None:
                continue

            pct = data["change_pct"]
            last = self._last_alert.get(symbol)

            if last is not None and abs(pct) <= abs(last):
                continue

            if abs(pct) >= ALERT_THRESHOLD:
                self._last_alert[symbol] = pct
                sign = "+" if pct >= 0 else ""
                if pct >= ALERT_THRESHOLD:
                    title = f"🚀 {name} 급등 알림"
                    color = discord.Color.green()
                else:
                    title = f"🔻 {name} 급락 알림"
                    color = discord.Color.red()

                embed = discord.Embed(
                    title=title,
                    description=f"**{data['price']:,.2f}** ({sign}{pct:.2f}%)",
                    color=color,
                    timestamp=datetime.now(KST),
                )
                await self._send_to_channels("price_alert", embed)
            else:
                if symbol in self._last_alert:
                    del self._last_alert[symbol]

    @price_alert_task.before_loop
    async def before_price_alert(self):
        await self.bot.wait_until_ready()

    # --- Helper ---
    async def _send_to_channels(self, channel_type: str, embed: discord.Embed):
        for guild in self.bot.guilds:
            ch_id = _get_channel_id(guild.id, channel_type)
            if ch_id:
                channel = guild.get_channel(ch_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoPost(bot))
