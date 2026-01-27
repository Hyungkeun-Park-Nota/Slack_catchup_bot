import os
import json
import time
import tempfile
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from parser import parse_command, get_help_message
from catchup import MessageCollector, CatchupResult

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


@app.command("/catchup")
def handle_catchup(ack, command, client, logger):
    """슬래시 커맨드 핸들러"""
    # 즉시 응답 (3초 제한)
    ack()
    
    user_id = command['user_id']
    channel_id = command['channel_id']
    text = command.get('text', '').strip()
    
    logger.info(f"Catchup command from {user_id} in {channel_id}: {text}")

    # clear 커맨드 처리
    if text.lower() == "clear":
        clear_dm(client, user_id)
        return

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
    
    # JSON 생성 및 파일 업로드
    catchup_json = build_catchup_json(
        user_id=user_id,
        command_text=text,
        results=results
    )

    success = upload_catchup_file(client, user_id, catchup_json)
    if success:
        send_dm(client, user_id, "✅ 메시지 수집 완료! 데이터 파일을 업로드했습니다. 로컬 워커가 요약을 생성합니다.")
    else:
        send_dm(client, user_id, "❌ 데이터 파일 업로드에 실패했습니다.")

    logger.info(f"Catchup data file uploaded for {user_id}")


def build_catchup_json(user_id: str, command_text: str, results: list[CatchupResult]) -> dict:
    """CatchupResult 리스트를 JSON dict로 변환"""
    channels = []
    for result in results:
        messages_data = []
        for msg in result.messages:
            thread_data = []
            for tmsg in msg.thread_messages:
                thread_data.append({
                    "ts": tmsg.ts,
                    "user": tmsg.user,
                    "user_name": tmsg.user_name,
                    "text": tmsg.text,
                    "channel": tmsg.channel,
                    "channel_name": tmsg.channel_name,
                    "permalink": tmsg.permalink,
                    "reply_count": tmsg.reply_count,
                    "reaction_count": tmsg.reaction_count,
                    "importance_score": tmsg.importance_score,
                    "is_bot": tmsg.is_bot,
                    "thread_messages": []
                })
            messages_data.append({
                "ts": msg.ts,
                "user": msg.user,
                "user_name": msg.user_name,
                "text": msg.text,
                "channel": msg.channel,
                "channel_name": msg.channel_name,
                "permalink": msg.permalink,
                "reply_count": msg.reply_count,
                "reaction_count": msg.reaction_count,
                "importance_score": msg.importance_score,
                "is_bot": msg.is_bot,
                "thread_messages": thread_data
            })
        channels.append({
            "channel_name": result.channel_name,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "total_count": result.total_count,
            "error": result.error,
            "messages": messages_data
        })

    return {
        "version": "1.0",
        "type": "catchup_data",
        "request": {
            "user_id": user_id,
            "command_text": command_text,
            "requested_at": datetime.now(timezone.utc).isoformat()
        },
        "channels": channels
    }


def upload_catchup_file(client, user_id: str, catchup_json: dict) -> bool:
    """JSON 데이터를 임시파일로 저장 후 DM에 업로드"""
    try:
        # DM 채널 열기
        response = client.conversations_open(users=[user_id])
        dm_channel = response['channel']['id']

        timestamp = int(time.time())
        filename = f"catchup_data_{user_id}_{timestamp}.json"

        # 임시파일에 JSON 저장
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, prefix='catchup_'
        ) as f:
            json.dump(catchup_json, f, ensure_ascii=False, indent=2)
            tmp_path = f.name

        try:
            client.files_upload_v2(
                channel=dm_channel,
                file=tmp_path,
                filename=filename,
                title=f"Catchup Data ({filename})"
            )
            return True
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Failed to upload catchup file for {user_id}: {e}")
        return False


def clear_dm(client, user_id: str):
    """DM 채널의 봇 메시지와 파일을 모두 삭제"""
    try:
        response = client.conversations_open(users=[user_id])
        dm_channel = response['channel']['id']

        deleted_msgs = 0
        deleted_files = 0
        cursor = None

        while True:
            result = client.conversations_history(
                channel=dm_channel,
                limit=100,
                cursor=cursor
            )

            messages = result.get('messages', [])
            if not messages:
                break

            for msg in messages:
                # 파일 삭제
                for f in msg.get('files', []):
                    try:
                        client.files_delete(file=f['id'])
                        deleted_files += 1
                    except Exception:
                        pass

                # 봇이 보낸 메시지 삭제
                try:
                    client.chat_delete(channel=dm_channel, ts=msg['ts'])
                    deleted_msgs += 1
                except Exception:
                    pass

            if not result.get('has_more', False):
                break
            cursor = result.get('response_metadata', {}).get('next_cursor')

        logger.info(f"DM cleared for {user_id}: {deleted_msgs} messages, {deleted_files} files")

    except Exception as e:
        logger.error(f"Failed to clear DM for {user_id}: {e}")


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
