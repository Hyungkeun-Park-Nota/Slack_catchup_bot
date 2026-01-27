import os
import sys
import json
import time
import logging
import requests
import tempfile
from typing import Optional, Tuple, List
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from catchup import Message, CatchupResult
from summarizer import Summarizer

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # 초


class CatchupWorker:
    """로컬 워커: DM 폴링 → 파일 감지 → Claude CLI 요약 → DM 전송"""

    def __init__(self):
        self.bot_token = os.environ.get("SLACK_BOT_TOKEN")
        self.user_token = os.environ.get("SLACK_USER_TOKEN")
        self.user_id = os.environ.get("SLACK_USER_ID")

        if not self.user_token:
            logger.error("SLACK_USER_TOKEN 환경변수가 설정되지 않았습니다. OAuth 인증을 먼저 진행하세요.")
            sys.exit(1)
        if not self.bot_token:
            logger.error("SLACK_BOT_TOKEN 환경변수가 설정되지 않았습니다.")
            sys.exit(1)
        if not self.user_id:
            logger.error("SLACK_USER_ID 환경변수가 설정되지 않았습니다.")
            sys.exit(1)

        # 유저 토큰: DM 폴링용 (rate limit 분리)
        self.user_client = WebClient(token=self.user_token)
        # 봇 토큰: DM 전송, 파일 삭제용
        self.bot_client = WebClient(token=self.bot_token)
        self.summarizer = Summarizer()
        self._processed_files = set()
        self._dm_channel: Optional[str] = None

    def _get_dm_channel(self) -> str:
        """봇과 사용자 간 DM 채널 ID 조회"""
        if self._dm_channel:
            return self._dm_channel
        response = self.bot_client.conversations_open(users=[self.user_id])
        self._dm_channel = response['channel']['id']
        return self._dm_channel

    def _send_dm(self, text: str) -> str:
        """사용자에게 DM 전송 (봇 토큰 사용). 메시지 ts를 반환한다."""
        try:
            dm_channel = self._get_dm_channel()
            result = self.bot_client.chat_postMessage(
                channel=dm_channel,
                text=text,
                mrkdwn=True,
                unfurl_links=False,
                unfurl_media=False,
            )
            return result.get("ts", "")
        except Exception as e:
            logger.error(f"Failed to send DM: {e}")
            return ""

    def _delete_dm(self, message_ts: str):
        """DM 메시지 삭제"""
        if not message_ts:
            return
        try:
            dm_channel = self._get_dm_channel()
            self.bot_client.chat_delete(channel=dm_channel, ts=message_ts)
        except Exception as e:
            logger.warning(f"Failed to delete DM message: {e}")

    def _cleanup_status_messages(self):
        """DM에서 봇의 중간 상태 메시지(수집 완료 등)를 삭제"""
        status_keywords = (
            "메시지를 수집하고 요약하는 중",
            "메시지 수집 완료",
            "쓰레드 수집 완료",
            "Claude로 요약을 생성하는 중",
        )
        try:
            dm_channel = self._get_dm_channel()
            result = self.user_client.conversations_history(
                channel=dm_channel,
                limit=20,
            )
            for msg in result.get("messages", []):
                text = msg.get("text", "")
                if any(kw in text for kw in status_keywords):
                    self._delete_dm(msg["ts"])
        except Exception as e:
            logger.warning(f"Failed to cleanup status messages: {e}")

    def _poll_dm_files(self) -> list[dict]:
        """DM에서 catchup_data_*.json 파일 검색 (유저 토큰 사용 → rate limit 분리)"""
        try:
            dm_channel = self._get_dm_channel()
            result = self.user_client.conversations_history(
                channel=dm_channel,
                limit=20
            )

            found_files = []
            for msg in result.get('messages', []):
                files = msg.get('files', [])
                for f in files:
                    name = f.get('name', '')
                    file_id = f.get('id', '')
                    if (name.startswith('catchup_data_')
                            and name.endswith('.json')
                            and file_id not in self._processed_files):
                        found_files.append(f)

            return found_files

        except SlackApiError as e:
            logger.error(f"DM polling error: {e.response['error']}")
            return []

    def _download_file(self, file_info: dict) -> Optional[str]:
        """Slack 파일 다운로드 → 임시파일 경로 반환 (유저 토큰 사용)"""
        url = file_info.get('url_private_download') or file_info.get('url_private')
        if not url:
            logger.error(f"No download URL for file {file_info.get('id')}")
            return None

        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.user_token}"},
                timeout=30
            )
            resp.raise_for_status()

            with tempfile.NamedTemporaryFile(
                mode='wb', suffix='.json', delete=False, prefix='catchup_dl_'
            ) as f:
                f.write(resp.content)
                return f.name

        except Exception as e:
            logger.error(f"File download error: {e}")
            return None

    def _parse_catchup_json(self, filepath: str) -> Optional[Tuple[dict, List[CatchupResult]]]:
        """JSON 파일 → (request_info, CatchupResult 리스트) 역직렬화"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if data.get('type') != 'catchup_data':
                logger.warning(f"Unknown file type: {data.get('type')}")
                return None

            request_info = data.get('request', {})
            results = []

            for ch_data in data.get('channels', []):
                messages = []
                for m in ch_data.get('messages', []):
                    thread_msgs = []
                    for tm in m.get('thread_messages', []):
                        thread_msgs.append(Message(
                            ts=tm['ts'],
                            user=tm['user'],
                            user_name=tm['user_name'],
                            text=tm['text'],
                            channel=tm['channel'],
                            channel_name=tm['channel_name'],
                            permalink=tm.get('permalink', ''),
                            reply_count=tm.get('reply_count', 0),
                            reaction_count=tm.get('reaction_count', 0),
                            is_bot=tm.get('is_bot', False),
                            thread_messages=[]
                        ))

                    messages.append(Message(
                        ts=m['ts'],
                        user=m['user'],
                        user_name=m['user_name'],
                        text=m['text'],
                        channel=m['channel'],
                        channel_name=m['channel_name'],
                        permalink=m.get('permalink', ''),
                        reply_count=m.get('reply_count', 0),
                        reaction_count=m.get('reaction_count', 0),
                        is_bot=m.get('is_bot', False),
                        thread_messages=thread_msgs
                    ))

                results.append(CatchupResult(
                    channel_name=ch_data['channel_name'],
                    messages=messages,
                    start_time=ch_data['start_time'],
                    end_time=ch_data['end_time'],
                    total_count=ch_data['total_count'],
                    error=ch_data.get('error')
                ))

            return request_info, results

        except Exception as e:
            logger.error(f"JSON parse error: {e}")
            return None

    def _delete_slack_file(self, file_id: str):
        """Slack에서 파일 삭제 (봇 토큰 사용)"""
        try:
            self.bot_client.files_delete(file=file_id)
            logger.info(f"Deleted Slack file: {file_id}")
        except SlackApiError as e:
            logger.warning(f"Failed to delete Slack file {file_id}: {e.response['error']}")

    def _process_file(self, file_info: dict):
        """파일 처리: 다운로드 → 파싱 → 요약 → DM 전송 → 정리"""
        file_id = file_info['id']
        filename = file_info.get('name', 'unknown')
        logger.info(f"Processing file: {filename} ({file_id})")

        # 중복 처리 방지
        self._processed_files.add(file_id)

        # 다운로드
        local_path = self._download_file(file_info)
        if not local_path:
            self._send_dm(f"❌ 파일 다운로드 실패: {filename}")
            return

        try:
            # 파싱
            parsed = self._parse_catchup_json(local_path)
            if not parsed:
                self._send_dm(f"❌ 데이터 파일 파싱 실패: {filename}")
                return

            request_info, results = parsed
            logger.info(f"Parsed {len(results)} channel(s) from {filename}")

            # 요약 생성
            progress_ts = self._send_dm("⏳ Claude로 요약을 생성하는 중입니다...")

            if len(results) == 1:
                summary = self.summarizer.summarize(results[0])
            else:
                summary = self.summarizer.summarize_multiple(results)

            # 중간 상태 메시지 삭제 (수집 완료 + 요약 생성중)
            self._delete_dm(progress_ts)
            self._cleanup_status_messages()

            # 요약 DM 전송
            self._send_dm(summary)
            logger.info(f"Summary sent for {filename}")

            # Slack 파일 삭제
            self._delete_slack_file(file_id)

        finally:
            # 로컬 임시파일 삭제
            try:
                os.unlink(local_path)
            except OSError:
                pass

    def run(self):
        """메인 폴링 루프"""
        logger.info(f"Catchup Worker started (user: {self.user_id})")
        logger.info(f"Polling interval: {POLL_INTERVAL}s")

        # 시작 시 미처리 파일 체크
        logger.info("Checking for unprocessed files...")
        files = self._poll_dm_files()
        if files:
            logger.info(f"Found {len(files)} unprocessed file(s)")
            for f in files:
                self._process_file(f)
        else:
            logger.info("No unprocessed files found")

        # 폴링 루프
        while True:
            try:
                time.sleep(POLL_INTERVAL)
                files = self._poll_dm_files()
                for f in files:
                    self._process_file(f)
            except KeyboardInterrupt:
                logger.info("Worker stopped by user")
                break
            except Exception as e:
                logger.error(f"Polling loop error: {e}")
                time.sleep(POLL_INTERVAL)


def main():
    worker = CatchupWorker()
    worker.run()


if __name__ == "__main__":
    main()
