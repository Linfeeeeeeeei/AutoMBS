#!/usr/bin/env python3
"""
Minimal hard-rule engine (v3) for MBS ED coding.

Policy:
- Hard checks now: age, duration (effective dates optional via flag)
- Components NOT hard-gated; we pass items even if components are unknown.
- "Optimistic pass": if no applicable fields exist on both sides (KB & facts), pass the item so LLM can judge later.
- NEW: Attendance-complexity gate (runs AFTER age/duration). If an item's description encodes
       "high", "more than ordinary but is not high", or "ordinary", compare to extractor's
       hard_gates_observed.attendance_complexity.value. If item has no such phrase, skip this gate.
       If phrase exists but extractor complexity is unknown, keep item (LLM decides later).

Inputs:
  --kb <path.jsonl>          : KB items, one JSON per line
  --facts <path.json>        : Extractor output (min v3 schema)
  --encounter-date YYYY-MM-DD: Optional; used only if --use-effective-dates is set
  --use-effective-dates      : Turn on effective date gate (off by default)
  --no-age                   : Disable age check
  --no-duration              : Disable duration check
  --limit <N>                : Optional: cap number of pass items printed (for quick tests)

Output (JSON):
{
  "$schema": "mbs.hardrule.passlist.v1",
  "encounter_date_used": "... or null",
  "config": { ... },
  "facts_digest": { ... },
  "passed_items": [ { Pass Object }, ... ]
}

Pass Object (minimal, LLM-friendly):
{
  "item_number": "14270",
  "description_original": "...",
  "soft_requirements_hint": {
    "components_required": [...],
    "components_prohibited": [...],
    "setting_tokens": [...],
    "provider_roles_allowed": [...],
    "referral_required": false
  },
  "llm_review_todo": [
    "confirm_components_present_in_note",
    "confirm_setting_matches_note (ED/private hospital?)",
    "confirm_provider_role or attending clinician type",
    "check_co-claim_requirements (e.g., attendance linkage)",
    "verify any implied durations / contiguity from context"
  ],
  "kept_because": "age within gate; duration check skipped (no minutes).",
  "salient_evidence": {
    "age": ["Age: 55Y"],
    "components": ["CAM boot", "avulsion fracture", "CT brain"]
  }
}
"""
import argparse, json, sys, datetime, re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------- Helpers ---------------------------

def _today_iso() -> str:
    return datetime.date.today().isoformat()

def _in_effect(item: Dict[str, Any], encounter_date: Optional[str]) -> bool:
    d = encounter_date or _today_iso()
    ef = (item.get("effective_from") or "1900-01-01")
    et = (item.get("effective_to") or "9999-12-31")
    return isinstance(ef, str) and isinstance(et, str) and (ef <= d <= et)

def _extract_age_years(facts: Dict[str, Any]) -> Optional[float]:
    v = facts.get("patient_age_years")
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

