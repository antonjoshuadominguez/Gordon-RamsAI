"""
Workout log parsing: natural language → exercise_name, sets, reps, weight_kg (kg only).
Used only for workout logging paths (not general chat semantics).
"""

from __future__ import annotations

import re
from typing import Any

LB_TO_KG = 0.45359237

INVALID_EXERCISE = frozenset(
    {
        "for",
        "on",
        "to",
        "of",
        "a",
        "the",
        "it",
        "and",
        "or",
        "kg",
        "lb",
        "lbs",
        "pr",
        "reps",
        "rep",
        "set",
        "sets",
    }
)

_TRAILING_FOR = re.compile(r"\s+for\s*$", re.I)
_LEAD_EX = re.compile(r"^(new|another|my|a|the)\s+", re.I)


def to_kg(value: float, unit: str) -> float:
    u = (unit or "kg").lower().rstrip("s")  # lbs -> lb
    if u in ("lb", "pound"):
        return round(float(value) * LB_TO_KG, 3)
    return round(float(value), 3)


def normalize_exercise_key(name: str) -> str:
    """Stable key for PR grouping / dedupe."""
    if not name:
        return ""
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = _TRAILING_FOR.sub("", s).strip()
    return s


def normalize_exercise_display(raw: str) -> str | None:
    """Clean exercise label for DB; None if unusable."""
    if not raw:
        return None
    s = re.sub(r"\s+", " ", str(raw).strip())
    s = _TRAILING_FOR.sub("", s).strip(" -–—:,.!?")
    s = _LEAD_EX.sub("", s).strip()
    key = s.lower()
    if len(key) < 2 or key in INVALID_EXERCISE:
        return None
    return " ".join(w.capitalize() for w in s.split() if w)


