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
    
    def is_private_channel(self, channel_id: str) -> bool:
        """채널이 프라이빗인지 확인"""
        try:
            ch_info = self.client.conversations_info(channel=channel_id)
            return ch_info['channel'].get('is_private', False)
        except SlackApiError:
            return True  # 확인 실패 시 안전하게 프라이빗 취급

    def check_user_membership(self, channel_id: str, user_id: str) -> bool:
        """사용자가 채널의 멤버인지 확인 (페이지네이션 지원)"""
        try:
            cursor = None
            while True:
                result = self.client.conversations_members(
                    channel=channel_id,
                    limit=200,
                    cursor=cursor
                )
                if user_id in result.get('members', []):
                    return True
                if not result.get('response_metadata', {}).get('next_cursor'):
                    break
                cursor = result['response_metadata']['next_cursor']
            return False
        except SlackApiError:
            return False  # 확인 실패 시 안전하게 비멤버 취급

    def collect_thread(
        self,
        channel_id: str,
        thread_ts: str,
        include_bots: bool = False
    ) -> CatchupResult:
        """특정 쓰레드의 메시지 수집 (부모 포함)

        Args:
            channel_id: 채널 ID
            thread_ts: 쓰레드 부모 메시지 타임스탬프
            include_bots: 봇 메시지 포함 여부
        """
        channel_name = self.get_channel_name(channel_id)
        messages = []

        try:
            cursor = None
            while True:
                result = self.client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=200,
                    cursor=cursor
                )

                for msg in result.get('messages', []):
                    is_bot = msg.get('bot_id') is not None or msg.get('subtype') == 'bot_message'
                    if is_bot and not include_bots:
                        continue

                    user_id = msg.get('user', 'unknown')

                    messages.append(Message(
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
                    ))

                if not result.get('has_more', False):
                    break
                cursor = result.get('response_metadata', {}).get('next_cursor')

            # 시간순 정렬
            messages.sort(key=lambda m: float(m.ts))

            start_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(float(messages[0].ts))) if messages else ""
            end_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(float(messages[-1].ts))) if messages else ""

            return CatchupResult(
                channel_name=channel_name,
                messages=messages,
                start_time=start_time,
                end_time=end_time,
                total_count=len(messages)
            )

        except SlackApiError as e:
            error_code = e.response['error']
            if error_code == 'thread_not_found':
                error_msg = "해당 쓰레드를 찾을 수 없습니다."
            elif error_code == 'not_in_channel':
                error_msg = f"채널 #{channel_name}에 봇이 초대되지 않았습니다. 채널에서 `/invite @Nota Catchup Bot`을 실행해주세요."
            else:
                error_msg = f"Slack API 오류: {error_code}"
            return CatchupResult(
                channel_name=channel_name,
                messages=[],
                start_time="",
                end_time="",
                total_count=0,
                error=error_msg
            )

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
            # 채널 진단 정보
            try:
                ch_info = self.client.conversations_info(channel=channel_id)
                ch = ch_info['channel']
                logger.info(
                    f"Channel info: id={channel_id}, name={ch.get('name')}, "
                    f"is_private={ch.get('is_private')}, is_member={ch.get('is_member')}, "
                    f"is_shared={ch.get('is_shared')}, is_ext_shared={ch.get('is_ext_shared')}, "
                    f"is_archived={ch.get('is_archived')}"
                )
            except Exception as e:
                logger.warning(f"Channel info failed for {channel_id}: {e}")

            # 메시지 히스토리 조회
            cursor = None
            logger.info(f"Fetching messages: channel={channel_id}, oldest={oldest} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest))}), latest={latest} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(latest))})")
            while True:
                try:
                    result = self.client.conversations_history(
                        channel=channel_id,
                        oldest=str(oldest),
                        latest=str(latest),
                        inclusive=True,
                        limit=200,
                        cursor=cursor
                    )
                except SlackApiError as e:
                    if e.response['error'] == 'not_in_channel':
                        # 퍼블릭 채널이면 자동 참여 (시스템 메시지 없음)
                        if self.is_private_channel(channel_id):
                            raise

                        logger.info(f"퍼블릭 채널 자동 참여: {channel_id}")
                        self.client.conversations_join(channel=channel_id)
                        result = self.client.conversations_history(
                            channel=channel_id,
                            oldest=str(oldest),
                            latest=str(latest),
                            inclusive=True,
                            limit=200,
                            cursor=cursor
                        )
                    else:
                        raise
                logger.info(f"API response ok={result.get('ok')}, has_more={result.get('has_more')}")

                raw_messages = result.get('messages', [])
                logger.info(f"API returned {len(raw_messages)} raw messages for {channel_id}")

                # 0건이면 시간 필터 없이 최근 메시지 진단 조회
                if not raw_messages and cursor is None:
                    try:
                        diag = self.client.conversations_history(channel=channel_id, limit=3)
                        diag_msgs = diag.get('messages', [])
                        logger.info(f"DIAG: {len(diag_msgs)} latest messages without time filter")
                        for dm in diag_msgs:
                            logger.info(
                                f"DIAG MSG: ts={dm.get('ts')}, subtype={dm.get('subtype')}, "
                                f"user={dm.get('user')}, bot_id={dm.get('bot_id')}, "
                                f"text={dm.get('text', '')[:80]!r}"
                            )
                    except Exception as e:
                        logger.warning(f"DIAG query failed: {e}")

                for msg in raw_messages:
                    # 메시지 raw 구조 로그 (디버그용)
                    logger.info(
                        f"MSG raw: subtype={msg.get('subtype')}, bot_id={msg.get('bot_id')}, "
                        f"user={msg.get('user')}, text={msg.get('text', '')[:80]!r}, "
                        f"has_attachments={bool(msg.get('attachments'))}, has_blocks={bool(msg.get('blocks'))}"
                    )

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
            error_code = e.response['error']
            if error_code == 'not_in_channel':
                error_msg = f"채널 #{channel_name}에 봇이 초대되지 않았습니다. 채널에서 `/invite @Nota Catchup Bot`을 실행해주세요."
            else:
                error_msg = f"Slack API 오류: {error_code}"
            return CatchupResult(
                channel_name=channel_name,
                messages=[],
                start_time="",
                end_time="",
                total_count=0,
                error=error_msg
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
