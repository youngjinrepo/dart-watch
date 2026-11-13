# 📊 dart-watch

국내 관심 기업 DART 전자공시, 공모주(IPO) 수요예측 100점 스코어링, 미국 주식 밸류체인 뉴스 및 SEC EDGAR 공시를 모니터링해 **Telegram**으로 실시간 알림을 전송하는 자동화 시스템입니다.

---

## ✨ 주요 기능

1. **DART 공시 실시간 알림 (`main.py`)**
   - DART Open API(`list.json`)를 통해 대상 기업(`corp_codes`)의 신규 공시를 조회합니다. (야간·주말 시간외 공시 누락 방지를 위해 전일/금요일부터 조회 후 `rcept_no`로 중복 제거)
   - 공시 원문을 DART Open API(`document.xml` ZIP 내 최대 크기 본문 우선 추출) 및 웹 뷰어(`report/viewer.do`) 이중 경로로 추출합니다.
   - **Google Gemini** 모델 폴백 체인(`gemini-3-flash-preview` → `gemini-2.5-flash` → `gemini-3.1-flash-lite`)을 사용해 투자자 관점 3줄 요약을 생성합니다. (API 키 미설정·실패 시 원문 핵심 항목을 자동 발췌)
   - 발송 완료된 접수번호(`rcept_no`)를 `sent.json`에 순서대로 기록하여 중복 발송을 방지합니다.

2. **AI 공모주(IPO) 100점 스코어링 알림 (`ipo_main.py`)**
   - DART 발행공시(`C001`, 비상장법인 `E`)에서 **`[발행조건확정]증권신고서(지분증권)`** 및 본 투자설명서/증권신고서 원문을 결합해 수집합니다.
   - 7대 평가 항목(**기업 경쟁력 15점, 밸류에이션 20점, 유통가능 물량 20점, 기관 경쟁률 5점, 가격 제시 분포 15점, 의무보유확약 15점, 공모 구조 10점**)을 결정론적 스코어링 엔진으로 채점합니다.
   - 단일/복합 위험 등급 상한(Cap) 및 자료 완성도(80% 미만 시 분석 보류) 규칙을 적용합니다.
   - **2단계 알림 타이밍**: ① 기관 수요예측 확정 공시 직후 정밀 스코어링 리포트 발송 + ② 일반청약 1일차 당일 리마인더 발송 (`ipo_sent.json`에 예약, 완료/만료 항목 자동 정리).

3. **미국 주식 밸류체인 & SEC EDGAR 인텔리전스 레이더 (`news_main.py`)**
   - `config.json`의 `news.us_tickers`에 티커를 지정하면 100% 영문 추론(Thinking)으로 3계층(직접 이슈 / 전방 수요·CapEx / 생태계·경쟁사) 검색 프로필(`us_stock_profiles.json`)을 자동 생성·캐싱합니다.
   - **SEC EDGAR 공식 서버**(`data.sec.gov`)에서 `8-K`, `10-Q`, `10-K`, `Form 4`(내부자 매매), `SC 13D/G` 공시를 API 키 없이 감지·요약합니다.
   - **Google News US (영문 RSS)**에서 3계층 뉴스를 수집하고 유사 제목 중복 차단 후 Gemini로 2차 연결고리까지 한국어 브리핑을 생성합니다.
   - 새로 올라온 SEC 공시나 뉴스가 없으면 AI 호출 없이 1초 만에 종료됩니다.

4. **정시 실행 자동화 (`google_apps_script.js` + `.github/workflows/`)**
   - GitHub Actions `schedule(cron)` 지연을 피하기 위해 **Google Apps Script(`google_apps_script.js`)**가 10분마다 실행되며 KST 정시에 각 워크플로우의 `workflow_dispatch` API를 호출합니다.
   - **`dart.yml` (국내 DART 공시)**: 평일 **08:00 ~ 19:00 (KST) 30분 간격**
   - **`ipo.yml` (공모주 스코어링 & 리마인더)**: 평일 **08:30 / 16:00 / 20:00 (KST)**
   - **`us_news.yml` (미국 주식 밸류체인 & SEC 레이더)**: 월~토 **매시 정각 (KST) 1시간 간격**

---

## 🛠️ 환경 설정

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정 (`.env`)
`.env.example`을 복사해 `.env` 파일을 생성하고 키를 입력합니다.
```env
DART_API_KEY=your-dart-api-key
TELEGRAM_TOKEN=your-telegram-token
CHAT_ID=your-chat-id
GEMINI_API_KEY=your-gemini-api-key
```

### 3. 설정 파일 (`config.json`)
- `corp_codes`: 감시 대상 기업 고유번호 (현재 `00860332` 메리츠금융지주)
- `keywords`: 공시 제목 필터 키워드 (빈 배열 `[]`이면 모든 공시 수신)
- `summary.method`: 요약 방식 (`gemini` 기본값, 키 미설정 시 `simple` 원문 발췌로 자동 대체)
- `news.us_tickers`: 미국 주식 밸류체인 & SEC 감시 티커 목록 (예: `["CRDO"]`)
- `ipo.only_confirmed`: `true`면 `[발행조건확정]` 공시만 분석