def _norm_seg(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _finalize(exercise: str, sets: int, reps: int, weight: float, wunit: str) -> dict[str, Any] | None:
    ex = normalize_exercise_display(exercise)
    if ex is None:
        return None
    w_kg = to_kg(weight, wunit)
    if w_kg <= 0:
        return None
    return {
        "exercise": ex,
        "sets": max(1, int(sets)),
        "reps": max(1, int(reps)),
        "weight": float(w_kg),
    }


def _parse_one_segment(norm: str) -> dict[str, Any] | None:
    """Try ordered patterns on one lowercase-normalized segment."""
    if not norm:
        return None

    # 1) "... leg press pr of 300kg for 8 reps" — exercise is the word run *immediately* before "pr"/"personal record"
    m = re.search(
        r"(?:pr|personal\s+record)\s+of\s+(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b"
        r"(?:\s+for\s+(?P<reps>\d+)\s*reps?)?",
        norm,
    )
    if m:
        before_pr = norm[: m.start()].strip()
        gd = m.groupdict()
        reps = int(gd["reps"]) if gd.get("reps") else 1
        # Prefer the last few alphabetic words before "PR of …" (skip long chatty prefixes).
        words = re.findall(r"[a-z0-9'\-]+", before_pr)
        stop_first = {
            "thanks", "thank", "those", "these", "helped", "me", "i", "im", "just",
            "then", "that", "this", "for", "with", "some", "got", "have", "eggs",
            "food", "meal", "banana", "shape", "and", "or", "so", "well", "wow",
            "make", "made", "doing", "do", "did", "give", "gave", "help", "helps",
            "a", "an", "new", "another", "some",
        }
        ex_raw = None
        for n in range(10, 1, -1):
            if len(words) < n:
                continue
            chunk = words[-n:]
            if chunk[0] in stop_first:
                continue
            cand = " ".join(chunk)
            if len(cand) >= 4:
                ex_raw = cand
                break
        if not ex_raw and words:
            ex_raw = " ".join(words[-min(4, len(words)) :])
        if ex_raw:
            r = _finalize(ex_raw, 1, reps, float(m.group("weight")), m.group("wunit"))
            if r:
                return r

    # 2) "pr of 300kg on leg press" / "personal record of 300 lb for leg press"
    m = re.search(
        r"(?:pr|personal\s+record)\s+of\s+(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b"
        r"(?:\s+for\s+(?P<reps>\d+)\s*reps?)?\s*(?:on|for)\s+(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58})",
        norm,
    )
    if m:
        reps = int(m.group("reps")) if m.groupdict().get("reps") and m.group("reps") else 1
        r = _finalize(m.group("exercise"), 1, reps, float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 3) "300kg leg press for 8 reps" — exercise must not be the word "for"
    m = re.search(
        r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s+(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58}?)\s+for\s+(?P<reps>\d+)\s*reps?\b",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, int(m.group("reps")), float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 4) "squats for 8 reps at 100kg"
    m = re.search(
        r"(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58}?)\s+for\s+(?P<reps>\d+)\s*reps?\s+(?:at|@)\s+"
        r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, int(m.group("reps")), float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 5) "i did 100kg squats for 8 reps" (verb prefix)
    m = re.search(
        r"(?:^|[\s!?.]|did|done|hit|got|made)\s*(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s+"
        r"(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58}?)\s+for\s+(?P<reps>\d+)\s*reps?\b",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, int(m.group("reps")), float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 6) legacy PR line: "pr ... 100kg ... bench" without "for N reps" (reps=1)
    m = re.search(
        r"(?:pr|personal\s+record)\s+.{0,35}?(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b"
        r".{0,22}?(?:on|for)\s+(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58})(?!\s+for\s+\d)",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, 1, float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 7) "i hit 100kg bench" / trailing context (reps=1)
    m = re.search(
        r"(?:i\s+(?:just\s+)?)?(?:did|hit|completed|got|made)\s+.{0,22}?"
        r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b.{0,18}?(?:on|for)\s+(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58})",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, 1, float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    # 8) loose "100kg bench today" (avoid capturing "for" as exercise)
    m = re.search(
        r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\b.{0,14}?(?:on|for)\s+(?!for\b)(?P<exercise>[a-z][a-z0-9\s'\-/]{2,58}?)(?:[.,!?]|$|\s+today|\s+just)",
        norm,
    )
    if m:
        r = _finalize(m.group("exercise"), 1, 1, float(m.group("weight")), m.group("wunit"))
        if r:
            return r

    return None


def extract_workouts_from_text(text: str) -> list[dict[str, Any]]:
    """
    Split into coarse segments (newlines / semicolons) and extract at most one workout per segment.
    Deduplicate identical payloads within the same message.
    """
    if not (text or "").strip():
        return []
    parts = [p.strip() for p in re.split(r"[\n;]+", text) if p.strip()]
    if not parts:
        parts = [text.strip()]
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for part in parts:
        w = _parse_one_segment(_norm_seg(part))
        if not w:
            continue
        sig = (normalize_exercise_key(w["exercise"]), w["sets"], w["reps"], round(w["weight"], 3))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(w)
    # If nothing from split lines, try whole text once
    if not out:
        w = _parse_one_segment(_norm_seg(text))
        if w:
            out.append(w)
    return out


def parse_log_command_body(body: str) -> dict[str, Any] | None:
    """
    Parse the tail after 'log this:' etc. Supports kg or lbs in the weight slot.
    """
    b = (body or "").strip()
    if not b:
        return None

    def _clean_exercise(name: str) -> str | None:
        return normalize_exercise_display(name)

    _xr = r"(?:\s*x\s*|x|\s+)"
    patterns = (
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*sets?)?"
        + _xr
        + r"\s*(?P<reps>\d+)(?:\s*reps?)?\s+(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s*$",
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s+(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s*$",
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s*@\s*(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s*$",
        r"^(?P<exercise>.+?)\s+(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s+(?P<sets>\d+)(?:\s*sets?)?"
        + _xr
        + r"\s*(?P<reps>\d+)(?:\s*reps?)?\s*$",
        r"^(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s*@\s*(?P<weight>\d+(?:\.\d+)?)\s*(?P<wunit>kg|lbs?)\s+(?P<exercise>.+)$",
    )
    for pat in patterns:
        match = re.search(pat, b, flags=re.IGNORECASE)
        if not match:
            continue
        gd = match.groupdict()
        ex = _clean_exercise(gd["exercise"])
        if not ex:
            continue
        wu = gd.get("wunit") or "kg"
        wval = to_kg(float(gd["weight"]), wu)
        return {
            "exercise": ex,
            "sets": int(gd["sets"]),
            "reps": int(gd["reps"]),
            "weight": float(wval),
        }
    return None
