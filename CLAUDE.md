# Catchup Bot

Slack 채널 메시지를 AI(Claude CLI)로 요약해서 DM으로 전달하는 봇.

## 아키텍처

**중앙 봇 + 로컬 워커** 구조.

```
사용자 → /catchup 3d → [중앙 봇] 메시지 수집 → JSON 파일 DM 업로드
                         [로컬 워커] DM에서 파일 감지 → Claude CLI 요약 → DM 전송
```

- 중앙 봇은 팀 서버에서 상시 실행
- 로컬 워커는 각 사용자 PC에서 필요할 때 실행
- 워커는 개인 유저 토큰(`xoxp-`)으로 DM 폴링 → rate limit 분리

## 파일 구조

```
app/
  main.py          - 중앙 봇 (Slack Bolt, Socket Mode)
                     /catchup 슬래시 커맨드 수신, 메시지 수집, JSON 파일 DM 업로드
                     Bolt OAuth 모드 충돌 방지를 위해 CLIENT_ID/SECRET을 환경에서 제거 후 초기화
  worker.py        - 로컬 워커
                     DM 폴링(5초 간격), 파일 감지, Claude CLI 요약, 결과 DM 전송
                     요약 완료 후 중간 상태 메시지 자동 삭제
  oauth_server.py  - OAuth 서버 (HTTPS, 자체 서명 인증서)
                     유저 토큰(xoxp-) 발급. --auto-save 플래그로 .env 자동 저장 지원
  catchup.py       - 데이터 모델 및 메시지 수집기
                     Message/CatchupResult 데이터클래스, MessageCollector
                     퍼블릭 채널은 conversations.join으로 자동 참여, 프라이빗은 /invite 안내
                     프라이빗 채널 멤버십 체크, 쓰레드 단위 수집(collect_thread)
  summarizer.py    - Claude Code CLI 기반 요약기
                     메시지 컨텍스트 구성, claude CLI 호출, 구조화된 요약 생성
  parser.py        - /catchup 커맨드 파서
                     기간(3d/12h/1w), from:링크/날짜, to:링크/날짜, in:링크, --threads, --channels 옵션 파싱

setup_worker.py    - 워커 자동 설정 스크립트 (5단계)
Dockerfile         - 중앙 봇용 Docker 이미지
requirements.txt   - Python 의존성 (slack-bolt, slack-sdk, requests, python-dotenv)
```

## 환경변수 (.env)

| 변수 | 용도 | 필요 위치 |
|------|------|-----------|
| `SLACK_BOT_TOKEN` | 봇 토큰 (xoxb-) | 봇 + 워커 |
| `SLACK_APP_TOKEN` | Socket Mode 토큰 (xapp-) | 봇 |
| `SLACK_SIGNING_SECRET` | 요청 검증 | 봇 |
| `SLACK_CLIENT_ID` | OAuth 앱 ID | OAuth 서버 |
| `SLACK_CLIENT_SECRET` | OAuth 앱 시크릿 | OAuth 서버 |
| `SLACK_USER_TOKEN` | 유저 토큰 (xoxp-) | 워커 |
| `SLACK_USER_ID` | 유저 ID | 워커 |

## 설정 방법

### 사전 준비 (유저가 직접 해야 할 것)

아래 항목은 자동화할 수 없으므로, `setup_worker.py` 실행 **전에** 준비해야 합니다.

1. **관리자에게 3가지 값 받기** (Slack App 관리자에게 요청)
   - `SLACK_CLIENT_ID` — OAuth 앱 ID
   - `SLACK_CLIENT_SECRET` — OAuth 앱 시크릿
   - `SLACK_BOT_TOKEN` — 봇 토큰 (xoxb-)

2. **`.env` 파일에 위 3개 값 직접 입력**
   - `.env` 파일이 없으면 `setup_worker.py`가 `.env.example`에서 자동 복사
   - 값은 유저가 직접 편집기로 입력해야 함

3. **Claude Code CLI 설치 및 로그인** (처음 1회)
   ```bash
   # 설치 확인
   claude --version
   # 최초 로그인 (대화형)
   claude
   ```
   - CLI가 없으면 워커는 실행되지만 요약 생성이 실패함

4. **OAuth 인증 시 브라우저 조작** (setup_worker.py Step 4에서 발생)
   - 자체 서명 인증서 경고 → '고급' → '계속 진행' 클릭
   - Slack 인증 페이지에서 **"허용"** 클릭

### 자동 설정 (권장)

위 사전 준비를 마친 뒤:

```bash
python setup_worker.py
```

