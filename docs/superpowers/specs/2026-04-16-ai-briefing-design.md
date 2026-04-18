# AI 시황 브리핑 기능 설계

## 개요

장마감 요약(숫자 데이터) 이후 별도 메시지로 AI 기반 시황 분석 브리핑을 자동 포스팅한다.
Gemini 2.5 Flash를 사용하여 시장 데이터 + 뉴스 헤드라인을 종합 분석한 3~4문단 상세 브리핑을 생성한다.

## 요구사항

- 기존 장마감 요약과 **별도 메시지**로 포스팅 (같은 `market_summary` 채널)
- 한국 시황: 16:05 KST, 미국 시황: 06:05 KST (장마감 요약 5분 뒤)
- 뉴스 소스: 한국은 네이버 금융 뉴스, 미국은 Google News RSS
- 브리핑 내용: 시장 요약 / 주요 이슈 / 섹터-종목 분석 / 내일 주목할 포인트
- Gemini 호출 실패 시 브리핑만 스킵, 기존 장마감 요약에 영향 없음

## 아키텍처

### 접근법: 별도 cog (`cogs/briefing.py`)

AI 브리핑 로직을 독립 cog으로 분리한다. 기존 autopost.py의 데이터 수집 함수를 import하여 재사용하고, 뉴스 수집 + Gemini 호출 + 포스팅은 briefing.py에서 담당한다.

**선택 이유**: AI 관련 로직(외부 API 호출, 프롬프트 관리, 에러 특성)이 기존 데이터 포스팅과 성격이 달라 분리가 유지보수에 유리하다.

### 데이터 흐름

```
[타이머 트리거: 16:05 / 06:05 KST]
       │
       ├── [시장 데이터 수집]
       │     autopost._fetch_kr_summary() / _fetch_us_summary() 재사용
       │
       ├── [뉴스 수집]
       │     KR: 네이버 금융 뉴스 (상위 10건)
       │     US: Google News RSS (상위 10건)
       │
       ▼
[Gemini 2.5 Flash 호출]
  시장 데이터 + 뉴스 헤드라인 → 프롬프트 조합 → 브리핑 텍스트
       │
       ▼
[Discord embed 생성 & 포스팅]
  market_summary 채널에 전송
```

## 컴포넌트 상세

### 1. 뉴스 수집

#### 네이버 금융 뉴스 (한국)
- 엔드포인트: 네이버 금융 뉴스 페이지 크롤링 또는 API
- 수집 항목: 제목 (상위 10건)
- 에러 시: 빈 리스트 반환, 브리핑은 시장 데이터만으로 생성

#### Google News RSS (미국)
- 기존 `cogs/news.py`의 Google News RSS 패턴 참고
- 검색 키워드: "stock market", "Wall Street"
- 수집 항목: 제목 (상위 10건)
- 에러 시: 빈 리스트 반환

### 2. Gemini API 연동

- **모델**: `gemini-2.5-flash`
- **SDK**: `google-genai` 패키지
- **환경변수**: `GEMINI_API_KEY` (.env)
- **호출**: `asyncio.to_thread`로 동기 SDK를 비동기 래핑

#### 프롬프트 구조

```
[시스템 지시]
너는 투자 커뮤니티의 시황 분석 애널리스트다.
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

[데이터]
{시장 데이터 JSON}

[뉴스]
{헤드라인 목록}
```

#### 에러 처리
- API 키 미설정 → cog 로드되지만 태스크 비활성화, 경고 로그 출력
- 호출 실패 / 타임아웃 → `log.exception()` 후 해당 브리핑 스킵
- 빈 응답 → 포스팅 스킵

### 3. Discord 포스팅

#### Embed 구조
- **title**: `📝 🇰🇷 한국 시장 시황 브리핑` / `📝 🇺🇸 미국 시장 시황 브리핑`
- **description**: Gemini 응답 전문 (4096자 제한 내)
- **color**: 한국 `Color.green()`, 미국 `Color.purple()` (기존 요약과 시각적 구분)
- **footer**: `🤖 Gemini 2.5 Flash · 자동 시황 분석`
- **timestamp**: 포스팅 시점 KST

#### 채널 전송
- 기존 `market_summary` 채널 키 공유
- `channels.json` 읽어서 길드별 전송 (autopost와 동일 패턴)

## 파일 변경 범위

### 새로 생성
- `cogs/briefing.py` — AI 시황 브리핑 cog

### 수정
- `requirements.txt` — `google-genai` 의존성 추가
- `.env` — `GEMINI_API_KEY` 추가 (수동, git 추적 안 됨)

### 변경 없음
- `cogs/autopost.py` — 기존 함수를 briefing에서 import만 함
- `bot.py` — cogs 디렉토리 자동 로드 패턴이면 변경 불필요

## Scope 밖
- `/set-channel` 별도 추가 없음 (기존 `market_summary` 채널 재사용)
- 사용자 명령어 없음 (자동 포스팅만)
- 브리핑 히스토리 저장 없음
- 급등/급락 알림에 AI 코멘트 추가 (향후 확장)
