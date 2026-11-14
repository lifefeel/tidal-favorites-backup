#!/usr/bin/env python3
"""
Tidal 즐겨찾기 백업 스크립트
사용법:
  python3 tidal_backup.py            # 처음 실행하면 브라우저 로그인이 열립니다
출력: ./tidal_backup/ 아래에 JSON 원본 + CSV

웹 로그인이 불가능한 계정이라면 앱에서 뽑은 토큰을 직접 넘길 수 있습니다:
  export TIDAL_TOKEN='eyJ...'        # Bearer 접두어 제외
"""
import csv, json, os, sys, time
from pathlib import Path
import urllib.request, urllib.parse, urllib.error

from tidal_auth import get_token, account_label

TOKEN = get_token()

API = os.environ.get("TIDAL_API", "https://api.tidal.com/v1")
OUT = Path("tidal_backup"); OUT.mkdir(exist_ok=True)
HEADERS = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "Mozilla/5.0"}


def get(path, **params):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt); continue
            body = e.read().decode(errors="ignore")
            sys.exit(f"HTTP {e.code} on {url}\n{body}")
    sys.exit("재시도 초과")


# 1. 세션 정보 → userId, countryCode
sess = get("sessions")
uid, cc = sess["userId"], sess.get("countryCode", "KR")
print(f"백업 대상: {account_label(API, TOKEN, uid)} country={cc}")


def fetch_all(path, limit=50, order="DATE", direction="DESC"):
    items, offset = [], 0
    while True:
        page = get(path, countryCode=cc, limit=limit, offset=offset, order=order, orderDirection=direction)
        batch = page.get("items", [])
        items.extend(batch)
        total = page.get("totalNumberOfItems", 0)
        print(f"  {path}: {len(items)}/{total}")
        offset += limit
        if not batch or offset >= total:
            return items
        time.sleep(0.3)


def artists(obj):
    return ", ".join(a["name"] for a in obj.get("artists", [])) or obj.get("artist", {}).get("name", "")


TARGETS = {
    "tracks": (f"users/{uid}/favorites/tracks",
               ["id", "title", "artists", "album", "duration", "isrc", "added"],
               lambda it: [it["id"], it["title"], artists(it), it.get("album", {}).get("title"), it.get("duration"), it.get("isrc"), None]),
    "albums": (f"users/{uid}/favorites/albums",
               ["id", "title", "artists", "releaseDate", "numberOfTracks", "upc", "added"],
               lambda it: [it["id"], it["title"], artists(it), it.get("releaseDate"), it.get("numberOfTracks"), it.get("upc"), None]),
    "artists": (f"users/{uid}/favorites/artists",
                ["id", "name", "added"],
                lambda it: [it["id"], it["name"], None]),
    "playlists": (f"users/{uid}/favorites/playlists",
                  ["uuid", "title", "creator", "numberOfTracks", "added"],
                  lambda it: [it["uuid"], it["title"], (it.get("creator") or {}).get("name"), it.get("numberOfTracks"), None]),
    "my_playlists": (f"users/{uid}/playlists",
                     ["uuid", "title", "numberOfTracks", "added"],
                     lambda it: [it["uuid"], it["title"], it.get("numberOfTracks"), None]),
}

for name, (path, cols, rowfn) in TARGETS.items():
    print(f"[{name}]")
    raw = fetch_all(path)
    (OUT / f"{name}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols)
        for entry in raw:
            # favorites 응답은 {"created": ..., "item": {...}} 형태, 내 플레이리스트는 바로 객체
            it = entry.get("item", entry)
            row = rowfn(it); row[-1] = entry.get("created")
            w.writerow(row)

# 2. 내 플레이리스트 각각의 트랙까지 저장
pl_dir = OUT / "playlist_tracks"; pl_dir.mkdir(exist_ok=True)
for entry in json.loads((OUT / "my_playlists.json").read_text()):
    pl = entry.get("item", entry)
    print(f"[playlist] {pl['title']}")
    # 플레이리스트는 사용자가 정한 곡 순서(INDEX)가 곧 내용이다.
    # DATE DESC 로 받으면 순서가 뒤집힌 채 저장되어 복원 시 역순 플레이리스트가 만들어진다.
    tracks = fetch_all(f"playlists/{pl['uuid']}/items", limit=100, order="INDEX", direction="ASC")
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in pl["title"])[:60]
    (pl_dir / f"{safe}_{pl['uuid'][:8]}.json").write_text(json.dumps(tracks, ensure_ascii=False, indent=2))
    with open(pl_dir / f"{safe}_{pl['uuid'][:8]}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["id", "title", "artists", "album", "isrc"])
        for t in tracks:
            it = t.get("item", t)
            w.writerow([it.get("id"), it.get("title"), artists(it), it.get("album", {}).get("title"), it.get("isrc")])

print(f"\n완료: {OUT.resolve()}")
