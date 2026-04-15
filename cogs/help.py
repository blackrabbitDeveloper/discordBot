from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))

CATEGORIES = [
    (
        "📊 주식",
        [
            ("`/stock`", "주가 조회 (한글/영어/티커)"),
            ("`/stock-chart`", "캔들스틱 차트"),
            ("`/stock-analysis`", "기술적 분석 (MA, RSI, MACD, 볼린저)"),
            ("`/stock-search`", "종목 검색"),
            ("`/stock-news`", "종목 관련 한국어 뉴스"),
        ],
    ),
    (
        "📈 ETF",
        [
            ("`/etf`", "ETF 기본 정보 + 수익률"),
            ("`/etf-holdings`", "상위 구성종목 + 섹터 비중"),
            ("`/etf-chart`", "ETF 차트"),
            ("`/etf-search`", "ETF 검색"),
        ],
    ),
    (
        "🌍 시장",
        [
            ("`/market`", "글로벌 시장 개요 (지수/원자재/환율)"),
            ("`/sector`", "섹터별 등락률"),
            ("`/earnings`", "실적 발표 일정"),
            ("`/dividend`", "배당 정보 조회"),
        ],
    ),
    (
        "🪙 암호화폐",
        [
            ("`/crypto`", "주요 암호화폐 시세"),
            ("`/crypto-detail`", "특정 코인 상세 정보"),
        ],
    ),
    (
        "📋 시장 데이터",
        [
            ("`/earnings-calendar`", "주요 대형주 실적 발표 일정"),
            ("`/insider`", "내부자(임원) 거래 내역 (SEC)"),
        ],
    ),
    (
        "🗺️ 히트맵",
        [
            ("`/heatmap us`", "미국 섹터 히트맵"),
            ("`/heatmap kr`", "한국 시가총액 히트맵"),
        ],
    ),
    (
        "📅 경제 캘린더",
        [
            ("`/calendar`", "이번 주 주요 경제 일정"),
            ("`/calendar month`", "이번 달 경제 일정"),
        ],
    ),
    (
        "🧮 계산기",
        [
            ("`/fire`", "FIRE 달성 계산기 (시드/월적립/수익률)"),
            ("`/dividend-calc`", "배당금 계산기 (종목/투자금)"),
        ],
    ),
    (
        "💱 환율",
        [
            ("`/exchange`", "주요 환율 현황"),
            ("`/exchange-calc`", "환율 계산기"),
        ],
    ),
    (
        "⏰ 알림",
        [
            ("`/remind`", "DM 알림 설정 (일회/반복)"),
            ("`/remind-list`", "내 리마인더 목록"),
            ("`/remind-cancel`", "리마인더 취소"),
        ],
    ),
    (
        "🔧 관리",
        [
            ("`/clear`", "메시지 삭제 (권한 필요)"),
            ("`/set-channel`", "자동 포스팅 채널 설정 (관리자)"),
        ],
    ),
]


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="봇 사용 가이드를 봅니다")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 투자분석 봇 가이드",
            description="투자분석 커뮤니티를 위한 디스코드 봇입니다.\n모든 응답은 본인에게만 보입니다.",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST),
        )

        for category, cmds in CATEGORIES:
            lines = [f"{name} — {desc}" for name, desc in cmds]
            embed.add_field(name=category, value="\n".join(lines), inline=False)

        embed.add_field(
            name="💡 팁",
            value=(
                "• 종목 검색 시 한글(삼성전자), 영어(samsung), 티커(005930.KS) 모두 가능\n"
                "• 입력 중 드롭다운에서 종목을 선택할 수 있습니다\n"
                "• 한국 주식: `.KS`(코스피), `.KQ`(코스닥)"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
