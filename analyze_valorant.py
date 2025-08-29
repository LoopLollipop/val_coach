#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_valorant.py
VALORANT 이벤트 로그(JSON) -> LLM 분석 리포트 생성기 (Google AI Studio / Gemini)
- 미완성 JSON 자동 복구 + 출력 축소 + 재시도/폴백 처리

필수:
  pip install google-generativeai python-dotenv
.env:
  GOOGLE_API_KEY=AIzaSyD-...
  MODEL_NAME=gemini-1.5-flash   # 권장
"""

import os
import re
import sys
import json
import time
import argparse
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# --------- Gemini SDK -----------
try:
    import google.generativeai as genai
    from google.api_core.exceptions import ResourceExhausted
except ImportError:
    sys.exit("[에러] google-generativeai 패키지가 없습니다.  pip install google-generativeai python-dotenv")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")

if not GOOGLE_API_KEY:
    sys.exit("[에러] GOOGLE_API_KEY가 설정되어 있지 않습니다. .env에 GOOGLE_API_KEY를 추가하세요.")

genai.configure(api_key=GOOGLE_API_KEY)

# --------- 프롬프트 ----------
SYSTEM_PROMPT = """너는 VALORANT 경기 로그 분석가이자 코치다.
입력은 시간순 이벤트 배열이며 각 항목은 {ts, actor, action, target, meta} 스키마다.
한국어로 간결하고 실전적인 조언을 제공한다.
반드시 'json' 키 하나를 가진 JSON만 출력하라. 다른 텍스트는 금지.
"""

# ⚠️ 리터럴 중괄호 이스케이프( {{ }} ), 단 {events}만 실제 치환
USER_PROMPT_TEMPLATE = """다음은 VALORANT 경기 이벤트 로그다.

이벤트 스키마:
- ts: ISO8601 타임스탬프 혹은 null
- actor: 행위자(플레이어명 또는 SYSTEM)
- action: 하나 (match_start, match_end, plant, defuse, kill, separator 등)
- target: 대상(플레이어나 Spike 등)
- meta: 부가정보(weapon, roundNum, mapId 등)

요구사항(출력 축소 버전):
1) story: 2~3문단 서사 요약 (톤=담담, 과장 금지, 로그에 없는 가정 최소화)
2) coaching:
   - strengths: 2개
   - mistakes: 2개 (원인과 대안 포함, 각 1문장씩)
   - checklist: 3개 (간결한 명령형, 10자 내외)
3) highlights: 중요 순간 최대 2개 [{{ts, label, roundNum?, actor?, target?}}]
4) metrics: 간단 지표(추정 가능 범위): kills/plants/defuses/rounds

출력 형식(엄수): 
{{
  "json": {{
    "story": "<문단들>",
    "coaching": {{
      "strengths": ["...", "..."],
      "mistakes": [{{"issue":"...","fix":"..."}}, {{"issue":"...","fix":"..."}}],
      "checklist": ["...", "...", "..."]
    }},
    "highlights": [{{"ts":"...","label":"...","roundNum":1}}],
    "metrics": {{"kills":0,"plants":0,"defuses":0,"rounds":0}}
  }}
}}

