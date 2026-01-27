#!/usr/bin/env python3
"""
Catchup Bot 워커 자동 설정 스크립트

OAuth 토큰 발급 → .env 설정 → Worker 실행까지 자동화합니다.

사용법:
    python setup_worker.py
"""

import os
import sys
import shutil
import subprocess
import webbrowser
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
ENV_EXAMPLE = os.path.join(PROJECT_ROOT, ".env.example")
REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirements.txt")
VENV_DIR = os.path.join(PROJECT_ROOT, "venv")
OAUTH_SERVER = os.path.join(PROJECT_ROOT, "app", "oauth_server.py")
WORKER_SCRIPT = os.path.join(PROJECT_ROOT, "app", "worker.py")


def _print_step(step: int, title: str):
    print(f"\n{'='*50}")
    print(f"  [{step}/5] {title}")
    print(f"{'='*50}\n")


def _print_ok(msg: str):
    print(f"  [OK] {msg}")


def _print_fail(msg: str):
    print(f"  [FAIL] {msg}")


def _print_warn(msg: str):
    print(f"  [WARN] {msg}")


def _read_env_value(key: str) -> str:
    """
    .env 파일에서 특정 키의 값을 읽는다.
    """
    if not os.path.exists(ENV_FILE):
        return ""
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                return line[len(key) + 1:]
    return ""


# ─────────────────────────────────────────────
# Step 1: 사전 요구사항 확인
# ─────────────────────────────────────────────
def step1_check_prerequisites() -> bool:
    _print_step(1, "사전 요구사항 확인")

    # Python 버전 확인
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 9):
        _print_fail(f"Python 3.9 이상이 필요합니다. (현재: {v.major}.{v.minor}.{v.micro})")
        return False
    _print_ok(f"Python {v.major}.{v.minor}.{v.micro}")

    # Claude CLI 확인
    claude_path = shutil.which("claude")
    if claude_path:
        _print_ok(f"Claude CLI: {claude_path}")
        print("  (최초 사용 시 'claude' 명령어로 로그인이 필요합니다)")
    else:
        _print_warn("Claude CLI를 찾을 수 없습니다.")
        _print_warn("워커는 실행되지만 요약 생성이 실패합니다.")
        _print_warn("설치 후 'claude' 명령어로 로그인하세요.")
        _print_warn("설치: https://docs.anthropic.com/en/docs/claude-code")

    return True


