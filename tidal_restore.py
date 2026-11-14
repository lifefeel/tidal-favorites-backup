#!/usr/bin/env python3
"""
Tidal 즐겨찾기 복원 스크립트 (tidal_backup.py 출력 폴더 기준)
사용법:
  python3 tidal_restore.py [백업폴더]   # 기본값 ./tidal_backup
  처음 실행하면 브라우저 로그인이 열립니다. 반드시 **복원할 계정**으로 로그인하세요.
  (다른 계정으로 바꾸려면 먼저 python3 tidal_auth.py --logout)

  앱에서 뽑은 토큰을 직접 쓰려면: export TIDAL_TOKEN='eyJ...' 
옵션 환경변수:
  DRY_RUN=1        실제 요청 없이 무엇을 할지 출력만
  YES=1            계정 확인 프롬프트를 건너뜀 (자동화용)
  SKIP=tracks,albums   건너뛸 항목 (tracks, albums, artists, playlists, my_playlists)
  BATCH=50         한 번에 추가할 개수 (기본 1 — 최근 추가순을 원본과 같게 유지)
"""
import json, os, sys, time
from pathlib import Path
import urllib.request, urllib.parse, urllib.error

from tidal_auth import get_token, account_label

TOKEN = get_token()
API = os.environ.get("TIDAL_API", "https://api.tidal.com/v1")
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "tidal_backup")
DRY = os.environ.get("DRY_RUN") == "1"
SKIP = set(filter(None, os.environ.get("SKIP", "").split(",")))
# 한 번에 여러 개를 추가하면 모두 같은 '추가 시각'을 받아 앱의 최근 추가순이 뒤섞인다.
# 원래 순서를 지키려고 기본은 1개씩 추가한다. 순서가 상관없으면 BATCH=50 으로 빠르게.
BATCH = max(1, int(os.environ.get("BATCH", "1") or 1))
HEADERS = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "Mozilla/5.0"}


def call(method, path, data=None, extra=None, **params):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    h = dict(HEADERS, **(extra or {}))
    if body:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt); continue
            return {"_error": e.code, "_body": e.read().decode(errors="ignore")}
    return {"_error": "retry-exceeded"}


def load(name):
    p = SRC / f"{name}.json"
    if not p.exists():
        print(f"  ({name}.json 없음, 건너뜀)"); return []
    entries = json.loads(p.read_text())
    # 백업 시 최신순(DATE DESC)이었으므로 뒤집어서 오래된 것부터 추가 → 상대 순서 유지
    return list(reversed(entries))


def item_of(e):
    return e.get("item", e)


def post_batches(label, path, key, ids):
    print(f"[{label}] {len(ids)}개")
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        if DRY:
            print(f"  DRY POST {path} {key}={len(chunk)}개"); continue
        r = call("POST", path, {key: ",".join(map(str, chunk)), "onArtifactNotFound": "SKIP"}, countryCode=CC)
        if "_error" in r:
            print(f"  실패 {i}-{i+len(chunk)}: {r['_error']} {r.get('_body','')[:200]}")
        elif (i + len(chunk)) % 50 == 0 or i + len(chunk) == len(ids):
            print(f"  {i+len(chunk)}/{len(ids)}")
        time.sleep(0.5 if BATCH > 1 else 0.3)


sess = call("GET", "sessions")
if "_error" in sess:
    sys.exit(f"세션 확인 실패: {sess}")
UID, CC = sess["userId"], sess.get("countryCode", "KR")
print(f"복원 대상: {account_label(API, TOKEN, UID)} country={CC}")
print(f"복원 원본: {SRC.resolve()}")

# 복원은 되돌리기 어려우므로 쓰기 전에 계정을 확인받는다
if not DRY:
    if os.environ.get("YES") == "1":
        print("YES=1 이므로 확인 없이 진행합니다.")
    elif not sys.stdin.isatty():
        sys.exit("확인 입력을 받을 수 없습니다. 위 계정이 맞다면 YES=1 을 붙여 다시 실행하세요.")
    elif input("이 계정에 복원합니다. 계속할까요? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("취소했습니다.")

if "tracks" not in SKIP:
    post_batches("tracks", f"users/{UID}/favorites/tracks", "trackIds",
                 [item_of(e)["id"] for e in load("tracks")])

if "albums" not in SKIP:
    post_batches("albums", f"users/{UID}/favorites/albums", "albumIds",
                 [item_of(e)["id"] for e in load("albums")])

if "artists" not in SKIP:
    post_batches("artists", f"users/{UID}/favorites/artists", "artistIds",
                 [item_of(e)["id"] for e in load("artists")])

if "playlists" not in SKIP:
    # 남의 플레이리스트 즐겨찾기 — uuid로 다시 팔로우
    post_batches("favorite playlists", f"users/{UID}/favorites/playlists", "uuids",
                 [item_of(e)["uuid"] for e in load("playlists")])

if "my_playlists" not in SKIP:
    # 내가 만든 플레이리스트 — 새로 생성 후 트랙 채우기
    pl_dir = SRC / "playlist_tracks"
    mine = load("my_playlists")
    print(f"[my playlists] {len(mine)}개")
    for e in mine:
        pl = item_of(e)
        title, uuid = pl["title"], pl["uuid"]
        matches = list(pl_dir.glob(f"*_{uuid[:8]}.json"))
        # 백업이 INDEX ASC(원래 곡 순서)로 저장하므로 파일 순서 그대로 추가한다.
        # load()처럼 뒤집으면 안 된다 — 여기서 뒤집으면 역순 플레이리스트가 된다.
        tracks = json.loads(matches[0].read_text()) if matches else []
        ids = [item_of(t)["id"] for t in tracks if item_of(t).get("id")]
        print(f"  '{title}' ({len(ids)}곡)")
        if DRY:
            continue
        created = call("POST", f"users/{UID}/playlists",
                       {"title": title, "description": pl.get("description", "") or ""}, countryCode=CC)
        if "_error" in created:
            print(f"    생성 실패: {created}"); continue
        new_uuid = created["uuid"]
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            r = call("POST", f"playlists/{new_uuid}/items",
                     {"trackIds": ",".join(map(str, chunk)), "onArtifactNotFound": "SKIP", "onDupes": "SKIP"},
                     extra={"If-None-Match": "*"}, countryCode=CC)
            if "_error" in r:
                print(f"    트랙 추가 실패 {i}: {r['_error']} {r.get('_body','')[:200]}")
            time.sleep(0.5)

print("\n완료")
