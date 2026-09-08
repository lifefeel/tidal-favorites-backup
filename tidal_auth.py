#!/usr/bin/env python3
"""
Tidal 인증 공통 모듈 (tidal_backup.py / tidal_restore.py 가 함께 사용)

토큰을 구하는 순서:
  1. TIDAL_TOKEN 환경변수 — 앱에서 직접 뽑은 토큰 (mitmproxy 경로)
  2. 저장된 토큰 파일 — 만료가 가까우면 refresh token으로 자동 갱신
  3. 둘 다 없으면 기기 코드 로그인을 바로 시작

단독 실행하면 로그인만 수행합니다:
  python3 tidal_auth.py            # 로그인 후 토큰 저장
  python3 tidal_auth.py --logout   # 저장된 토큰 삭제
"""
import json, os, sys, time
from pathlib import Path
import urllib.request, urllib.parse, urllib.error

AUTH = "https://auth.tidal.com/v1/oauth2"
SCOPE = "r_usr w_usr w_sub"
# Tidal이 자사 TV·기기용 클라이언트에 넣어 배포하는 공개 자격증명입니다.
# 사용자별 비밀이 아니라 앱을 식별하는 고정값이며, 기기 코드 로그인에만 쓰입니다.
CLIENT_ID = "fX2JxdmntZWK0ixT"
CLIENT_SECRET = "1Nn9AfDAjxrgJFJbKNWLeAyKGVGmINuXPPLHVXAvxAg="

TOKEN_FILE = Path(os.environ.get("TIDAL_TOKEN_FILE", "tidal_auth.token"))
# 만료 10분 전부터는 미리 갱신한다 (긴 백업 도중 끊기지 않도록)
REFRESH_MARGIN = 600


def _post(path, **fields):
    """폼 인코딩 POST. (성공여부, 파싱된 JSON) 을 돌려준다."""
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"{AUTH}/{path}", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return False, json.loads(raw)
        except ValueError:
            return False, {"error": f"http_{e.code}", "body": raw[:200]}
    except urllib.error.URLError as e:
        sys.exit(f"네트워크 연결에 실패했습니다: {e.reason}")


def save_token(tok):
    TOKEN_FILE.write_text(json.dumps(tok, ensure_ascii=False, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)  # 다른 사용자가 읽지 못하도록
    except OSError:
        pass
    return tok


def load_token():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (ValueError, OSError):
        print(f"경고: {TOKEN_FILE} 을 읽을 수 없어 무시합니다.")
        return None


def _store(data):
    """토큰 응답을 저장 형식으로 변환한다."""
    user = data.get("user") or {}
    return save_token({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": int(time.time()) + int(data.get("expires_in", 0)),
        "user_id": user.get("userId"),
        "country_code": user.get("countryCode"),
        # 응답에 없을 수도 있으므로 있으면만 저장 — 어느 계정인지 보여 주는 용도
        "account": user.get("email") or user.get("username") or "",
    })


def describe(tok):
    """저장된 토큰이 어느 계정 것인지 사람이 읽을 수 있게."""
    return tok.get("account") or f"userId={tok.get('user_id')}" or "알 수 없음"


def device_login():
    """기기 코드 로그인. 브라우저에서 코드를 입력하면 토큰을 저장한다."""
    ok, start = _post("device_authorization", client_id=CLIENT_ID, scope=SCOPE)
    if not ok:
        sys.exit(f"로그인 시작 실패: {start}")

    url = start["verificationUriComplete"]
    print("\n" + "=" * 54)
    print("  브라우저에서 아래 주소를 열고 Tidal에 로그인하세요:")
    print(f"\n      https://{url}\n")
    print(f"  (주소창에 직접 입력할 경우: {start['verificationUri']}")
    print(f"   에 접속해 코드 {start['userCode']} 를 입력)")
    print("=" * 54 + "\n")

    # macOS 라면 브라우저를 대신 열어 준다
    if sys.platform == "darwin":
        os.system(f"open 'https://{url}' >/dev/null 2>&1")

    interval = int(start.get("interval", 2))
    deadline = time.time() + int(start.get("expiresIn", 300))
    print("로그인을 기다리는 중", end="", flush=True)
    while time.time() < deadline:
        time.sleep(interval)
        ok, res = _post("token", client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                        device_code=start["deviceCode"], scope=SCOPE,
                        grant_type="urn:ietf:params:oauth:grant-type:device_code")
        if ok:
            print(" 완료!")
            tok = _store(res)
            print(f"{describe(tok)} 계정으로 로그인했습니다.")
            print(f"토큰을 {TOKEN_FILE} 에 저장했습니다. 다음부터는 로그인 없이 실행됩니다.\n")
            return tok
        if res.get("error") == "expired_token":
            break
        print(".", end="", flush=True)  # authorization_pending — 계속 대기

    sys.exit("\n시간이 초과되었습니다. 스크립트를 다시 실행해 주세요.")


def refresh_token(tok):
    """refresh token 으로 access token 을 갱신한다. 실패하면 None."""
    if not tok.get("refresh_token"):
        return None
    ok, res = _post("token", client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                    refresh_token=tok["refresh_token"], grant_type="refresh_token",
                    scope=SCOPE)
    if not ok:
        return None
    res.setdefault("refresh_token", tok["refresh_token"])
    return _store(res)


def account_label(api, token, user_id):
    """users/{id} 에서 사람이 알아볼 수 있는 계정 이름을 얻는다. 실패하면 userId 만."""
    req = urllib.request.Request(
        f"{api}/users/{user_id}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            u = json.load(r)
        name = u.get("email") or u.get("username")
        return f"{name} (userId={user_id})" if name else f"userId={user_id}"
    except Exception:
        # 계정 이름은 편의 기능일 뿐이므로 실패해도 진행을 막지 않는다
        return f"userId={user_id}"


def get_token():
    """실제로 쓸 access token 문자열을 돌려준다. 없으면 로그인을 시작한다."""
    env = os.environ.get("TIDAL_TOKEN")
    if env:
        # 환경변수가 저장된 로그인을 덮어쓰므로 반드시 알린다 (엉뚱한 계정 사고 방지)
        print("TIDAL_TOKEN 환경변수의 토큰을 사용합니다 — 저장된 로그인보다 우선합니다.")
        print("  저장된 로그인을 쓰려면: unset TIDAL_TOKEN")
        env = env.strip()
        return env[7:].strip() if env.lower().startswith("bearer ") else env

    tok = load_token()
    if tok:
        # 이전 계정 토큰이 조용히 재사용되지 않도록 항상 누구인지 밝힌다
        print(f"저장된 로그인 사용: {describe(tok)}  (바꾸려면 python3 tidal_auth.py --logout)")
        if tok.get("expires_at", 0) - REFRESH_MARGIN > time.time():
            return tok["access_token"]
        print("토큰이 만료되어 갱신합니다...")
        fresh = refresh_token(tok)
        if fresh:
            return fresh["access_token"]
        print("갱신에 실패했습니다. 다시 로그인이 필요합니다.")

    return device_login()["access_token"]


if __name__ == "__main__":
    if "--logout" in sys.argv:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
            print(f"{TOKEN_FILE} 을 삭제했습니다.")
        else:
            print("저장된 토큰이 없습니다.")
    else:
        device_login()
