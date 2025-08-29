#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VALORANT 매치 → 이벤트 JSON 생성기
- Riot API에서 Riot ID → PUUID → 활성 샤드 → 최근 매치 → 매치 상세를 조회
- 스파이크 plant/defuse, kill, match_start/end 이벤트를 추출해 시간순 JSON으로 저장

필요:
  pip install requests python-dotenv

.env 예시:
  RIOT_API_KEY=RGAPI-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
  ACCOUNT_ROUTE=asia      # americas/europe/asia (계정 라우트)
  VAL_REGION=kr           # 기본 shard (kr/ap/na/eu/...), 활성 샤드로 자동 덮어씀
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

API_KEY    = os.getenv("RIOT_API_KEY")
ACCOUNT_RT = os.getenv("ACCOUNT_ROUTE", "asia")   # americas/europe/asia
VAL_REGION = os.getenv("VAL_REGION", "kr")        # kr/ap/na/eu/... (활성 샤드로 런타임에 덮어씀)

HDR = {"X-Riot-Token": API_KEY}

def die(msg: str):
    print(f"[에러] {msg}", file=sys.stderr)
    sys.exit(1)

def rget(url: str, params=None):
    """GET + 간단한 429 재시도."""
    r = requests.get(url, headers=HDR, params=params, timeout=20)
    if r.status_code == 429:
        retry = int(r.headers.get("Retry-After", "2"))
        time.sleep(retry)
        r = requests.get(url, headers=HDR, params=params, timeout=20)
    if r.status_code != 200:
        die(f"{r.status_code} {r.text[:400]}")
    return r.json()

# -------------------- 계정/샤드 조회 --------------------

def get_puuid(game_name: str, tag_line: str) -> str:
    """Riot ID → PUUID (account-v1, route: americas/europe/asia)"""
    url = f"https://{ACCOUNT_RT}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    j = rget(url)
    return j["puuid"]

def get_active_shard(puuid: str) -> str:
    """해당 PUUID의 VALORANT 활성 샤드 조회 (예: kr, ap, na, eu 등)"""
    url = f"https://{ACCOUNT_RT}.api.riotgames.com/riot/account/v1/active-shards/by-game/val/by-puuid/{puuid}"
    j = rget(url)
    return j.get("activeShard")

# -------------------- 매치 조회 --------------------

def get_recent_match_ids_by_puuid(puuid: str, shard: str, count: int = 3):
    """해당 shard에서 최근 매치 ID 목록"""
    url = f"https://{shard}.api.riotgames.com/val/match/v1/matchlists/by-puuid/{puuid}"
    j = rget(url)
    ids = [m["matchId"] for m in j.get("history", [])]
    return ids[:count]

def get_match_detail(match_id: str, shard: str):
    """매치 상세"""
    url = f"https://{shard}.api.riotgames.com/val/match/v1/matches/{match_id}"
    return rget(url)

# -------------------- 유틸/추출 --------------------

def as_ts(ms):
    """epoch(ms) → ISO8601 문자열"""
    try:
        return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat()
    except Exception:
        return None

