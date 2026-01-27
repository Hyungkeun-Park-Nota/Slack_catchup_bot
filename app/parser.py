import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class CatchupCommand:
    """파싱된 catchup 커맨드 정보"""
    is_help: bool = True
    duration: Optional[str] = None          # "3d", "12h", "1w"
    duration_seconds: Optional[int] = None  # 초 단위 변환값
    from_link: Optional[str] = None         # Slack 메시지 링크
    from_timestamp: Optional[str] = None    # 링크에서 추출한 타임스탬프
    from_channel: Optional[str] = None      # 링크에서 추출한 채널 ID
    include_threads: bool = False           # --threads 플래그
    include_bots: bool = False              # --include-bots 플래그
    channels: list[str] = None              # --channels 옵션
    error: Optional[str] = None             # 파싱 에러 메시지

    def __post_init__(self):
        if self.channels is None:
            self.channels = []


def parse_duration(duration_str: str) -> Optional[int]:
    """기간 문자열을 초 단위로 변환
    
    예: "3d" -> 259200, "12h" -> 43200, "1w" -> 604800
    """
    match = re.match(r'^(\d+)([hdw])$', duration_str.lower())
    if not match:
        return None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    multipliers = {
        'h': 3600,      # 1시간
        'd': 86400,     # 1일
        'w': 604800,    # 1주
    }
    
    return value * multipliers[unit]


def parse_slack_link(link: str) -> tuple[Optional[str], Optional[str]]:
    """Slack 메시지 링크에서 채널 ID와 타임스탬프 추출
    
    링크 형식: https://workspace.slack.com/archives/C0123ABC/p1234567890123456
    반환: (channel_id, timestamp) 또는 (None, None)
    """
    # 패턴: /archives/채널ID/p타임스탬프
    match = re.search(r'/archives/([A-Z0-9]+)/p(\d+)', link)
    if not match:
        return None, None
    
    channel_id = match.group(1)
    # Slack 타임스탬프는 p 뒤의 숫자에서 앞 10자리.뒤6자리 형식
    raw_ts = match.group(2)
    if len(raw_ts) >= 16:
        timestamp = f"{raw_ts[:10]}.{raw_ts[10:16]}"
    else:
        timestamp = raw_ts
    
    return channel_id, timestamp


def parse_channels(channels_str: str) -> list[str]:
    """채널 목록 문자열 파싱
    
    예: "#backend,#frontend" -> ["backend", "frontend"]
    """
    channels = []
    for ch in channels_str.split(','):
        ch = ch.strip().lstrip('#')
        if ch:
            channels.append(ch)
    return channels


def parse_command(text: str) -> CatchupCommand:
    """슬래시 커맨드 텍스트 파싱
    
    지원 형식:
    - /catchup (헬프)
    - /catchup 3d
    - /catchup 12h --threads
    - /catchup from:링크
    - /catchup 1w --channels:#backend,#frontend
    """
    text = text.strip()
    
    # 빈 입력 = 헬프
    if not text:
        return CatchupCommand(is_help=True)
    
    cmd = CatchupCommand(is_help=False)
    tokens = text.split()
    
    i = 0
    while i < len(tokens):
        token = tokens[i]
        
        # --threads 플래그
        if token == '--threads':
            cmd.include_threads = True
        
        # --include-bots 플래그
        elif token == '--include-bots':
            cmd.include_bots = True
        
        # --channels 옵션
        elif token.startswith('--channels:'):
            channels_str = token[len('--channels:'):]
            cmd.channels = parse_channels(channels_str)
        
        # from:링크 옵션
        elif token.startswith('from:'):
            link = token[len('from:'):]
            cmd.from_link = link
            channel_id, timestamp = parse_slack_link(link)
            if channel_id and timestamp:
                cmd.from_channel = channel_id
                cmd.from_timestamp = timestamp
            else:
                cmd.error = "잘못된 Slack 링크 형식입니다."
                return cmd
        
        # 기간 (3d, 12h, 1w 등)
        elif re.match(r'^\d+[hdw]$', token.lower()):
            cmd.duration = token.lower()
            cmd.duration_seconds = parse_duration(token)
            if cmd.duration_seconds is None:
                cmd.error = f"잘못된 기간 형식입니다: {token}"
                return cmd
        
        # 알 수 없는 토큰
        else:
            cmd.error = f"알 수 없는 옵션입니다: {token}"
            return cmd
        
        i += 1
    
    # 기간도 없고 from 링크도 없으면 헬프
    if cmd.duration is None and cmd.from_link is None:
        cmd.is_help = True
    
    return cmd


def get_help_message() -> str:
    """헬프 메시지 반환"""
    return """📖 *Catchup Bot 사용법*

*기본 명령어*
• `/catchup 3d` - 최근 3일간 메시지 요약
• `/catchup 12h` - 최근 12시간 메시지 요약
• `/catchup 1w` - 최근 1주일 메시지 요약
• `/catchup from:<링크>` - 해당 메시지 시점부터 현재까지 요약

*옵션*
• `--threads` - 쓰레드 내용 포함
• `--include-bots` - 봇 메시지 포함
• `--channels:#ch1,#ch2` - 다중 채널 지정

*예시*
```
/catchup 3d
/catchup 1w --threads
/catchup 3d --channels:#backend,#frontend
/catchup from:https://slack.com/archives/C0123/p1234567890
```"""
