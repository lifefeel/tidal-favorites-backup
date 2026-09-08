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
  SKIP=tracks,albums   건너뛸 항목 (tracks, albums, artists, playlists, my_playlists)
"""
import json, os, sys, time
from pathlib import Path
import urllib.request, urllib.parse, urllib.error

from tidal_auth import get_token

TOKEN = get_token()
API = os.environ.get("TIDAL_API", "https://api.tidal.com/v1")
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "tidal_backup")
DRY = os.environ.get("DRY_RUN") == "1"
SKIP = set(filter(None, os.environ.get("SKIP", "").split(",")))
BATCH = 50
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
        else:
            print(f"  {i+len(chunk)}/{len(ids)}")
        time.sleep(0.5)


sess = call("GET", "sessions")
if "_error" in sess:
    sys.exit(f"세션 확인 실패: {sess}")
UID, CC = sess["userId"], sess.get("countryCode", "KR")
print(f"복원 대상 userId={UID} country={CC} (source={SRC})")

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