def _item_age_gate(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hg = (item.get("hard_gates") or {})
    return hg.get("patient_age")

def _get_service_req(item: Dict[str, Any]) -> Dict[str, Any]:
    return (item.get("hard_gates") or {}).get("service_requirements") or {}

def _observed_components(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ((facts.get("hard_gates_observed") or {}).get("components") or [])

def _observed_durations(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ((facts.get("hard_gates_observed") or {}).get("durations") or [])

def _observed_attendance_complexity(facts: Dict[str, Any]) -> Optional[str]:
    ac = ((facts.get("hard_gates_observed") or {}).get("attendance_complexity") or {})
    v = (ac or {}).get("value")
    if not v:
        return None
    s = str(v).strip().lower().replace(" ", "_")
    if s in ("more_than_ordinary_not_high", "more_than_ordinary_but_is_not_high", "more than ordinary but not high", "more than ordinary but is not high"):
        return "more_than_ordinary_not_high"
    if s in ("ordinary", "ordinary_complexity", "ordinary complexity"):
        return "ordinary"
    if s in ("high", "high_complexity", "high complexity"):
        return "high"
    return s  # fallback

def _expected_complexity_from_desc(item: Dict[str, Any]) -> Optional[str]:
    """
    Parse item's description text for a complexity phrase.
    Returns: 'high' | 'more_than_ordinary_not_high' | 'ordinary' | None
    """
    desc = (_get_description_original(item) or "").lower()
    # check 'more than ordinary ... not high' first
    if "more than ordinary" in desc and "not high" in desc:
        return "more_than_ordinary_not_high"
    # 'high' but not 'not high'
    if "high" in desc and "not high" not in desc and re.search(r"\bhigh\b", desc):
        return "high"
    # 'ordinary' only if no higher phrase was matched
    if re.search(r"\bordinary\b", desc) and "more than ordinary" not in desc:
        return "ordinary"
    return None

def _norm_component_label(label: Optional[str]) -> Optional[str]:
    if not label: return None
    s = str(label).strip().lower().replace(" ", "_")
    # simple normaliser for common cases
    if s in ("ct","ct_head","ct_brain","ct_facial_bones","ct_left_foot","ct_foot"):
        return "imaging_ct"
    if "ct" in s and "imaging_ct" not in s:
        return "imaging_ct"
    if any(k in s for k in ["plaster","backslab","cast","cam_boot","camboot","boot","immobilis"]):
        return "immobilisation"
    if "suture" in s or "suturing" in s:
        return "procedure_minor"
    if "fracture" in s and "management" in s:
        return "fracture_management"
    return s

def _component_tokens_from_facts(facts: Dict[str, Any]) -> List[str]:
    toks = []
    for comp in _observed_components(facts):
        norm = comp.get("normalized")
        if norm:
            toks.append(str(norm))
        else:
            toks.append(_norm_component_label(comp.get("label")) or "")
    return [t for t in toks if t]

def _gather_salient_evidence(facts: Dict[str, Any]) -> Dict[str, List[str]]:
    out = {"age": [], "components": []}
    # Age evidence
    for span in (facts.get("patient_age_evidence") or []):
        t = (span or {}).get("text")
        if t: out["age"].append(t)
    # Component evidence (sample up to 6)
    comp_ev = []
    for comp in _observed_components(facts):
        for span in (comp.get("evidence") or []):
            t = (span or {}).get("text")
            if t: comp_ev.append(t)
    out["components"] = comp_ev[:6]
    return out

def _get_description_original(item: Dict[str, Any]) -> Optional[str]:
    # Try common locations
    disp = item.get("display") or {}
    for k in ("description_original", "description"):
        if disp.get(k): return disp[k]
    # root-level fallbacks
    for k in ("description_original", "description"):
        if item.get(k): return item[k]
    return None

def _soft_hints(item: Dict[str, Any]) -> Dict[str, Any]:
    hg = item.get("hard_gates") or {}
    sr = (hg.get("service_requirements") or {})
    setting = (hg.get("setting_mode") or {})
    return {
        "components_required": sr.get("components_required") or [],
        "components_prohibited": sr.get("components_prohibited") or [],
        "setting_tokens": setting.get("locations_allowed") or [],
        "provider_roles_allowed": hg.get("provider_roles_allowed") or [],
        "referral_required": (hg.get("referral") or {}).get("required", False)
    }

# --------------------------- Core evaluation ---------------------------

def eval_item(item: Dict[str, Any], facts: Dict[str, Any], cfg: Dict[str, Any], encounter_date: Optional[str]) -> Tuple[bool, List[str]]:
    """Returns (passes, reasons_kept). Reasons_kept is a human-readable list for the 'kept_because' field."""
    reasons = []
    has_any_check = False

    # Effective dates (optional)
    if cfg.get("use_effective_dates", False):
        has_any_check = True
        if not _in_effect(item, encounter_date):
            return (False, [])
        reasons.append("effective dates: in effect")

    # Age
    if cfg.get("use_age_gate", True):
        age_gate = _item_age_gate(item)
        age_years = _extract_age_years(facts)
        if age_gate:
            has_any_check = True
            if age_years is not None:
                unit = (age_gate.get("unit") or "years")
                a = age_years if unit == "years" else age_years/12.0
                minv = age_gate.get("min"); maxv = age_gate.get("max")
                min_inc = age_gate.get("min_inclusive"); max_inc = age_gate.get("max_inclusive")
                def below_min(x):
                    if minv is None: return False
                    return x < minv if (min_inc is True) else x <= minv if (min_inc is False) else x < minv
                def above_max(x):
                    if maxv is None: return False
                    return x > maxv if (max_inc is True) else x >= maxv if (max_inc is False) else x > maxv
                if below_min(a) or above_max(a):
                    return (False, [])
                reasons.append(f"age {age_years} within gate")
            else:
                reasons.append("age gate present but unknown in facts (kept for LLM)")

    # Duration
    if cfg.get("use_duration_thresholds", True):
        sr = _get_service_req(item)
        min_dur = sr.get("min_duration_minutes")
        max_dur = sr.get("max_duration_minutes")
        if (min_dur is not None) or (max_dur is not None):
            has_any_check = True
            # find any observed minutes (prefer component-linked later if needed)
            observed_minutes = None
            for dur in _observed_durations(facts):
                m = dur.get("minutes")
                if m is not None:
                    try:
                        observed_minutes = float(m)
                    except Exception:
                        continue
            if observed_minutes is None:
                reasons.append("duration gate present but no minutes in facts (kept for LLM)")
            else:
                if (min_dur is not None) and (observed_minutes < float(min_dur)):
                    return (False, [])
                if (max_dur is not None) and (observed_minutes > float(max_dur)):
                    return (False, [])
                reasons.append(f"duration {observed_minutes} within gate")

    # NEW: Attendance complexity gate (last hard check; text match is slower)
    expected_cx = _expected_complexity_from_desc(item)
    if expected_cx is not None:
        has_any_check = True
        observed_cx = _observed_attendance_complexity(facts)
        if observed_cx is None:
            reasons.append(f"complexity phrase in item ({expected_cx}) but unknown in facts (kept for LLM)")
        else:
            if observed_cx != expected_cx:
                return (False, [])
            reasons.append(f"complexity {observed_cx} matches item expectation")

    # Optimistic pass if no checks applied
    if not has_any_check:
        reasons.append("no applicable hard checks; kept for LLM")

    return (True, reasons)

# --------------------------- IO & main ---------------------------

def load_kb_jsonl(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            out.append(json.loads(line))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True, help="Path to KB jsonl")
    ap.add_argument("--facts", required=True, help="Path to extractor facts (min v3)")
    ap.add_argument("--encounter-date", default=None, help="YYYY-MM-DD; used if --use-effective-dates")
    ap.add_argument("--use-effective-dates", action="store_true", help="Enable effective date checks")
    ap.add_argument("--no-age", action="store_true", help="Disable age gate")
    ap.add_argument("--no-duration", action="store_true", help="Disable duration thresholds")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of pass items for quick testing")
    args = ap.parse_args()

    with open(args.facts, "r", encoding="utf-8") as f:
        facts = json.load(f)
    kb = load_kb_jsonl(args.kb)

    cfg = {
        "use_effective_dates": bool(args.use_effective_dates),
        "use_age_gate": not args.no_age,
        "use_duration_thresholds": not args.no_duration,
        "use_components_hard_gate": False
    }

    pass_items = []
    for item in kb:
        ok, reasons = eval_item(item, facts, cfg, args.encounter_date)
        if not ok:
            continue
        # Build Pass Object
        pass_obj = {
            "item_number": item.get("item_number"),
            "description_original": _get_description_original(item),
            "soft_requirements_hint": _soft_hints(item),
            "llm_review_todo": [
                "confirm_components_present_in_note",
                "confirm_setting_matches_note (ED/private hospital?)",
                "confirm_provider_role or attending clinician type",
                "check_co-claim_requirements (e.g., attendance linkage)",
                "verify any implied durations / contiguity from context"
            ],
            "kept_because": "; ".join(reasons),
            "salient_evidence": _gather_salient_evidence(facts)
        }
        pass_items.append(pass_obj)

    if args.limit is not None:
        pass_items = pass_items[: max(0, args.limit)]

    out = {
        "$schema": "mbs.hardrule.passlist.v1",
        "encounter_date_used": args.encounter_date or None,
        "config": cfg,
        "facts_digest": {
            "patient_age_years": _extract_age_years(facts),
            "has_any_duration_minutes": any(d.get("minutes") is not None for d in _observed_durations(facts)),
            "attendance_complexity": _observed_attendance_complexity(facts)
        },
        "passed_items": pass_items
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
