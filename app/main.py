import os
import time
import logging
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from parser import parse_command, get_help_message
from catchup import MessageCollector, format_messages_for_summary
from summarizer import Summarizer

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Slack 앱 초기화
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

# 요약기 초기화
summarizer = Summarizer()


@app.command("/catchup")
def handle_catchup(ack, command, client, logger):
    """슬래시 커맨드 핸들러"""
    # 즉시 응답 (3초 제한)
    ack()
    
    user_id = command['user_id']
    channel_id = command['channel_id']
    text = command.get('text', '').strip()
    
    logger.info(f"Catchup command from {user_id} in {channel_id}: {text}")
    
    # 커맨드 파싱
    cmd = parse_command(text)
    
    # 헬프 출력
    if cmd.is_help:
        send_dm(client, user_id, get_help_message())
        return
    
    # 파싱 에러
    if cmd.error:
        send_dm(client, user_id, f"❌ {cmd.error}\n\n{get_help_message()}")
        return
    
    # 처리 시작 알림
    send_dm(client, user_id, "⏳ 메시지를 수집하고 요약하는 중입니다...")
    
    # 메시지 수집기 초기화
    collector = MessageCollector(client)
    
    # 대상 채널 결정
    if cmd.channels:
        target_channels = []
        for ch_name in cmd.channels:
            ch_id = collector.get_channel_id_by_name(ch_name)
            if ch_id:
                target_channels.append(ch_id)
            else:
                send_dm(client, user_id, f"⚠️ 채널을 찾을 수 없습니다: #{ch_name}")
        if not target_channels:
            return
    else:
        target_channels = [channel_id]
    
    # 시간 범위 결정
    now = time.time()
    if cmd.from_timestamp:
        oldest = float(cmd.from_timestamp)
        # from: 링크의 채널 사용
        if cmd.from_channel:
            target_channels = [cmd.from_channel]
    else:
        oldest = now - cmd.duration_seconds
    
    # 각 채널별 메시지 수집 및 요약
    results = []
    for ch_id in target_channels:
        result = collector.collect_messages(
            channel_id=ch_id,
            oldest=oldest,
            latest=now,
            include_threads=cmd.include_threads,
            include_bots=cmd.include_bots
        )
        results.append(result)
    
    # 요약 생성
    if len(results) == 1:
        summary = summarizer.summarize(results[0])
    else:
        summary = summarizer.summarize_multiple(results)
    
    # DM으로 전송
    send_dm(client, user_id, summary)
    
    logger.info(f"Catchup summary sent to {user_id}")


def send_dm(client, user_id: str, message: str):
    """사용자에게 DM 전송"""
    try:
        # DM 채널 열기
        response = client.conversations_open(users=[user_id])
        dm_channel = response['channel']['id']
        
        # 메시지 전송
        client.chat_postMessage(
            channel=dm_channel,
            text=message,
            mrkdwn=True
        )
    except Exception as e:
        logger.error(f"Failed to send DM to {user_id}: {e}")


@app.event("app_mention")
def handle_mention(event, say):
    """앱 멘션 핸들러 (옵션)"""
    say("안녕하세요! `/catchup` 명령어로 채널 요약을 받아보세요.")


@app.event("message")
def handle_message(event, logger):
    """메시지 이벤트 핸들러 (로깅용)"""
    # 필요시 추가 로직
    pass


def main():
    """메인 실행 함수"""
    # Socket Mode 사용 시 (개발용)
    if os.environ.get("SLACK_APP_TOKEN"):
        handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        logger.info("Starting Catchup Bot in Socket Mode...")
        handler.start()
    else:
        # HTTP 모드 (프로덕션)
        port = int(os.environ.get("PORT", 3000))
        logger.info(f"Starting Catchup Bot on port {port}...")
        app.start(port=port)


if __name__ == "__main__":
    main()
