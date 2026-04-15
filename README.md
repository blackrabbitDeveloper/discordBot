# 투자분석 디스코드 봇

투자 커뮤니티를 위한 디스코드 봇입니다. 주식, ETF, 암호화폐, 환율 조회부터 기술적/펀더멘탈 분석, 시장 히트맵, 경제 캘린더, 내부자 거래까지 다양한 투자 정보를 제공합니다.

## 주요 기능

### 주식
| 명령어 | 설명 |
|--------|------|
| `/stock` | 주가 조회 (한글/영어/티커) |
| `/stock-chart` | 캔들스틱 차트 |
| `/stock-analysis` | 기술적 분석 + 펀더멘탈 (MA, RSI, MACD, 볼린저, PER, ROE 등) |
| `/stock-search` | 종목 검색 |
| `/stock-news` | 종목 관련 한국어 뉴스 |

### ETF
| 명령어 | 설명 |
|--------|------|
| `/etf` | ETF 기본 정보 + 수익률 |
| `/etf-holdings` | 상위 구성종목 + 섹터 비중 |
| `/etf-chart` | ETF 차트 |
| `/etf-search` | ETF 검색 |

### 시장 & 분석
| 명령어 | 설명 |
|--------|------|
| `/market` | 글로벌 시장 개요 (지수/원자재/환율) |
| `/sector` | 미국 섹터별 등락률 |
| `/heatmap us` | 미국 섹터 히트맵 |
| `/heatmap kr` | 한국 시가총액 히트맵 |
| `/earnings` | 실적 발표 일정 |
| `/dividend` | 배당 정보 조회 |

### 시장 데이터
| 명령어 | 설명 |
|--------|------|
| `/earnings-calendar` | 주요 대형주 실적 발표 일정 |
| `/insider` | 내부자(임원) 거래 내역 (SEC EDGAR) |

### 경제 캘린더
| 명령어 | 설명 |
|--------|------|
| `/calendar` | 향후 7일 주요 경제 일정 |
| `/calendar month` | 이번 달 경제 일정 |

### 계산기
| 명령어 | 설명 |
|--------|------|
| `/fire` | FIRE 달성 계산기 (시드/월적립/수익률) |
| `/dividend-calc` | 배당금 계산기 (종목/투자금) |

### 암호화폐 & 환율
| 명령어 | 설명 |
|--------|------|
| `/crypto` | 주요 암호화폐 시세 |
| `/crypto-detail` | 특정 코인 상세 정보 |
| `/exchange` | 주요 환율 현황 |
| `/exchange-calc` | 환율 계산기 |

### 기타
| 명령어 | 설명 |
|--------|------|
| `/remind` | DM 알림 설정 (일회/반복) |
| `/set-channel` | 자동 포스팅 채널 설정 (관리자) |
| `/help` | 봇 사용 가이드 |

## 자동 포스팅

`/set-channel`로 채널을 설정하면 자동으로 포스팅됩니다.

| 시간 (KST) | 내용 |
|------------|------|
| 06:00 | 미국 장 마감 요약 + 섹터 히트맵 |
| 08:00 | 오늘 주요 경제 일정 |
| 16:00 | 한국 장 마감 요약 + 시가총액 히트맵 |
| 21:00 | 내일 주요 경제 일정 |
| 5분마다 | 급등/급락 알림 (±2% 이상) |

## 데이터 소스

| 소스 | 용도 |
|------|------|
| Yahoo Finance | 주가, ETF, 배당, 실적, 환율, 암호화폐 |
| 네이버 금융 | 코스피/코스닥 지수, 한국 종목 순위 |
| SEC EDGAR | 내부자 거래 (Form 4) |
| Google News RSS | 종목 뉴스 |

모든 데이터 소스는 무료이며 API 키가 필요 없습니다.

## 설치

### 요구사항

- Python 3.12+
- Discord Bot Token

### 설정

```bash
git clone https://github.com/blackrabbitDeveloper/discordBot.git
cd discordBot
pip install -r requirements.txt
```

`.env` 파일 생성:

```
DISCORD_TOKEN=your_discord_bot_token
```

### 실행

```bash
python main.py
```

### Railway 배포

1. Railway에 GitHub 레포 연결
2. 환경변수에 `DISCORD_TOKEN` 설정
3. 자동 배포 완료

## 프로젝트 구조

```
├── main.py              # 엔트리포인트
├── bot.py               # 봇 초기화, cog 로드
├── cogs/                # 기능별 모듈
│   ├── stock.py         # 주가, 차트, 분석
│   ├── etf.py           # ETF
│   ├── fundamental.py   # 배당, 실적, 섹터
│   ├── market.py        # 글로벌 시장
│   ├── crypto.py        # 암호화폐
│   ├── exchange.py      # 환율
│   ├── news.py          # 뉴스
│   ├── calculator.py    # FIRE/배당금 계산기
│   ├── calendar.py      # 경제 캘린더
│   ├── heatmap.py       # 시장 히트맵
│   ├── fmp.py           # 실적 캘린더, 내부자 거래
│   ├── autopost.py      # 자동 포스팅
│   ├── reminder.py      # 리마인더
│   ├── moderation.py    # 관리 명령어
│   └── help.py          # 도움말
└── data/
    ├── krx_stocks.csv           # 한국 종목 매핑
    ├── economic_calendar.json   # 2026 경제 일정
    └── fonts/                   # 히트맵용 폰트
```
