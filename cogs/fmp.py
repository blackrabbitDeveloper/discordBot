import asyncio
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import discord
import requests
import yfinance as yf
from discord import app_commands
from discord.ext import commands

from cogs.fundamental import _resolve_ticker, _has_korean, _ticker_autocomplete

KST = timezone(timedelta(hours=9))

SEC_HEADERS = {"User-Agent": "DiscordBot admin@example.com", "Accept": "application/json"}

# 실적 캘린더 — 주요 대형주 목록
MAJOR_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "MA", "HD", "PG", "JNJ", "COST", "ABBV", "BAC",
    "CRM", "MRK", "AVGO", "KO", "PEP", "TMO", "WMT", "CSCO", "ACN",
    "MCD", "ABT", "DHR", "NEE", "LIN", "ADBE", "TXN", "PM", "NKE",
    "ORCL", "NFLX", "AMD", "INTC", "DIS", "PYPL", "QCOM", "LOW",
    "GS", "MS", "AXP", "CAT", "DE", "UPS",
]


# --- 실적 캘린더 (Yahoo Finance) ---

def _fetch_earnings_calendar() -> list[dict]:
    """주요 대형주의 향후 30일 실적 발표 일정."""
    today = datetime.now(KST).date()
    end = today + timedelta(days=30)
    results = []

    for symbol in MAJOR_TICKERS:
        try:
            t = yf.Ticker(symbol)
            cal = t.calendar
            if not cal:
                continue
            dates = cal.get("Earnings Date", [])
            if not dates:
                continue
            for d in dates:
                if hasattr(d, "date"):
                    d = d.date() if hasattr(d.date, "__call__") else d
                if today <= d <= end:
                    results.append({
                        "symbol": symbol,
                        "date": str(d),
                        "eps_est": cal.get("Earnings Average"),
                        "revenue_est": cal.get("Revenue Average"),
                    })
        except Exception:
            continue

    results.sort(key=lambda x: x["date"])
    return results


# --- 내부자 거래 (SEC EDGAR) ---

# CIK 매핑 캐시 (ticker → CIK)
_cik_map: dict[str, str] | None = None


