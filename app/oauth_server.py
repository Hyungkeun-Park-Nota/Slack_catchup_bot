"""
Slack OAuth 서버 (HTTPS)
사용자가 브라우저에서 인증하면 xoxp- 유저 토큰을 발급받아 표시합니다.
워커의 .env에 SLACK_USER_TOKEN으로 설정하면 됩니다.

실행: python app/oauth_server.py
브라우저에서: https://localhost:3001/start
(자체 서명 인증서를 사용하므로 브라우저 경고가 뜨면 무시하고 진행)
"""

import os
import ssl
import json
import logging
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
OAUTH_PORT = int(os.environ.get("OAUTH_PORT", 3001))
REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", f"https://localhost:{OAUTH_PORT}/callback")

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "certs")
CERT_FILE = os.path.join(CERT_DIR, "localhost.pem")
KEY_FILE = os.path.join(CERT_DIR, "localhost-key.pem")

# 워커가 DM 폴링에 필요한 최소 스코프
USER_SCOPES = "im:history,im:read,files:read,files:write"


class OAuthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/start":
            self._handle_start()
        elif parsed.path == "/callback":
            self._handle_callback(parsed)
        else:
            self._respond(200, "Catchup Bot OAuth Server\n\n/start 로 이동하여 인증을 시작하세요.")

    def _handle_start(self):
        """Slack OAuth 인증 페이지로 리다이렉트"""
        if not CLIENT_ID:
            self._respond(500, "SLACK_CLIENT_ID 환경변수가 설정되지 않았습니다.")
            return

        params = urlencode({
            "client_id": CLIENT_ID,
            "user_scope": USER_SCOPES,
            "redirect_uri": REDIRECT_URI,
        })
        auth_url = f"https://slack.com/oauth/v2/authorize?{params}"

        self.send_response(302)
        self.send_header("Location", auth_url)
        self.end_headers()
        logger.info("Redirecting to Slack OAuth")

    def _handle_callback(self, parsed):
        """OAuth 콜백: code를 토큰으로 교환"""
        params = parse_qs(parsed.query)

        error = params.get("error", [None])[0]
        if error:
            self._respond(400, f"인증 실패: {error}")
            return

        code = params.get("code", [None])[0]
        if not code:
            self._respond(400, "인증 코드가 없습니다.")
            return

        # 토큰 교환
        resp = requests.post("https://slack.com/api/oauth.v2.access", data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }, timeout=10)

        data = resp.json()

        if not data.get("ok"):
            self._respond(400, f"토큰 교환 실패: {data.get('error', 'unknown')}")
            return

        authed_user = data.get("authed_user", {})
        user_token = authed_user.get("access_token", "")
        user_id = authed_user.get("id", "")

        if not user_token:
            self._respond(400, "유저 토큰을 받지 못했습니다.")
            return

        logger.info(f"Token issued for user: {user_id}")

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Catchup Bot - 인증 완료</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }}
.token-box {{ background: #f4f4f4; padding: 15px; border-radius: 8px; word-break: break-all; font-family: monospace; font-size: 14px; }}
.env-box {{ background: #1a1a2e; color: #0f0; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 14px; white-space: pre; }}
h1 {{ color: #333; }}
</style></head>
<body>
<h1>Catchup Bot 인증 완료</h1>
<p>아래 내용을 워커 PC의 <code>.env</code> 파일에 추가하세요:</p>
<div class="env-box">SLACK_USER_TOKEN={user_token}
SLACK_USER_ID={user_id}</div>
<br>
<p>설정 후 워커를 실행하면 됩니다:</p>
<div class="env-box">python app/worker.py</div>
<br>
<p><strong>이 토큰은 다시 표시되지 않습니다.</strong> 지금 복사해주세요.</p>
</body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _respond(self, status, message):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")


def _ensure_certs():
    """자체 서명 인증서가 없으면 생성"""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return

    os.makedirs(CERT_DIR, exist_ok=True)
    logger.info("자체 서명 인증서를 생성합니다...")

    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", KEY_FILE,
        "-out", CERT_FILE,
        "-days", "365",
        "-nodes",
        "-subj", "/CN=localhost"
    ], check=True, capture_output=True)

    logger.info(f"인증서 생성 완료: {CERT_DIR}")


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        logger.error("SLACK_CLIENT_ID, SLACK_CLIENT_SECRET 환경변수를 설정하세요.")
        return

    _ensure_certs()

    server = HTTPServer(("0.0.0.0", OAUTH_PORT), OAuthHandler)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)

    logger.info(f"OAuth server running on https://localhost:{OAUTH_PORT}")
    logger.info(f"브라우저에서 https://localhost:{OAUTH_PORT}/start 로 접속하세요.")
    logger.info("(자체 서명 인증서이므로 브라우저 경고가 뜨면 '고급' → '계속 진행')")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("OAuth server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