# ─────────────────────────────────────────────
# Step 2: venv 생성 + 의존성 설치
# ─────────────────────────────────────────────
def step2_setup_venv() -> bool:
    _print_step(2, "가상환경 및 의존성 설치")

    # venv 생성
    if os.path.exists(VENV_DIR):
        _print_ok("가상환경이 이미 존재합니다 (venv/)")
    else:
        print("  가상환경 생성 중...")
        result = subprocess.run(
            [sys.executable, "-m", "venv", VENV_DIR],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _print_fail(f"venv 생성 실패: {result.stderr}")
            return False
        _print_ok("가상환경 생성 완료 (venv/)")

    # pip 경로 결정
    if sys.platform == "win32":
        pip_path = os.path.join(VENV_DIR, "Scripts", "pip")
    else:
        pip_path = os.path.join(VENV_DIR, "bin", "pip")

    # 의존성 설치
    print("  의존성 설치 중...")
    result = subprocess.run(
        [pip_path, "install", "-r", REQUIREMENTS],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        _print_fail(f"의존성 설치 실패: {result.stderr}")
        return False
    _print_ok("의존성 설치 완료")

    return True


# ─────────────────────────────────────────────
# Step 3: .env 파일 확인 및 필수 값 검증
# ─────────────────────────────────────────────
def step3_validate_env() -> bool:
    _print_step(3, ".env 파일 확인")

    # .env 파일 존재 확인
    if not os.path.exists(ENV_FILE):
        if os.path.exists(ENV_EXAMPLE):
            print("  .env 파일이 없습니다. .env.example에서 복사합니다.")
            shutil.copy2(ENV_EXAMPLE, ENV_FILE)
            _print_ok(".env.example → .env 복사 완료")
        else:
            _print_fail(".env 파일과 .env.example 모두 없습니다.")
            _print_fail("프로젝트 루트에 .env 파일을 생성해주세요.")
            return False
    else:
        _print_ok(".env 파일 존재")

    # 필수 값 검증
    required_keys = {
        "SLACK_CLIENT_ID": "OAuth 인증에 필요 (관리자에게 받으세요)",
        "SLACK_CLIENT_SECRET": "OAuth 인증에 필요 (관리자에게 받으세요)",
        "SLACK_BOT_TOKEN": "워커 DM 전송에 필요 (관리자에게 받으세요)",
    }

    missing = []
    for key, desc in required_keys.items():
        val = _read_env_value(key)
        placeholder_prefixes = ("your-", "xoxb-your", "xapp-your", "xoxp-your")
        if not val or any(val.startswith(p) for p in placeholder_prefixes):
            missing.append((key, desc))
        else:
            _print_ok(f"{key} = {'***' if 'TOKEN' in key or 'SECRET' in key else val}")

    if missing:
        print()
        _print_fail("다음 환경변수가 설정되지 않았습니다:")
        for key, desc in missing:
            print(f"    {key}  — {desc}")
        print()
        print("  .env 파일을 편집한 후 이 스크립트를 다시 실행하세요.")
        print(f"    파일 위치: {ENV_FILE}")
        return False

    return True


# ─────────────────────────────────────────────
# Step 4: OAuth 서버 실행 → 토큰 자동 저장
# ─────────────────────────────────────────────
def step4_oauth_token() -> bool:
    _print_step(4, "OAuth 유저 토큰 발급")

    # 이미 유효한 토큰이 있는지 확인
    existing_token = _read_env_value("SLACK_USER_TOKEN")
    if existing_token and existing_token.startswith("xoxp-") and existing_token != "xoxp-your-user-token":
        _print_ok("SLACK_USER_TOKEN이 이미 설정되어 있습니다.")
        answer = input("  기존 토큰을 유지하시겠습니까? (Y/n): ").strip().lower()
        if answer != "n":
            return True

    # Python 실행 경로 결정 (venv 우선)
    if sys.platform == "win32":
        python_path = os.path.join(VENV_DIR, "Scripts", "python")
    else:
        python_path = os.path.join(VENV_DIR, "bin", "python")

    if not os.path.exists(python_path):
        python_path = sys.executable

    print("  OAuth 서버를 --auto-save 모드로 시작합니다.")
    print("  브라우저에서 아래 단계를 직접 수행해주세요:")
    print("    1) 인증서 경고 → '고급' → '계속 진행' 클릭")
    print("    2) Slack 인증 페이지에서 '허용' 클릭")
    print("  완료되면 토큰이 .env에 자동 저장됩니다.")
    print()

    # OAuth 서버 실행
    proc = subprocess.Popen(
        [python_path, OAUTH_SERVER, "--auto-save"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 서버 시작 대기
    time.sleep(2)

    # 브라우저 열기
    url = "https://localhost:3001/start"
    print(f"  브라우저를 엽니다: {url}")
    print("  (자체 서명 인증서 경고가 뜨면 '고급' → '계속 진행')")
    print()
    webbrowser.open(url)

    # 서버 종료 대기
    print("  인증 완료를 기다리는 중... (Ctrl+C로 취소)")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print()
        _print_warn("OAuth 인증이 취소되었습니다.")
        return False

    # 토큰 저장 확인
    saved_token = _read_env_value("SLACK_USER_TOKEN")
    if saved_token and saved_token.startswith("xoxp-") and saved_token != "xoxp-your-user-token":
        _print_ok("유저 토큰이 .env에 저장되었습니다.")
        return True
    else:
        _print_fail("토큰 저장을 확인할 수 없습니다.")
        _print_fail("수동으로 python app/oauth_server.py 를 실행해주세요.")
        return False


# ─────────────────────────────────────────────
# Step 5: 워커 실행
# ─────────────────────────────────────────────
def step5_run_worker():
    _print_step(5, "워커 실행")

    answer = input("  지금 워커를 실행하시겠습니까? (Y/n): ").strip().lower()
    if answer == "n":
        print()
        print("  나중에 워커를 실행하려면:")
        print("    source venv/bin/activate")
        print("    python app/worker.py")
        return

    # Python 실행 경로 결정
    if sys.platform == "win32":
        python_path = os.path.join(VENV_DIR, "Scripts", "python")
    else:
        python_path = os.path.join(VENV_DIR, "bin", "python")

    if not os.path.exists(python_path):
        python_path = sys.executable

    print()
    print("  워커를 시작합니다. (종료: Ctrl+C)")
    print("  Slack에서 /catchup 명령어를 사용해보세요.")
    print()

    try:
        subprocess.run([python_path, WORKER_SCRIPT])
    except KeyboardInterrupt:
        print("\n  워커가 종료되었습니다.")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    print()
    print("  Catchup Bot 워커 자동 설정")
    print("  ─────────────────────────")
    print()

    if not step1_check_prerequisites():
        sys.exit(1)

    if not step2_setup_venv():
        sys.exit(1)

    if not step3_validate_env():
        sys.exit(1)

    if not step4_oauth_token():
        sys.exit(1)

    step5_run_worker()

    print()
    print("  설정이 완료되었습니다!")
    print()


if __name__ == "__main__":
    main()