def _load_cik_map() -> dict[str, str]:
    """SEC 공식 ticker-CIK 매핑을 로드."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
        _cik_map = {
            val["ticker"]: str(val["cik_str"]).zfill(10)
            for val in data.values()
        }
        return _cik_map
    except Exception:
        return {}


def _get_cik(symbol: str) -> str | None:
    """종목 심볼로 SEC CIK 번호를 가져온다."""
    cik_map = _load_cik_map()
    return cik_map.get(symbol)


def _fetch_insider_trades(symbol: str) -> list[dict]:
    """SEC EDGAR에서 내부자 거래 내역을 가져온다."""
    cik = _get_cik(symbol)
    if not cik:
        return []

    try:
        # 회사 filing 목록에서 Form 4 찾기
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=10)
        if r.status_code != 200:
            return []

        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        # 최근 Form 4, 최대 5건
        form4s = []
        for i, f in enumerate(forms):
            if f == "4" and len(form4s) < 5:
                form4s.append((dates[i], accessions[i]))

        if not form4s:
            return []

        # 각 Form 4 XML 파싱
        results = []
        for filing_date, acc_no in form4s:
            trades = _parse_form4(cik, acc_no)
            for trade in trades:
                trade["filing_date"] = filing_date
                results.append(trade)

        return results

    except Exception:
        return []


def _parse_form4(cik: str, acc_no: str) -> list[dict]:
    """Form 4 XML을 파싱하여 거래 내역을 추출."""
    try:
        acc_clean = acc_no.replace("-", "")
        # filing directory에서 form4.xml 찾기
        dir_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/"
        r = requests.get(dir_url, headers=SEC_HEADERS, timeout=10)
        if r.status_code != 200:
            return []

        xml_files = re.findall(r'href="([^"]*\.xml)"', r.text)
        xml_path = None
        for xf in xml_files:
            if "form4" in xf.lower() or xf.endswith(".xml"):
                xml_path = xf
                break

        if not xml_path:
            return []

        # 절대 경로 처리
        if xml_path.startswith("/"):
            xml_url = f"https://www.sec.gov{xml_path}"
        else:
            xml_url = f"{dir_url}{xml_path}"

        r2 = requests.get(xml_url, headers=SEC_HEADERS, timeout=10)
        if r2.status_code != 200:
            return []

        root = ElementTree.fromstring(r2.text)

        # 이름, 직위 추출
        def _text(tag):
            el = root.find(f".//{tag}")
            return el.text.strip() if el is not None and el.text else ""

        owner_name = _text("rptOwnerName")
        officer_title = _text("officerTitle")

        # 거래 내역 추출
        tx_codes = {"P": "매수", "S": "매도", "M": "옵션행사", "F": "세금납부", "A": "수여"}
        trades = []

        # nonDerivativeTransaction 파싱
        for tx in root.findall(".//nonDerivativeTransaction"):
            code_el = tx.find(".//transactionCode")
            code = code_el.text if code_el is not None else ""

            # transactionShares > value
            shares_el = tx.find(".//transactionAmounts/transactionShares/value")
            shares = 0
            if shares_el is not None and shares_el.text:
                try:
                    shares = float(shares_el.text)
                except ValueError:
                    pass

            # transactionPricePerShare > value
            price_el = tx.find(".//transactionAmounts/transactionPricePerShare/value")
            price = 0
            if price_el is not None and price_el.text:
                try:
                    price = float(price_el.text)
                except ValueError:
                    pass

            trades.append({
                "name": owner_name,
                "title": officer_title,
                "code": code,
                "type": tx_codes.get(code, code),
                "shares": shares,
                "price": price,
            })

        return trades

    except Exception:
        return []


def _fmt_number(n) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:,.1f}M"
    return f"${n:,.0f}"


# --- Cog ---

class MarketData(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._earnings_cache: list[dict] = []
        self._cache_date: str = ""

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(
        name="earnings-calendar", description="주요 대형주 실적 발표 일정"
    )
    async def earnings_calendar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        today = datetime.now(KST).strftime("%Y-%m-%d")
        if self._cache_date != today or not self._earnings_cache:
            self._earnings_cache = await asyncio.to_thread(_fetch_earnings_calendar)
            self._cache_date = today

        data = self._earnings_cache

        embed = discord.Embed(
            title="📊 실적 발표 일정 (향후 30일)",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST),
        )

        if not data:
            embed.description = "예정된 실적 발표가 없습니다."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # 날짜별 그룹핑
        grouped: dict[str, list[dict]] = {}
        for item in data:
            grouped.setdefault(item["date"], []).append(item)

        from datetime import date as dt_date
        for date_str in sorted(grouped.keys()):
            items = grouped[date_str]
            lines = []
            for item in items[:10]:
                parts = [f"**{item['symbol']}**"]
                if item["eps_est"]:
                    parts.append(f"EPS est: ${item['eps_est']:.2f}")
                if item["revenue_est"]:
                    parts.append(f"매출 {_fmt_number(item['revenue_est'])}")
                lines.append(" · ".join(parts))
            if len(items) > 10:
                lines.append(f"... 외 {len(items) - 10}개")

            try:
                dt = dt_date.fromisoformat(date_str)
                weekday = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
                header = f"📅 {dt.month}/{dt.day} ({weekday})"
            except ValueError:
                header = f"📅 {date_str}"

            embed.add_field(name=header, value="\n".join(lines), inline=False)

        embed.set_footer(text="Yahoo Finance · 일 1회 캐시")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.checks.cooldown(1, 5)
    @app_commands.command(
        name="insider", description="종목의 내부자(임원) 거래 내역 (SEC EDGAR)"
    )
    @app_commands.describe(ticker="종목 코드 (예: AAPL)")
    @app_commands.autocomplete(ticker=_ticker_autocomplete)
    async def insider(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer(ephemeral=True)

        resolved = _resolve_ticker(ticker) if _has_korean(ticker) else ticker.upper()
        if resolved is None:
            await interaction.followup.send(
                f"`{ticker}` 종목을 찾을 수 없습니다.", ephemeral=True
            )
            return

        # .KS / .KQ 제거 (한국 종목은 SEC에 없음)
        if resolved.endswith((".KS", ".KQ")):
            await interaction.followup.send(
                "한국 종목은 SEC 내부자 거래 조회가 불가합니다. 미국 종목만 지원됩니다.",
                ephemeral=True,
            )
            return

        data = await asyncio.to_thread(_fetch_insider_trades, resolved)

        embed = discord.Embed(
            title=f"🕵️ {resolved} 내부자 거래",
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(KST),
        )

        if not data:
            embed.description = "최근 내부자 거래 내역이 없습니다."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lines = []
        for item in data:
            code = item.get("code", "")
            if code == "P":
                icon = "🟢"
            elif code == "S":
                icon = "🔴"
            elif code == "M":
                icon = "🔵"
            else:
                icon = "⚪"

            shares = f"{item['shares']:,.0f}주" if item["shares"] else ""
            price = f"@ ${item['price']:,.2f}" if item["price"] else ""
            title_str = f" ({item['title']})" if item.get("title") else ""

            lines.append(
                f"{icon} **{item['type']}** — {item['name']}{title_str}\n"
                f"　{item.get('filing_date', '')} · {shares} {price}"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="SEC EDGAR Form 4")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MarketData(bot))
