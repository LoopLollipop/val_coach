#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_fake_val_log.py
VALORANT 스타일의 가짜 이벤트 로그 생성기
- 출력(JSON 배열): [{ts, actor, action, target, meta}, ...]
- story/analyze 파이프라인 테스트용 (Riot API 없이 사용)

사용 예:
  python make_fake_val_log.py
  python make_fake_val_log.py --rounds 10 --seed 42 --out sample.json
"""

import json
import random
import argparse
from datetime import datetime, timezone, timedelta

WEAPONS = [
    "Vandal", "Phantom", "Operator", "Spectre", "Bulldog",
    "Sheriff", "Ghost", "Classic", "Judge", "Marshal"
]

ATTACKERS = ["You", "Teammate1", "Teammate2", "Teammate3", "Teammate4"]
DEFENDERS = ["Enemy1", "Enemy2", "Enemy3", "Enemy4", "Enemy5"]

MAPS = ["Ascent", "Bind", "Haven", "Split", "Icebox", "Breeze", "Fracture", "Lotus", "Sunset", "Abyss"]
QUEUES = ["unrated", "competitive", "swiftplay", "spikerush"]

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

def gen_events(rounds: int, seed: int | None = None) -> list[dict]:
    if seed is not None:
        random.seed(seed)

    events = []
    # 기준 시간(최근 20~35분 사이 시작)
    start = datetime.now(timezone.utc) - timedelta(minutes=random.randint(20, 35))
    map_id = random.choice(MAPS)
    queue_id = random.choice(QUEUES)
    match_id = f"FAKE-{random.randint(100000, 999999)}"

    # 매치 시작
    events.append({
        "ts": iso(start),
        "actor": "SYSTEM",
        "action": "match_start",
        "target": queue_id,
        "meta": {"mapId": map_id, "matchId": match_id}
    })

    t = start
    atk, defn = ATTACKERS[:], DEFENDERS[:]
    atk_score = 0
    def_score = 0

    for r in range(1, rounds + 1):
        # 라운드 준비 시간
        t += timedelta(seconds=random.randint(10, 20))

        # (공격측 기준) 스파이크 설치 확률
        plant_happened = random.random() < 0.7
        if plant_happened:
            planter = random.choice(atk)
            t += timedelta(seconds=random.randint(12, 30))
            events.append({
                "ts": iso(t),
                "actor": planter,
                "action": "plant",
                "target": "Spike",
                "meta": {"roundNum": r}
            })

        # 킬 이벤트 (라운드당 1~4회)
        for _ in range(random.randint(1, 4)):
            t += timedelta(seconds=random.randint(4, 12))
            killer_side = atk if random.random() < 0.5 else defn
            victim_side = defn if killer_side is atk else atk
            if not victim_side:
                break
            killer = random.choice(killer_side)
            victim = random.choice([v for v in victim_side if v != killer] or [random.choice(victim_side)])
            weapon = random.choice(WEAPONS)
            events.append({
                "ts": iso(t),
                "actor": killer,
                "action": "kill",
                "target": victim,
                "meta": {"weapon": weapon, "roundNum": r}
            })

        # 해제 시도 (설치가 있었을 때만, 확률 35%)
        defuse_happened = plant_happened and (random.random() < 0.35)
        if defuse_happened:
            defuser = random.choice(defn)
            t += timedelta(seconds=random.randint(5, 12))
            events.append({
                "ts": iso(t),
                "actor": defuser,
                "action": "defuse",
                "target": "Spike",
                "meta": {"roundNum": r}
            })

        # 승패 임의 판정(설치/해제 여부에 가중치)
        if plant_happened and not defuse_happened:
            atk_score += 1
        elif defuse_happened:
            def_score += 1
        else:
            # 설치 없었으면 50:50
            if random.random() < 0.5:
                atk_score += 1
            else:
                def_score += 1

        # 라운드 간 간격
        t += timedelta(seconds=random.randint(6, 15))

    # 매치 종료
    t += timedelta(seconds=5)
    events.append({
        "ts": iso(t),
        "actor": "SYSTEM",
        "action": "match_end",
        "target": match_id,
        "meta": {"teams": [{"teamId": "Attackers", "score": atk_score}, {"teamId": "Defenders", "score": def_score}]}
    })

    # 시간 순 정렬
    events.sort(key=lambda e: (e["ts"] or "Z"))
    return events

def main():
    ap = argparse.ArgumentParser(description="가짜 VALORANT 이벤트 로그 생성기")
    ap.add_argument("--rounds", type=int, default=8, help="라운드 수 (기본 8)")
    ap.add_argument("--seed", type=int, help="랜덤 시드 (재현성)")
    ap.add_argument("--out", default="sample.json", help="저장 파일명 (기본 sample.json)")
    args = ap.parse_args()

    events = gen_events(args.rounds, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"[완료] {args.out} 생성됨 (라운드 {args.rounds}, 시드 {args.seed})")

if __name__ == "__main__":
    main()