def extract_events_from_match(md: dict):
    """
    match detail에서 간추린 이벤트 생성:
      - match_start / match_end
      - plant / defuse
      - kill
    스키마: {ts, actor, action, target, meta}
    """
    events = []
    info    = md.get("matchInfo", {}) or {}
    players = {p["puuid"]: p for p in md.get("players", [])}
    rounds  = md.get("roundResults", []) or []

    # 매치 시작
    start_ms = info.get("gameStartMillis")
    events.append({
        "ts": as_ts(start_ms) if start_ms else None,
        "actor": "SYSTEM",
        "action": "match_start",
        "target": info.get("queueID"),
        "meta": {"mapId": info.get("mapId"), "matchId": info.get("matchId")}
    })

    base = info.get("gameStartMillis", 0)

    for rr in rounds:
        rnd = rr.get("roundNum")

        # plant
        plant = rr.get("plantRoundTime")
        if plant is not None:
            planter_p = (rr.get("plantPlayerLocations") or [{}])[0].get("puuid")
            actor = players.get(planter_p, {}).get("gameName") or planter_p or "Unknown"
            events.append({
                "ts": as_ts(base + plant),
                "actor": actor,
                "action": "plant",
                "target": "Spike",
                "meta": {"roundNum": rnd}
            })

        # defuse
        defuse = rr.get("defuseRoundTime")
        if defuse is not None:
            defuser_p = (rr.get("defusePlayerLocations") or [{}])[0].get("puuid")
            actor = players.get(defuser_p, {}).get("gameName") or defuser_p or "Unknown"
            events.append({
                "ts": as_ts(base + defuse),
                "actor": actor,
                "action": "defuse",
                "target": "Spike",
                "meta": {"roundNum": rnd}
            })

        # kills
        for ps in rr.get("playerStats", []):
            killer_name = players.get(ps.get("puuid",""), {}).get("gameName") or ps.get("puuid","Unknown")
            for k in ps.get("kills", []):
                victim_p = k.get("victim")
                victim   = players.get(victim_p, {}).get("gameName") or victim_p or "Unknown"
                ts_ms    = k.get("timeSinceGameStartMillis")
                weap     = (k.get("finishingDamage") or {}).get("damageItem") or "weapon"
                events.append({
                    "ts": as_ts(ts_ms) if ts_ms else None,
                    "actor": killer_name,
                    "action": "kill",
                    "target": victim,
                    "meta": {"weapon": weap, "roundNum": rnd}
                })

    # 매치 종료
    end_ms = info.get("gameStartMillis", 0) + info.get("gameLengthMillis", 0)
    events.append({
        "ts": as_ts(end_ms),
        "actor": "SYSTEM",
        "action": "match_end",
        "target": info.get("matchId"),
        "meta": {"teams": info.get("teams")}
    })

    # 정렬
    events.sort(key=lambda e: (e["ts"] or "Z"))
    return events

# -------------------- 메인 --------------------

def main():
    ap = argparse.ArgumentParser(description="VALORANT → 이벤트 JSON 생성기 (활성 샤드 자동 감지)")
    ap.add_argument("--riot-id", required=True, help='예: GameName#KR1')
    ap.add_argument("--count", type=int, default=1, help="최근 매치 n개 (기본 1)")
    ap.add_argument("--out", default="sample.json", help="저장 파일명 (기본 sample.json)")
    args = ap.parse_args()

    if not API_KEY:
        die("RIOT_API_KEY가 .env 또는 환경변수에 없습니다.")

    if "#" not in args.riot_id:
        die("--riot-id 형식은 GameName#TagLine 입니다. 예: GOODLUCK#KR1")

    game, tag = args.riot_id.split("#", 1)

    print(f"[정보] {game}#{tag} → PUUID 조회…")
    puuid = get_puuid(game, tag)
    print(f"[정보] PUUID: {puuid}")

    # 활성 샤드 자동 감지
    shard = get_active_shard(puuid)
    if not shard:
        die("활성 샤드를 확인할 수 없습니다. (계정 라우트/키/계정 상태 확인)")
    print(f"[정보] 활성 샤드: {shard}")

    # 이 실행 동안에는 감지한 샤드를 사용
    print(f"[정보] 최근 매치 {args.count}개 조회…")
    mids = get_recent_match_ids_by_puuid(puuid, shard, count=args.count)
    if not mids:
        die("매치 기록이 없습니다. (비공개 계정이거나 최근 전적 없음)")

    all_events = []
    for mid in mids:
        print(f"[정보] match {mid} 상세 가져오는 중…")
        md = get_match_detail(mid, shard)
        evs = extract_events_from_match(md)
        all_events.extend(evs + [{"ts": None, "actor": "SYSTEM", "action": "separator", "target": mid, "meta": {}}])
        time.sleep(1)  # rate limit 여유
    if all_events and all_events[-1]["action"] == "separator":
        all_events.pop()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)
    print(f"[완료] {args.out} 저장. 다음 실행 예:")
    print(f"  python analyze_valorant.py {args.out} --out report.json")

if __name__ == "__main__":
    main()
