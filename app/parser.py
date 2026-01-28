import re
from dataclasses import dataclass
from datetime import datetime
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
    from_date: Optional[str] = None         # YYYY-MM-DD 형식 날짜
    to_link: Optional[str] = None           # to: Slack 메시지 링크
    to_timestamp: Optional[str] = None      # to: 링크/날짜에서 추출한 타임스탬프
    to_channel: Optional[str] = None        # to: 링크에서 추출한 채널 ID
    to_date: Optional[str] = None           # to: YYYY-MM-DD 형식 날짜
    in_link: Optional[str] = None           # in: Slack 메시지 링크 (쓰레드)
    in_timestamp: Optional[str] = None      # in: 링크에서 추출한 타임스탬프
    in_channel: Optional[str] = None        # in: 링크에서 추출한 채널 ID
    include_threads: bool = False           # --threads 플래그
    exclude_bots: bool = False             # --exclude-bots 플래그
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


def parse_date_to_timestamp(date_str: str) -> Optional[float]:
    """YYYY-MM-DD 날짜 문자열을 Unix timestamp로 변환 (로컬 00:00:00)

    예: "2026-01-20" -> 해당 날짜 00:00:00의 Unix timestamp
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.timestamp()
    except ValueError:
        return None


def parse_link_or_date(value: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Slack 링크 또는 YYYY-MM-DD 날짜를 파싱

    반환: (timestamp, channel_id, link, date_str)
    - 링크인 경우: (ts, channel, link, None)
    - 날짜인 경우: (ts, None, None, date_str)
    - 실패: (None, None, None, None)
    """
    # 먼저 Slack 링크 시도
    channel_id, timestamp = parse_slack_link(value)
    if channel_id and timestamp:
        return timestamp, channel_id, value, None

    # 날짜 시도
    ts = parse_date_to_timestamp(value)
    if ts is not None:
        return str(ts), None, None, value

    return None, None, None, None


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
        
        # --exclude-bots 플래그
        elif token == '--exclude-bots':
            cmd.exclude_bots = True
        
        # --channels 옵션
        elif token.startswith('--channels:'):
            channels_str = token[len('--channels:'):]
            cmd.channels = parse_channels(channels_str)
        
        # from:링크 또는 from:날짜 옵션
        elif token.startswith('from:'):
            value = token[len('from:'):]
            ts, ch, link, date_str = parse_link_or_date(value)
            if ts:
                cmd.from_timestamp = ts
                cmd.from_channel = ch
                cmd.from_link = link
                cmd.from_date = date_str
            else:
                cmd.error = "잘못된 from: 형식입니다. Slack 링크 또는 YYYY-MM-DD 날짜를 입력하세요."
                return cmd

        # to:링크 또는 to:날짜 옵션
        elif token.startswith('to:'):
            value = token[len('to:'):]
            ts, ch, link, date_str = parse_link_or_date(value)
            if ts:
                cmd.to_timestamp = ts
                cmd.to_channel = ch
                cmd.to_link = link
                cmd.to_date = date_str
            else:
                cmd.error = "잘못된 to: 형식입니다. Slack 링크 또는 YYYY-MM-DD 날짜를 입력하세요."
                return cmd

        # in:링크 옵션 (특정 쓰레드만 요약)
        elif token.startswith('in:'):
            link = token[len('in:'):]
            channel_id, timestamp = parse_slack_link(link)
            if channel_id and timestamp:
                cmd.in_link = link
                cmd.in_channel = channel_id
                cmd.in_timestamp = timestamp
            else:
                cmd.error = "잘못된 in: 링크 형식입니다. Slack 메시지 링크를 입력하세요."
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
    
    # in:은 from:, to:, 기간과 동시 사용 불가
    if cmd.in_link:
        if cmd.from_timestamp or cmd.to_timestamp or cmd.duration:
            cmd.error = "in: 옵션은 from:, to:, 기간과 함께 사용할 수 없습니다."
            return cmd
        return cmd

    # to:만 있고 from:/기간이 없으면 에러
    if cmd.to_timestamp and cmd.from_timestamp is None and cmd.duration is None:
        cmd.error = "to: 옵션은 from: 또는 기간과 함께 사용해야 합니다."
        return cmd

    # from: ≥ to: 이면 에러
    if cmd.from_timestamp and cmd.to_timestamp:
        if float(cmd.from_timestamp) >= float(cmd.to_timestamp):
            cmd.error = "from: 시점이 to: 시점보다 이전이어야 합니다."
            return cmd

    # 기간도 없고 from 링크/날짜도 없으면 헬프
    if cmd.duration is None and cmd.from_link is None and cmd.from_date is None:
        cmd.is_help = True

    return cmd


def get_help_message() -> str:
    """헬프 메시지 반환"""
    return """📖 *Catchup Bot 사용법*

*기본 명령어*
• `/catchup 3d` - 최근 3일간 메시지 요약
• `/catchup 12h` - 최근 12시간 메시지 요약
• `/catchup 1w` - 최근 1주일 메시지 요약

*시간 범위 지정*
• `/catchup from:<링크>` - 해당 메시지 시점부터 현재까지 요약
• `/catchup from:<YYYY-MM-DD>` - 해당 날짜부터 현재까지 요약
• `/catchup from:<시작> to:<끝>` - 시작~끝 범위 요약 (링크 또는 날짜)
• `/catchup 3d to:<YYYY-MM-DD>` - 지정 날짜 기준 최근 3일 요약

*쓰레드 요약*
• `/catchup in:<링크>` - 해당 메시지의 쓰레드만 요약

*옵션*
• `--threads` - 쓰레드 내용 포함
• `--exclude-bots` - 봇 메시지 제외
• `--channels:#ch1,#ch2` - 다중 채널 지정

*예시*
```
/catchup 3d
/catchup 1w --threads
/catchup 3d --channels:#backend,#frontend
/catchup from:https://slack.com/archives/C0123/p1234567890
/catchup from:2026-01-20 to:2026-01-25
/catchup in:https://slack.com/archives/C0123/p1234567890
```"""
