# Catchup Bot

Slack 채널 메시지를 AI로 요약해서 DM으로 전달하는 봇입니다.

## 아키텍처

**중앙 봇 + 로컬 워커** 구조로 동작합니다.

```
1. 사용자가 Slack에서 /catchup 3d 실행
2. [중앙 봇 - 팀 서버] → 메시지 수집 → JSON 파일 → 사용자 DM에 업로드
3. [로컬 워커 - 각자 PC] → DM에서 파일 감지 → Claude CLI 요약 → 요약 DM 전송
```

| 구성 요소 | 실행 위치 | 역할 |
|-----------|----------|------|
| 중앙 봇 (`app/main.py`) | 팀 서버 (상시 실행) | 슬래시 커맨드 수신, 메시지 수집, JSON 파일 DM 전송 |
| 로컬 워커 (`app/worker.py`) | 각자 PC (필요할 때 실행) | DM 폴링, Claude CLI로 요약 생성, 결과 DM 전송 |
| OAuth 발급 (`app/oauth_server.py`) | 각자 PC (토큰 발급 시 1회만) | Slack 유저 토큰 발급 |

워커는 개인 유저 토큰(`xoxp-`)으로 DM을 폴링하므로, 각 사용자별로 rate limit이 분리됩니다.

## 기능

- 기간별 채널 메시지 요약 (`/catchup 3d`)
- 특정 시점부터 요약 (`/catchup from:링크`)
- 쓰레드 포함 옵션 (`--threads`)
- 다중 채널 지원 (`--channels:#ch1,#ch2`)
- 리액션/답글 기반 중요도 판단
- 구조화된 요약 (액션 필요, 의사결정, 공지, 주요 논의)

---

## Part 1. Slack App 설정 (관리자 1회)

### 1. Slack App 생성

1. [api.slack.com/apps](https://api.slack.com/apps) 접속
2. "Create New App" → "From scratch"
3. 앱 이름과 워크스페이스 선택

### 2. 권한 설정 (OAuth & Permissions)

**Bot Token Scopes:**
- `channels:history`, `channels:read`
- `chat:write`, `commands`
- `files:read`, `files:write`
- `groups:history`, `groups:read`
- `im:history`, `im:write`
- `users:read`, `reactions:read`

**User Token Scopes:**
- `im:history`, `im:read`
- `files:read`, `files:write`

### 3. Redirect URL 추가

OAuth & Permissions 페이지에서:
```
http://localhost:3001/callback
```

### 4. Socket Mode 활성화

1. "Socket Mode" 활성화
2. App-Level Token 생성 → `xapp-...` 토큰 저장

### 5. Event Subscriptions

Subscribe to bot events:
- `message.im`

### 6. 슬래시 커맨드 등록

- Command: `/catchup`
- Description: "채널 메시지 요약"

---

## Part 2. 중앙 봇 실행 (관리자 — 팀 서버)

팀 서버에서 **한 번만 실행**하면 됩니다. 전체 팀이 공유합니다.

```bash
# 1. 코드 클론
git clone <repo-url>
cd Slack_catchup_bot

# 2. .env 파일 생성
cp .env.example .env
```

`.env`에 아래 3개만 입력:
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
```

```bash
# 3. 실행
pip install -r requirements.txt
python app/main.py
```

또는 Docker:
```bash
docker-compose up -d
```

이 시점부터 `/catchup` 슬래시 커맨드가 작동합니다.
단, 로컬 워커가 없으면 JSON 파일만 DM에 올라오고 요약은 생성되지 않습니다.

---

## Part 3. 로컬 워커 설정 (각 사용자 — 본인 PC)

### Step 1. 사전 준비

```bash
# 코드 클론
git clone <repo-url>
cd Slack_catchup_bot

# Python 가상환경
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Claude Code CLI 설치 및 로그인 (처음 1회)
claude --version
claude
```

### Step 2. 유저 토큰 발급 (본인 PC에서 1회)

`.env` 파일에 `SLACK_CLIENT_ID`와 `SLACK_CLIENT_SECRET`를 입력합니다.
(관리자에게 받으세요)

```
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
```

OAuth 서버를 실행합니다:
```bash
python app/oauth_server.py
```

브라우저에서 `http://localhost:3001/start` 접속:
1. Slack 인증 페이지에서 "허용" 클릭
2. 화면에 표시된 `SLACK_USER_TOKEN`과 `SLACK_USER_ID`를 복사
3. OAuth 서버는 `Ctrl+C`로 종료 (다시 쓸 일 없음)

### Step 3. .env 설정

```
SLACK_BOT_TOKEN=xoxb-...        # 관리자에게 받은 봇 토큰
SLACK_USER_TOKEN=xoxp-...       # Step 2에서 발급받은 토큰
SLACK_USER_ID=U0...             # Step 2에서 확인한 ID
```

### Step 4. 워커 실행

```bash
python app/worker.py
```

워커가 실행되면 5초 간격으로 DM을 확인합니다.
Slack에서 `/catchup`을 실행하면, 워커가 자동으로 요약을 생성하여 DM으로 보내줍니다.

워커는 **요약을 받고 싶을 때만 켜놓으면** 됩니다.
꺼져 있을 때 요청한 파일은 DM에 남아있고, 워커를 다시 켜면 자동 처리됩니다.

---

## 사용법

```
/catchup              # 도움말
/catchup 3d           # 최근 3일 요약
/catchup 12h          # 최근 12시간 요약
/catchup 1w           # 최근 1주일 요약
/catchup from:링크    # 특정 메시지 이후 요약
/catchup clear        # 봇 DM 내 메시지/파일 전체 삭제

# 옵션
--threads             # 쓰레드 내용 포함
--include-bots        # 봇 메시지 포함
--channels:#a,#b      # 다중 채널 지정
```

## 출력 예시

```
📬 #backend 요약 (2024-01-15 09:00 ~ 2024-01-18 14:30)

🔴 액션 필요
- 배포 전 코드 리뷰 요청됨 (@HK 멘션) [원본↗]

📌 의사결정 사항
- Redis TTL 30분→1시간으로 변경 확정 [원본↗]

📢 공지/변경
- 금요일 배포 → 목요일로 변경 [원본↗]

💬 주요 논의
- 모델 경량화 방안 논의 중 [원본↗]
```

## 라이선스

MIT
