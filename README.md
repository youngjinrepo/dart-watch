# 📊 dart-watch

관심 기업(**메리츠금융지주**)의 DART 전자공시 및 관련 뉴스를 모니터링하여 AI(Claude / OpenAI) 요약과 함께 **Telegram**으로 실시간 알림을 전송하는 자동화 시스템입니다.

---

## ✨ 주요 기능

1. **DART 공시 실시간 알림 (`main.py`)**
   - DART Open API(`list.json`)를 통해 대상 기업(`corp_codes`)의 신규 공시를 조회합니다.
   - 공시 원문을 DART Open API(`document.xml`) 및 웹 뷰어(`report/viewer.do`) 이중 경로로 추출합니다.
   - **Google Gemini**(`gemini-2.5-flash`)를 사용해 투자자 관점 3줄 요약을 생성합니다. (API 키 미설정 시 원문 핵심 항목을 자동 발췌)
   - 발송 완료된 접수번호(`rcept_no`)를 `sent.json`에 순서대로 기록하여 중복 발송을 방지합니다.

2. **AI 공모주(IPO) 100점 스코어링 알림 (`ipo_main.py`)**
   - DART 발행공시(`C001`, 비상장법인 `E`)에서 **`[발행조건확정]증권신고서(지분증권)`** 및 본 투자설명서/증권신고서 원문을 결합해 수집합니다.
   - 7대 평가 항목(**기업 경쟁력 15점, 밸류에이션 20점, 유통가능 물량 20점, 기관 경쟁률 5점, 가격 제시 분포 15점, 의무보유확약 15점, 공모 구조 10점**)을 결정론적 스코어링 엔진으로 채점합니다.
   - 단일/복합 위험 등급 상한(Cap) 및 자료 완성도(80% 미만 시 분석 보류) 규칙을 적용합니다.
   - **2단계 알림 타이밍**: ① 기관 수요예측 확정 공시 직후 정밀 스코어링 리포트 발송 + ② 일반청약 1일차 당일 리마인더 발송 (`.github/workflows/ipo.yml` 평일 13:00 / 18:30 실행).

3. **뉴스 수집 및 알림 (`news_main.py`, 선택 기능)**
   - 기업 코드(`corp_code`)를 회사명으로 자동 변환하여 네이버 뉴스 검색 API 등에서 최신 기사를 수집합니다.
   - 단순 추출(`simple`) 또는 OpenAI(`gpt-4o-mini`) 요약 후 텔레그램으로 전송합니다.

3. **GitHub Actions 서버리스 자동화 (`.github/workflows/`)**
   - **`dart.yml` (메리츠금융지주 공시)**: DART 실제 공시 공개 시간대인 **평일 08:00 ~ 19:00 (KST)에 15분 간격**으로 실행됩니다. (공시가 올라오지 않는 야간·새벽·주말 헛도는 스케줄 제거)
   - **`ipo.yml` (공모주 100점 스코어링)**: **평일 13:00 (점심) / 18:30 (저녁) 하루 2번** 실행되어 수요예측 확정 공시 분석 및 청약 1일차 리마인더를 발송합니다.
   - 신규 발송 건이 있으면 `sent.json` / `ipo_sent.json`을 자동으로 커밋 & 푸시하여 상태를 유지합니다.

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

---

## 🚀 실행 방법

```bash
# DART 공시 알림 실행 (당일/주말 공시 조회 및 텔레그램 발송)
python main.py

# 로컬 테스트 실행 (텔레그램 발송 없이 최근 10일간 공시 요약 미리보기)
python main.py --dry-run --days 10

# 공모주(IPO) 100점 스코어링 실행 및 로컬 테스트
python ipo_main.py
python ipo_main.py --dry-run --days 5

# 기업명으로 DART 고유번호(corp_code) 검색
python find_corp_code.py

# 당일 DART 공시 응답 디버깅
python debug_dart.py
```
