import time
import logging
from dataclasses import dataclass
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Slack 메시지 정보"""
    ts: str                          # 타임스탬프 (메시지 ID)
    user: str                        # 사용자 ID
    user_name: str                   # 사용자 이름
    text: str                        # 메시지 본문
    channel: str                     # 채널 ID
    channel_name: str                # 채널 이름
    permalink: str                   # 원본 링크
    reply_count: int = 0             # 답글 수
    reaction_count: int = 0          # 리액션 수
    importance_score: int = 0        # 중요도 점수
    is_bot: bool = False             # 봇 메시지 여부
    thread_messages: list = None     # 쓰레드 메시지들

    def __post_init__(self):
        if self.thread_messages is None:
            self.thread_messages = []
        # 중요도 점수 계산
        self.importance_score = self.reply_count * 2 + self.reaction_count


@dataclass  
class CatchupResult:
    """Catchup 결과"""
    channel_name: str
    messages: list[Message]
    start_time: str
    end_time: str
    total_count: int
    error: Optional[str] = None


class MessageCollector:
    """Slack 메시지 수집기"""
    
    MAX_MESSAGES_PER_CHANNEL = 500  # 채널당 최대 메시지 수
    
    def __init__(self, client: WebClient):
        self.client = client
        self._user_cache = {}
        self._channel_cache = {}
    
    def get_user_name(self, user_id: str) -> str:
        """사용자 ID로 이름 조회 (캐싱)"""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        
        try:
            result = self.client.users_info(user=user_id)
            name = result['user']['real_name'] or result['user']['name']
            self._user_cache[user_id] = name
            return name
        except SlackApiError:
            return user_id
    
    def get_channel_name(self, channel_id: str) -> str:
        """채널 ID로 이름 조회 (캐싱)"""
        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]
        
        try:
            result = self.client.conversations_info(channel=channel_id)
            name = result['channel']['name']
            self._channel_cache[channel_id] = name
            return name
        except SlackApiError:
            return channel_id
    
    def get_channel_id_by_name(self, channel_name: str) -> Optional[str]:
        """채널 이름으로 ID 조회"""
        try:
            # 퍼블릭 채널 목록 조회
            result = self.client.conversations_list(types="public_channel,private_channel")
            for channel in result['channels']:
                if channel['name'] == channel_name:
                    return channel['id']
            return None
        except SlackApiError:
            return None
    
    def get_permalink(self, channel: str, ts: str) -> str:
        """메시지 퍼머링크 생성"""
        try:
            result = self.client.chat_getPermalink(channel=channel, message_ts=ts)
            return result['permalink']
        except SlackApiError:
            return ""
    
    def collect_messages(
        self,
        channel_id: str,
        oldest: float,
        latest: float = None,
        include_threads: bool = False,
        include_bots: bool = False
    ) -> CatchupResult:
        """채널에서 메시지 수집
        
        Args:
            channel_id: 채널 ID
            oldest: 시작 타임스탬프 (Unix timestamp)
            latest: 종료 타임스탬프 (기본: 현재)
            include_threads: 쓰레드 포함 여부
            include_bots: 봇 메시지 포함 여부
        """
        if latest is None:
            latest = time.time()
        
        channel_name = self.get_channel_name(channel_id)
        messages = []
        
        try:
            # 메시지 히스토리 조회
            cursor = None
            logger.info(f"Fetching messages: channel={channel_id}, oldest={oldest} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest))}), latest={latest} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(latest))})")
            while True:
                result = self.client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest),
                    latest=str(latest),
                    limit=200,
                    cursor=cursor
                )
                logger.info(f"API response ok={result.get('ok')}, has_more={result.get('has_more')}")

                raw_messages = result.get('messages', [])
                logger.info(f"API returned {len(raw_messages)} raw messages for {channel_id}")

                for msg in raw_messages:
                    # 봇 메시지 필터링
                    is_bot = msg.get('bot_id') is not None or msg.get('subtype') == 'bot_message'
                    if is_bot and not include_bots:
                        logger.debug(f"Skipping bot message: {msg.get('text', '')[:50]}")
                        continue

                    # 서브타입 메시지 스킵 (채널 입장/퇴장 등)
                    if msg.get('subtype') in ['channel_join', 'channel_leave', 'channel_topic']:
                        logger.debug(f"Skipping subtype: {msg.get('subtype')}")
                        continue
                    
                    user_id = msg.get('user', 'unknown')
                    
                    message = Message(
                        ts=msg['ts'],
                        user=user_id,
                        user_name=self.get_user_name(user_id) if not is_bot else msg.get('username', 'Bot'),
                        text=msg.get('text', ''),
                        channel=channel_id,
                        channel_name=channel_name,
                        permalink=self.get_permalink(channel_id, msg['ts']),
                        reply_count=msg.get('reply_count', 0),
                        reaction_count=sum(r.get('count', 0) for r in msg.get('reactions', [])),
                        is_bot=is_bot
                    )
                    
                    # 쓰레드 수집
                    if include_threads and msg.get('reply_count', 0) > 0:
                        message.thread_messages = self._collect_thread(
                            channel_id, msg['ts'], include_bots
                        )
                    
                    messages.append(message)
                
                # 페이지네이션
                if not result.get('has_more', False):
                    break
                cursor = result.get('response_metadata', {}).get('next_cursor')
                
                # 최대 메시지 수 제한
                if len(messages) >= self.MAX_MESSAGES_PER_CHANNEL:
                    break
            
            # 시간순 정렬 (오래된 순)
            messages.sort(key=lambda m: float(m.ts))
            
            return CatchupResult(
                channel_name=channel_name,
                messages=messages,
                start_time=time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest)),
                end_time=time.strftime('%Y-%m-%d %H:%M', time.localtime(latest)),
                total_count=len(messages)
            )
            
        except SlackApiError as e:
            return CatchupResult(
                channel_name=channel_name,
                messages=[],
                start_time="",
                end_time="",
                total_count=0,
                error=f"Slack API 오류: {e.response['error']}"
            )
    
    def _collect_thread(
        self, 
        channel_id: str, 
        thread_ts: str,
        include_bots: bool
    ) -> list[Message]:
        """쓰레드 메시지 수집"""
        thread_messages = []
        
        try:
            result = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=100
            )
            
            # 첫 번째 메시지(부모)는 제외
            for msg in result.get('messages', [])[1:]:
                is_bot = msg.get('bot_id') is not None
                if is_bot and not include_bots:
                    continue
                
                user_id = msg.get('user', 'unknown')
                
                thread_messages.append(Message(
                    ts=msg['ts'],
                    user=user_id,
                    user_name=self.get_user_name(user_id) if not is_bot else msg.get('username', 'Bot'),
                    text=msg.get('text', ''),
                    channel=channel_id,
                    channel_name=self.get_channel_name(channel_id),
                    permalink=self.get_permalink(channel_id, msg['ts']),
                    is_bot=is_bot
                ))
                
        except SlackApiError:
            pass
        
        return thread_messages


def format_messages_for_summary(result: CatchupResult) -> str:
    """요약을 위해 메시지를 텍스트로 포맷팅"""
    lines = []
    lines.append(f"채널: #{result.channel_name}")
    lines.append(f"기간: {result.start_time} ~ {result.end_time}")
    lines.append(f"총 메시지 수: {result.total_count}")
    lines.append("---")
    
    for msg in result.messages:
        importance = ""
        if msg.importance_score >= 5:
            importance = "[중요] "
        
        lines.append(f"{importance}[{msg.user_name}]: {msg.text}")
        lines.append(f"  (답글: {msg.reply_count}, 리액션: {msg.reaction_count})")
        
        # 쓰레드 내용
        for thread_msg in msg.thread_messages:
            lines.append(f"  └─ [{thread_msg.user_name}]: {thread_msg.text}")
        
        lines.append("")
    
    return "\n".join(lines)
