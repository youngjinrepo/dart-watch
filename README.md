# 🚀 DART 공시 자동 알림 시스템

Python 기반 DART 공시 조회 및 Telegram 알림 자동화 시스템입니다.

## 📋 기능

- ✅ DART Open API를 통한 실시간 공시 조회
- ✅ `rcept_no` 기반 중복 방지 (간단하고 확실함)
- ✅ 기업 코드 및 키워드 필터링
- ✅ Telegram 메시지 발송 (링크 포함)
- ✅ GitHub Actions 자동 실행
- ✅ **뉴스 알림** (별도 실행): corp_codes → 회사명 검색, 다중 소스 지원, 요약 후 발송

## 🔧 로컬 설정

### 1️⃣ 가상 환경 생성 및 활성화

```bash
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1

# Windows CMD
python -m venv .venv
.venv\Scripts\activate.bat

# Mac/Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 2️⃣ 의존성 설치

```bash
pip install -r requirements.txt
```

### 3️⃣ 환경 변수 설정

`.env` 파일 생성 또는 터미널에서 설정:

```bash
# Windows PowerShell
$env:DART_API_KEY = "your-api-key"
$env:TELEGRAM_TOKEN = "your-token"
$env:CHAT_ID = "your-chat-id"

# Windows CMD
set DART_API_KEY=your-api-key
set TELEGRAM_TOKEN=your-token
set CHAT_ID=your-chat-id

# Mac/Linux
export DART_API_KEY="your-api-key"
export TELEGRAM_TOKEN="your-token"
export CHAT_ID="your-chat-id"
```

### 4️⃣ 로컬 테스트

```bash
python main.py
```

## 📱 환경 변수 얻기

### DART_API_KEY
- [DART Open API](https://opendart.fss.or.kr/intro/main.do) 접속
- 로그인 후 API 키 발급

### TELEGRAM_TOKEN & CHAT_ID
1. [@BotFather](https://t.me/botfather)에서 `/newbot` 명령으로 봇 생성
2. 받은 `HTTP API` 토큰 복사 → `TELEGRAM_TOKEN`
3. [봇 링크](https://t.me/your_bot_username)에서 `/start` 입력
4. 다음 URL에서 `chat_id` 확인:
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```

## 🔄 GitHub Actions 설정

### 1️⃣ GitHub Repository Secrets 추가

Settings → Secrets and variables → Actions에서:

- `DART_API_KEY`
- `TELEGRAM_TOKEN`
- `CHAT_ID`

### 2️⃣ 자동 실행 스케줄

`.github/workflows/dart.yml`에서 설정:

- **장중 (월~금 09:00~18:00)**: 5분마다
- **야간 (19:00~06:00)**: 30분마다
- **수동 트리거**: `workflow_dispatch`

### 3️⃣ 실행 확인

Actions 탭에서 `DART 공시 알림 시스템` 워크플로우 확인

## ⚙️ 설정 커스터마이징

### `main.py`에서 수정

```python
# 감시 대상 기업 코드
CORP_CODES = [
    '00126380',  # 삼성전자
    '00164779',  # SK하이닉스
]

# 필터링 키워드
KEYWORDS = [
    '공시',
    '보고서',
]

# 최대 보관 이력 (건수)
MAX_HISTORY = 100
```

## 📊 동작 흐름

```
1. sent.json 로드 (이전 발송 이력)
   ↓
2. DART API 호출 (오늘 공시 조회)
   ↓
3. 기업 코드 필터링
   ↓
4. 키워드 필터링
   ↓
5. rcept_no 중복 제거
   ↓
6. 신규 공시만 Telegram 발송
   ↓
7. sent.json 업데이트 (최근 100개)
```

## 📰 뉴스 알림 (news_main.py)

공시와 별도로, 관심 기업/키워드 관련 뉴스를 수집해 Telegram으로 발송합니다.

### 설정

1. **config.json** - `news.enabled: true` 로 설정
2. **corp_names** - corp_code → 회사명 매핑 (뉴스 검색 시 회사명으로 검색)
   - 비어있으면 DART API로 자동 조회
3. **news.sources** - 사용할 소스 목록 (`naver`, `example` 등)
4. **환경 변수** - 네이버 뉴스 API: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`

### 뉴스 소스 추가

`news/sources/` 에 새 소스 모듈을 추가하고, `NewsSource` 추상 클래스를 상속해 구현합니다.
`config.json` 의 `news.sources` 와 `news.source_config` 에 등록하면 됩니다.

### 요약

- `simple`: 앞 150자 추출
- `openai`: OpenAI API 요약 (`OPENAI_API_KEY` 필요)

### 실행

```bash
python news_main.py
```

## 📁 파일 구조

```
DartWatch/
├── .github/
│   └── workflows/
│       └── dart.yml          # GitHub Actions 설정
├── .venv/                    # 가상 환경 (ignored)
├── main.py                   # DART 공시 메인 스크립트
├── news_main.py              # 뉴스 알림 메인 스크립트
├── news/
│   ├── base.py               # 뉴스 소스 추상 인터페이스
│   ├── corp_resolver.py      # corp_code → 회사명 변환
│   ├── summarizer.py         # 뉴스 요약
│   └── sources/
│       ├── naver.py          # 네이버 뉴스 API
│       └── example.py        # 예제/플레이스홀더
├── requirements.txt          # Python 의존성
├── .gitignore               # Git 무시 파일
├── README.md                # 이 파일
├── sent.json                # 공시 발송 이력 (자동 생성)
└── news_sent.json           # 뉴스 발송 이력 (자동 생성)
```

## 🐛 문제 해결

### 1. "API Key 에러"
- DART API 키가 유효한지 확인
- 스페이스나 개행 문자 제거

### 2. "Telegram 발송 실패"
- `TELEGRAM_TOKEN` 정확성 확인
- `CHAT_ID` 번호 올바른지 확인 (음수일 수 있음)
- 봇을 채팅방에 초대했는지 확인

### 3. "공시가 나오지 않음"
- 기업 코드(`CORP_CODES`) 확인
- 키워드 필터링(`KEYWORDS`) 재검토
- DART API 페이지에서 해당 기업의 공시 있는지 확인

## 📝 로그 확인

### 로컬 실행 로그
터미널 출력 확인

### GitHub Actions 로그
1. GitHub 저장소 → Actions 탭
2. 워크플로우 실행 클릭
3. logs 확인

## 💡 특징

| 항목 | 설명 |
|------|------|
| **중복 방지** | DART 공시 고유 번호(`rcept_no`) 사용 |
| **상태 저장** | JSON 형식 (최근 100개만) |
| **로직 단순** | 해시 계산 불필요, 이해하기 쉬움 |
| **API 효율성** | 1회/주기 조회 |
| **스케줄 자동화** | GitHub Actions (별도 서버 불필요) |

## 📚 참고 자료

- [DART Open API](https://opendart.fss.or.kr/intro/main.do)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [GitHub Actions 스케줄](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule)

---

**작성일**: 2026-02-13  
**Python 버전**: 3.8+  
**라이선스**: MIT