5단계 자동 실행: 요구사항 확인 → venv/의존성 → .env 검증 → OAuth 토큰 발급 → 워커 실행

### 수동 설정

```bash
# 1. 환경 준비
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. .env 설정 (관리자에게 받은 값 입력)
cp .env.example .env
# SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_BOT_TOKEN 직접 입력

# 3. OAuth 토큰 발급
python app/oauth_server.py              # 수동 모드 (토큰 화면 표시)
python app/oauth_server.py --auto-save  # 자동 모드 (.env 자동 저장)
# 브라우저에서 https://localhost:3001/start 접속
# → 인증서 경고 무시 → Slack에서 "허용" 클릭

# 4. 워커 실행
python app/worker.py
```

## 자주 쓰는 명령어

```bash
# 중앙 봇 실행
python app/main.py

# 워커 실행
python app/worker.py

# OAuth 토큰 발급 (수동)
python app/oauth_server.py

# OAuth 토큰 발급 (자동 저장)
python app/oauth_server.py --auto-save

# Docker (중앙 봇)
docker-compose up -d
```

### Slack 슬래시 커맨드

```
/catchup 3d                          # 최근 3일 요약
/catchup 12h --threads               # 최근 12시간, 쓰레드 포함
/catchup 1w --channels:#backend      # 최근 1주, 특정 채널
/catchup from:<메시지_링크>           # 특정 메시지 이후 요약
/catchup from:<YYYY-MM-DD>           # 특정 날짜 이후 요약
/catchup from:<시작> to:<끝>         # 시간 범위 지정 (링크 또는 날짜)
/catchup 3d to:<YYYY-MM-DD>         # 지정 날짜 기준 역산
/catchup in:<메시지_링크>             # 특정 쓰레드만 요약
/catchup clear                       # 봇 DM 메시지/파일 전체 삭제
```

## 주의사항

### Bolt OAuth 모드 충돌
- `.env`에 `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET`이 있으면 Bolt가 자동으로 OAuth 모드로 전환됨
- `main.py`에서 봇 초기화 전에 해당 변수를 `os.environ`에서 제거하여 해결
- 이 변수들은 `oauth_server.py`에서만 사용됨

### 채널 접근 권한
- **퍼블릭 채널**: `conversations.join` API로 자동 참여 (채널에 시스템 메시지 안 남음, `channels:join` 스코프 필요)
- **프라이빗 채널**: `/invite @Nota Catchup Bot`으로 수동 초대 필요
- 프라이빗 채널에서 초대 없이 `/catchup` 실행 시 DM으로 `/invite` 안내 메시지 전달
- **프라이빗 채널 멤버십 체크**: 사용자가 본인이 속하지 않은 프라이빗 채널의 메시지를 수집하지 못하도록 차단. `conversations_members` API로 멤버 확인

### DM 메시지 동작
- 링크 프리뷰(unfurl) 비활성화: `unfurl_links=False`, `unfurl_media=False`
- 중간 상태 메시지("수집 중", "수집 완료", "요약 생성 중")는 요약 완료 후 자동 삭제
- 최종 요약 메시지만 DM에 남음

## 트러블슈팅

### OAuth 서버가 시작되지 않음
- `SLACK_CLIENT_ID`와 `SLACK_CLIENT_SECRET`이 .env에 설정되어 있는지 확인
- 포트 3001이 사용 중인지 확인: `lsof -i :3001`

### 브라우저 인증서 경고
- 자체 서명 인증서 사용. '고급' → '계속 진행' 클릭
- 인증서는 `certs/` 디렉토리에 자동 생성됨

### /catchup 명령 시 "installation is no longer available" 에러
- `main.py`가 Bolt OAuth 모드로 전환된 경우 발생
- `main.py` 코드에서 `os.environ.pop("SLACK_CLIENT_ID")` 처리가 되어 있는지 확인
- 봇 프로세스 재시작

### "not_in_channel" 에러
- 봇이 해당 채널에 초대되지 않은 상태
- 채널에서 `/invite @Nota Catchup Bot` 실행 후 재시도

### 워커가 요약을 생성하지 않음
- `claude --version`으로 Claude CLI 설치 확인
- `SLACK_BOT_TOKEN`, `SLACK_USER_TOKEN`, `SLACK_USER_ID`가 .env에 설정되어 있는지 확인
- 워커 로그에서 에러 메시지 확인

### 토큰 교환 실패
- Slack App의 Redirect URL에 `https://localhost:3001/callback`이 등록되어 있는지 확인
- `SLACK_CLIENT_SECRET`이 정확한지 확인
