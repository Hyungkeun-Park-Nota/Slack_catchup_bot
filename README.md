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

- 기간별 채널 메시지 요약 (`/catchup 3d`, `12h`, `1w`)
- 특정 시점/날짜부터 요약 (`/catchup from:<링크>`, `from:<YYYY-MM-DD>`)
- 시간 범위 지정 (`from:<시작> to:<끝>`)
- 특정 스레드만 요약 (`/catchup in:<링크>`)
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
- `channels:history`, `channels:read`, `channels:join`
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

### 사전 준비

1. 관리자에게 아래 3가지 값을 받으세요:
   - `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_BOT_TOKEN`

2. Claude Code CLI 설치 및 로그인 (처음 1회):
   ```bash
   claude --version   # 설치 확인
   claude             # 최초 로그인
   ```

### 자동 설정 (권장)

```bash
git clone <repo-url>
cd Slack_catchup_bot

# .env 파일 생성 후 관리자에게 받은 값 입력
cp .env.example .env
# SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_BOT_TOKEN 입력

# 자동 설정 실행
python setup_worker.py
```

5단계 자동 실행:
1. Python 3.9+, Claude CLI 확인
2. 가상환경 생성 + 의존성 설치
3. `.env` 필수 값 검증
4. OAuth 서버 실행 → 브라우저 인증 → 토큰 `.env` 자동 저장
5. 워커 실행 여부 확인

> Step 4에서 브라우저가 열리면 **인증서 경고 → '고급' → '계속 진행'** 후 **Slack에서 "허용"** 을 클릭하세요.

### 수동 설정

<details>
<summary>자동 설정이 안 되는 경우 펼쳐보세요</summary>

```bash
# 1. 환경 준비
git clone <repo-url>
cd Slack_catchup_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. .env 설정 (관리자에게 받은 값 입력)
cp .env.example .env
# SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_BOT_TOKEN 입력

# 3. OAuth 토큰 발급
python app/oauth_server.py --auto-save
# 브라우저에서 https://localhost:3001/start 접속
# → 인증서 경고 무시 → Slack에서 "허용" 클릭
# → .env에 SLACK_USER_TOKEN, SLACK_USER_ID 자동 저장

# 4. 워커 실행
python app/worker.py
```

`--auto-save` 없이 실행하면 토큰이 화면에 표시되며 직접 `.env`에 복사해야 합니다.

</details>

### 워커 사용법

워커는 **요약을 받고 싶을 때만 켜놓으면** 됩니다.
꺼져 있을 때 요청한 파일은 DM에 남아있고, 워커를 다시 켜면 자동 처리됩니다.

```bash
python app/worker.py
```

---

## 채널 접근 권한

| 채널 유형 | 동작 |
|-----------|------|
| **퍼블릭 채널** | `/catchup` 실행 시 봇이 자동 참여 (채널에 메시지 안 남음) |
| **프라이빗 채널** | 봇을 먼저 초대해야 합니다: 채널에서 `/invite @Nota Catchup Bot` 실행 |

---

## 사용법

```
/catchup                            # 도움말
/catchup 3d                         # 최근 3일 요약
/catchup 12h                        # 최근 12시간 요약
/catchup 1w                         # 최근 1주일 요약
/catchup from:<링크|YYYY-MM-DD>     # 특정 시점 이후 ~ 현재까지 요약
/catchup from:<시작> to:<끝>        # 시작~끝 범위 요약 (링크 또는 날짜)
/catchup 3d to:<YYYY-MM-DD>         # 지정 날짜 기준 최근 3일 요약
/catchup in:<링크>                  # 특정 스레드만 요약 (--threads 불필요)
/catchup clear                      # 봇 DM 내 메시지/파일 전체 삭제

# 옵션
--threads                           # 쓰레드 내용 포함 (기간 요약 시)
--exclude-bots                      # 봇 메시지 제외
--channels:#a,#b                    # 다중 채널 지정
```

## 출력 예시

```
📬 #backend 요약 (2026-01-26 09:00 ~ 2026-01-29 14:30)

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