---

## 🚀 실행 방법

```bash
# DART 공시 알림 실행 및 로컬 테스트
python main.py
python main.py --dry-run --days 10

# 공모주(IPO) 100점 스코어링 실행 및 로컬 테스트
python ipo_main.py
python ipo_main.py --dry-run --days 5

# 미국 주식 밸류체인 & SEC 레이더 실행 및 로컬 테스트
python news_main.py
python news_main.py --dry-run --ticker CRDO --include-recent-sec

# 기업명으로 DART 고유번호(corp_code) 검색 / 당일 DART 응답 디버깅
python find_corp_code.py
python debug_dart.py
```

---

## 📌 운영 및 유지보수 참고사항 (Reference Notes)

### 1. `bot-state` 브랜치 분리 아키텍처
- 발송 이력(`sent.json`, `ipo_sent.json`, `news_sent.json`)과 AI가 자동 생성한 프로필 캐시(`us_stock_profiles.json`)는 `main` 브랜치를 깔끔하게 유지하기 위해 GitHub 원격 **`bot-state` 브랜치**에 별도 저장됩니다.
- 각 워크플로우(`.github/workflows/dart.yml`, `ipo.yml`, `us_news.yml`)는 **자신이 담당하는 상태 파일만** `bot-state`에서 읽고 덮어쓰며, 정각·16시 등 동시 실행 시 `git push` 충돌을 막기 위해 최대 4회 재시도(re-fetch 후 커밋)합니다.
- 로컬에서 테스트 후 `.gitignore`에 포함된 `*_sent.json`은 원격 `bot-state`에 자동 반영되지 않으므로, 운영 상태 초기화/수정이 필요할 때는 `bot-state` 브랜치를 직접 checkout해 수정해야 합니다.

### 2. Google Apps Script 정시 트리거 & GitHub PAT 관리
- `google_apps_script.js`는 `PropertiesService`에 `LAST_SLOT_<workflow>` 키를 기록하여 10분 간격 실행 중 같은 시간 슬롯 내 중복 트리거를 방지합니다.
- GitHub Personal Access Token(Classic, `repo` + `workflow` 권한)은 Google Apps Script 편집기 내 `GITHUB_TOKEN` 상수에 직접 입력해 사용하며, 절대 Git 저장소에 커밋하지 마세요. 토큰 만료/교체 시 Apps Script에서 교체 후 `testTriggerNow()`로 즉시 테스트할 수 있습니다.

### 3. Gemini API 모델 폴백 및 `thinkingConfig` 주의사항
- 세 스크립트(`main.py`, `ipo_main.py`, `news_main.py`) 모두 무료 쿼터(`429`)나 일시 오류(`503`) 대응을 위해 `gemini-3-flash-preview` → `gemini-2.5-flash` → `gemini-3.1-flash-lite` 순으로 폴백합니다. (단, `ipo_main.py`의 정밀 JSON 추출은 `gemini-2.5-flash` URL 변수를 선언한 뒤 아래 루프에서 `models` 리스트로 덮어써 순회)
- `gemini-3.1-flash-lite` 등 `flash-lite` 계열 모델은 `thinkingConfig` 파라미터를 지원하지 않아 요청 시 400 에러가 날 수 있으므로 `"flash-lite" not in model_name` 조건으로 분기되어 있습니다. 모델 목록 변경 시 이 조건을 함께 확인하세요.

### 4. 미국 주식 티커 추가/갱신 및 SEC EDGAR 정책
- `config.json`의 `news.us_tickers`에 티커를 추가하면 첫 실행 시 `us_stock_profiles.json`에 밸류체인 검색 프로필과 10자리 `sec_cik`가 자동 저장됩니다.
- 사업 구조 변화 등으로 검색 키워드를 재생성하고 싶을 때는 `python news_main.py --ticker <TICKER> --refresh-profile`을 실행하세요.
- `news_main.py`의 `SEC_HEADERS["User-Agent"]`는 SEC EDGAR API 정책상 `AppName/Version (ContactEmail)` 형식을 반드시 유지해야 합니다.

### 5. 공모주(IPO) 100점 스코어링 원문 결합 및 보류 기준
- `[발행조건확정]` 정정신고서는 정정 내용만 담고 있어 재무제표나 유통가능 물량 표가 누락되는 경우가 많습니다. 이를 보완하기 위해 `ipo_main.py`는 원문 길이가 **25만 자 미만**일 때 최근 180일 내 동일 기업의 `투자설명서` 또는 `증권신고서(지분증권)`(15만 자 이상)를 자동으로 찾아 결합합니다.
- 추출된 항목 중 확인 가능한 배점의 합이 **80% 미만(`completeness_pct < 80`)**이면 잘못된 고득점·저득점을 막기 위해 `분석 보류`로 판정합니다.
- 청약 1일차 리마인더(`scheduled_reminders`)는 발송 완료 후 청약 시작일이 지나거나 14일이 경과하면 `save_ipo_state()`에서 자동 정리됩니다.