이벤트:
{events}
"""

# --------- 유틸 ----------
def load_events(path_or_dash: str):
    """source가 '-'면 stdin, 아니면 파일에서 JSON 배열을 읽고 ts로 정렬."""
    raw = sys.stdin.read() if path_or_dash == "-" else open(path_or_dash, "r", encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"[에러] 이벤트 JSON 파싱 실패: {e}")

    def _key(e):
        ts = e.get("ts")
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else datetime.max
        except Exception:
            return datetime.max

    data.sort(key=_key)
    return data

def shrink_events(events, max_items=160):
    """입력 로그가 너무 길 때 앞/뒤 위주 샘플링 + 핵심 이벤트 유지로 토큰 절감."""
    if len(events) <= max_items:
        return events
    head = events[:max_items//2]
    tail = events[-max_items//2:]
    keep = [e for e in events if e.get("action") in ("match_start", "match_end", "plant", "defuse")]
    merged = head + keep + tail
    # 중복 제거
    seen, uniq = set(), []
    for e in merged:
        key = (e.get("ts"), e.get("actor"), e.get("action"), e.get("target"))
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    uniq.sort(key=lambda x: x.get("ts") or "Z")
    return uniq[:max_items]

# ---------- JSON 파싱/복구 도구 ----------
def extract_top_level_json(s: str) -> str:
    """응답 문자열에서 첫 최상위 JSON 블록만 잘라내기."""
    if not s:
        return s
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    if start == -1:
        return s
    depth = 0
    end = None
    for i, ch in enumerate(s[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return s
    return s[start:end]

def find_all_top_level_json(s: str) -> list[str]:
    """문자열에서 모든 최상위 JSON 블록을 찾아 리스트로 반환."""
    s = s.strip()
    s = re.sub(r"```(?:json)?", "", s)
    s = s.replace("```", "")
    out = []
    depth = 0
    start = None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(s[start:i+1])
                    start = None
    return out

def wrap_if_needed(obj):
    """응답이 wrapper 없이 바로 본문이면 {"json": obj}로 감쌈."""
    if isinstance(obj, dict) and "json" not in obj:
        keys = set(obj.keys())
        expected = {"story", "coaching", "highlights", "metrics"}
        if expected.issubset(keys):
            return {"json": obj}
    return obj

def naive_json_repair(trunc: str):
    """
    아주 단순한 미완성 JSON 복구:
    - 따옴표 개수 홀수면 닫기
    - [], {} 개수 차이만큼 닫기(두 순서 모두 시도)
    """
    cand = extract_top_level_json(trunc).strip()
    if not cand:
        return None

    # 따옴표 개수(escape 무시, 약식)
    if cand.count('"') % 2 == 1:
        cand += '"'

    def balance(c: str):
        opens = cand.count(c[0])
        closes = cand.count(c[1])
        return max(0, opens - closes)

    close_braces   = balance("{}")
    close_brackets = balance("[]")

    # 시도1: ]들 먼저, 그다음 }
    cand1 = cand + ("]" * close_brackets) + ("}" * close_braces)
    try:
        return json.loads(cand1)
    except Exception:
        pass

    # 시도2: }들 먼저, 그다음 ]
    cand2 = cand + ("}" * close_braces) + ("]" * close_brackets)
    try:
        return json.loads(cand2)
    except Exception:
        return None

def try_parse_or_coerce(raw_text: str):
    """
    1차: 직파싱
    2차: 단일 블록 추출 후 파싱
    3차: 여러 후보 중 스코어링으로 최적 선택
    4차: 단순 복구(naive_json_repair)
    실패 시 None
    """
    # 1차
    try:
        obj = json.loads(raw_text)
        return wrap_if_needed(obj)
    except Exception:
        pass
    # 2차
    single = extract_top_level_json(raw_text)
    try:
        obj = json.loads(single)
        return wrap_if_needed(obj)
    except Exception:
        pass
    # 3차
    best = None
    best_score = -1
    for cand in find_all_top_level_json(raw_text):
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        obj = wrap_if_needed(obj)
        score = 0
        root = obj.get("json", obj) if isinstance(obj, dict) else {}
        if isinstance(obj, dict) and "json" in obj:
            score += 2
        if isinstance(root, dict):
            for k in ("story", "coaching", "highlights", "metrics"):
                if k in root:
                    score += 1
        if score > best_score:
            best_score = score
            best = obj
    if best:
        return best
    # 4차: 단순 복구
    repaired = naive_json_repair(raw_text)
    return repaired

# ---------- LLM 호출 ----------
def call_gemini_json_with_retry(system_prompt: str, user_prompt: str, model_name: str, max_retries: int = 2) -> str:
    """
    429(쿼터 초과) 시 retry_delay를 존중해 재시도.
    실패 시 flash로 폴백. (schema-less)
    """
    from google.generativeai import GenerativeModel
    attempt = 0
    curr_model = model_name
    while attempt <= max_retries:
        try:
            model = GenerativeModel(curr_model)
            resp = model.generate_content(
                f"{system_prompt}\n\n{user_prompt}",
                generation_config={
                    "response_mime_type": "application/json",
                    "max_output_tokens": 800,   # ↑ 출력 끊김 방지
                    "temperature": 0.15
                }
            )
            if not resp or not getattr(resp, "text", None):
                raise RuntimeError("빈 응답")
            return resp.text
        except ResourceExhausted as e:
            # 429 - 쿼터 초과
            delay = 20
            try:
                delay = getattr(getattr(e, "retry_delay", None), "seconds", None) or delay
            except Exception:
                pass
            time.sleep(min(int(delay), 60))
            # pro 사용 중이면 flash로 폴백
            if "pro" in curr_model:
                curr_model = "gemini-1.5-flash"
        except Exception:
            # 기타 에러 한 번 폴백 후 재시도
            if "pro" in curr_model:
                curr_model = "gemini-1.5-flash"
            else:
                raise
        attempt += 1
    raise RuntimeError("Gemini 호출 반복 실패")

# --------- 메인 ----------
def main():
    ap = argparse.ArgumentParser(description="VALORANT 로그 LLM 분석기 (Gemini, robust)")
    ap.add_argument("source", help="이벤트 JSON 경로 또는 '-'(stdin)")
    ap.add_argument("--out", help="결과 저장 파일(.json). 미설정 시 stdout")
    args = ap.parse_args()

    events = load_events(args.source)
    events = shrink_events(events, max_items=160)  # 입력 토큰 감소

    user_prompt = USER_PROMPT_TEMPLATE.format(events=json.dumps(events, ensure_ascii=False, indent=2))
    raw = call_gemini_json_with_retry(SYSTEM_PROMPT, user_prompt, os.getenv("MODEL_NAME", "gemini-1.5-flash"))

    wrapper = try_parse_or_coerce(raw)
    if not wrapper or "json" not in wrapper:
        # 축약 프롬프트로 재시도(더 짧게)
        short_user = (
            USER_PROMPT_TEMPLATE
            .replace("2~3문단", "2문단")
            .replace("strengths: 2개", "strengths: 2개 (각 10자)")
            .replace("mistakes: 2개", "mistakes: 2개 (각 1문장, 20자 이내)")
            .replace("checklist: 3개", "checklist: 3개 (각 10자)")
            .replace("중요 순간 최대 2개", "중요 순간 최대 1개")
        )
        raw2 = call_gemini_json_with_retry(
            SYSTEM_PROMPT + "\n\n반드시 단일 JSON만 반환. 코드펜스/설명 금지.",
            short_user.format(events=json.dumps(events, ensure_ascii=False, indent=2)),
            os.getenv("MODEL_NAME", "gemini-1.5-flash")
        )
        wrapper = try_parse_or_coerce(raw2)
        if not wrapper or "json" not in wrapper:
            with open("gemini_raw_response.txt", "w", encoding="utf-8") as f:
                f.write(raw2)
            sys.exit("[에러] 모델 응답 JSON 파싱 실패(재시도 포함). gemini_raw_response.txt를 확인하세요.")

    report = wrapper["json"]

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[완료] {args.out} 저장")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()



