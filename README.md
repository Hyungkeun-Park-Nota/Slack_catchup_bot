# Catchup Bot

Slack 채널 메시지를 AI로 요약해서 DM으로 전달하는 봇입니다.

## 기능

- 기간별 채널 메시지 요약 (`/catchup 3d`)
- 특정 시점부터 요약 (`/catchup from:링크`)
- 쓰레드 포함 옵션 (`--threads`)
- 다중 채널 지원 (`--channels:#ch1,#ch2`)
- 리액션/답글 기반 중요도 판단
- 구조화된 요약 (액션 필요, 의사결정, 공지, 주요 논의)

## 요구사항

- Python 3.9+
- Claude Code CLI (로그인 필요)
- Slack App 토큰

## 설치 및 실행

### 1. Slack App 생성

1. [api.slack.com/apps](https://api.slack.com/apps) 접속
2. "Create New App" → "From scratch"
3. 앱 이름과 워크스페이스 선택

### 2. 권한 설정 (OAuth & Permissions)

Bot Token Scopes에 추가:
- `channels:history` - 채널 메시지 읽기
- `channels:read` - 채널 정보 조회
- `chat:write` - 메시지 전송
- `commands` - 슬래시 커맨드
- `groups:history` - 프라이빗 채널 히스토리
- `groups:read` - 프라이빗 채널 정보
- `im:write` - DM 전송
- `users:read` - 사용자 정보 조회
- `reactions:read` - 리액션 조회

### 3. Socket Mode 활성화

1. Slack App 설정에서 "Socket Mode" 활성화
2. App-Level Token 생성 (이름: 아무거나)
3. 생성된 `xapp-...` 토큰 저장

### 4. 슬래시 커맨드 등록

Slash Commands에서:
- Command: `/catchup`
- Request URL: `https://placeholder.com` (Socket Mode에서는 사용 안함)
- Description: "채널 메시지 요약"

### 5. 환경변수 설정
```bash
cp .env.example .env
```

`.env` 파일 수정:
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
```

### 6. Claude Code CLI 준비
```bash
# 설치 확인
claude --version

# 로그인 (처음 한번)
claude
```

### 7. 로컬 실행
```bash
# 가상환경 생성
python3 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 실행
python app/main.py
```

## 사용법
```
/catchup              # 도움말
/catchup 3d           # 최근 3일 요약
/catchup 12h          # 최근 12시간 요약
/catchup 1w           # 최근 1주일 요약
/catchup from:링크    # 특정 메시지 이후 요약

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

## 서버 배포

서버에서 실행하려면:
1. 서버에 Claude Code CLI 설치 및 로그인
2. 봇을 백그라운드로 실행 (nohup, systemd, screen 등)
```bash
# 예: nohup 사용
nohup python app/main.py > catchup.log 2>&1 &
```

## 라이선스

MIT