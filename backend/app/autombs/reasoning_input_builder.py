#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from typing import Any, Dict, List, Optional

def load_json(p: str):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def load_kb_jsonl(p: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not p: return {}
    idx = {}
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
                num = str(obj.get("item_number"))
                if num: idx[num] = obj
            except Exception:
                continue
    return idx

def _ev_texts(spans):
    out = []
    for s in spans or []:
        t = (s or {}).get("text")
        if t: out.append(t)
    # dedupe
    seen = set(); res = []
    for t in out:
        if t in seen: continue
        seen.add(t); res.append(t)
    return res

def build_note_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    age = facts.get("patient_age_years")
    if age is not None: out["patient_age_years"] = age

    st = []
    for loc in ((facts.get("hard_gates_observed") or {}).get("setting_locations") or []):
        tok = loc.get("token")
        sup = loc.get("support") or "explicit"
        ev = _ev_texts(loc.get("evidence") or [])
        if tok:
            obj = {"token": tok, "support": sup}
            if ev: obj["evidence"] = ev
            st.append(obj)
    if st: out["setting_tokens"] = st

    kp = (facts.get("summary") or {}).get("key_points") or []
    kp = [k for k in kp if isinstance(k, str) and k.strip()]
    if kp: out["key_points"] = kp[:12]

    durs_out = []
    for d in ((facts.get("hard_gates_observed") or {}).get("durations") or []):
        obj = {"label": d.get("label"), "support": d.get("support") or "explicit"}
        if d.get("minutes") is not None: obj["minutes"] = d.get("minutes")
        if d.get("contiguous") is not None: obj["contiguous"] = d.get("contiguous")
        ev = _ev_texts(d.get("evidence") or [])
        if ev: obj["evidence"] = ev
        durs_out.append(obj)
    if durs_out: out["durations"] = durs_out

    ac = ((facts.get("hard_gates_observed") or {}).get("aftercare_without_same_provider") or {})
    if ac and (ac.get("value") is not None or ac.get("support") or ac.get("evidence")):
        obj = {"value": ac.get("value"), "support": ac.get("support") or "explicit"}
        ev = _ev_texts(ac.get("evidence") or [])
        if ev: obj["evidence"] = ev
        if ac.get("rationale"): obj["rationale"] = ac.get("rationale")
        out["aftercare_without_same_provider"] = obj

    cx = ((facts.get("hard_gates_observed") or {}).get("attendance_complexity") or {})
    if cx and (cx.get("value") or cx.get("support") or cx.get("evidence")):
        obj = {"value": cx.get("value"), "support": cx.get("support") or "inferred"}
        ev = _ev_texts(cx.get("evidence") or [])
        if ev: obj["evidence"] = ev
        if cx.get("rationale"): obj["rationale"] = cx.get("rationale")
        out["attendance_complexity"] = obj

    return out

def _inject_context_setting(note_facts: Dict[str, Any], dept: Optional[str], hosp_type: Optional[str], recognised_ed: bool):
    """Inject a metadata-backed setting token if context indicates ED/private."""
    dept_norm = (dept or "").strip().lower()
    hosp_norm = (hosp_type or "").strip().lower()
    tokens = note_facts.setdefault("setting_tokens", [])
    # Generic hospital if we know the hospital type
    if hosp_norm in ("private","public") and all(t.get("token") != "hospital" for t in tokens):
        tokens.append({"token": "hospital", "support": "metadata", "evidence": [f"context: hospital_type={hosp_norm}"]})
    # Recognised ED in a private hospital
    if dept_norm in ("ed","emergency","emergency_department") and hosp_norm == "private":
        if recognised_ed and all(t.get("token") != "recognised_emergency_department_private_hospital" for t in tokens):
            tokens.append({"token": "recognised_emergency_department_private_hospital",
                           "support": "metadata",
                           "evidence": [f"context: department={dept_norm}", f"context: hospital_type={hosp_norm}", "context: recognised_ed=true"]})
    return note_facts

def desc_trim(s: Optional[str], n: int) -> Optional[str]:
    if not s: return None
    s = s.strip()
    return (s if len(s) <= n else s[:n].rstrip() + "…")

def derive_requires_aftercare(desc: Optional[str], kb_item: Optional[Dict[str, Any]]) -> Optional[bool]:
    if kb_item:
        hg = kb_item.get("hard_gates") or {}
        sr = (hg.get("service_requirements") or {})
        val = sr.get("aftercare_without_same_provider")
        if isinstance(val, bool): return val
    if not desc: return None
    s = desc.lower()
    if "without aftercare" in s: return True
    if "with aftercare" in s or "including aftercare" in s: return False
    return None

def get_requires_duration(kb_item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not kb_item: return None
    hg = kb_item.get("hard_gates") or {}
    sr = (hg.get("service_requirements") or {})
    min_d = sr.get("min_duration_minutes")
    max_d = sr.get("max_duration_minutes")
    if (min_d is None) and (max_d is None): return None
    return {"min_minutes": min_d if min_d is not None else None, "max_minutes": max_d if max_d is not None else None}

def get_requires_settings(pass_item: Dict[str, Any], kb_item: Optional[Dict[str, Any]]) -> List[str]:
    hints = (pass_item.get("soft_requirements_hint") or {})
    st = hints.get("setting_tokens")
    if st: return st
    if kb_item:
        hg = kb_item.get("hard_gates") or {}
        sm = (hg.get("setting_mode") or {})
        return sm.get("locations_allowed") or []
    return []

# NEW: extract schedule_fee from KB (handles int/float/string)
def get_schedule_fee(kb_item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not kb_item: return None
    pb = (kb_item.get("pricing_benefit") or {})
    fee = pb.get("schedule_fee")
    if isinstance(fee, (int, float)):
        return float(fee)
    if isinstance(fee, str):
        try:
            return float(fee.strip())
        except Exception:
            return None
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True)
    ap.add_argument("--passlist", required=True)
    ap.add_argument("--kb", default=None)
    ap.add_argument("--out-dir", default="reasoning_inputs")
    ap.add_argument("--max-desc-len", type=int, default=500)
    ap.add_argument("--context-department", type=str, default=None, help="e.g., ED, ICU, ward")
    ap.add_argument("--context-hospital-type", type=str, default=None, help="private|public|other")
    ap.add_argument("--context-recognised-ed", action="store_true", help="Mark as recognised ED if department=ED and private")
    args = ap.parse_args()

    facts = load_json(args.facts)
    passlist = load_json(args.passlist)
    kb_idx = load_kb_jsonl(args.kb) if args.kb else {}

    note_facts = build_note_facts(facts)
    note_facts = _inject_context_setting(note_facts, args.context_department, args.context_hospital_type, args.context_recognised_ed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for p in passlist.get("passed_items", []):
        item_no = str(p.get("item_number"))
        kb_item = kb_idx.get(item_no)
        desc = p.get("description_original")
        trimmed = desc_trim(desc, args.max_desc_len)

        candidate = {"item_number": item_no}
        if trimmed: candidate["description_original"] = trimmed

        # NEW: schedule_fee from KB
        if kb_idx:
            fee = get_schedule_fee(kb_item)
            if fee is not None:
                candidate["schedule_fee"] = fee

        req_settings = get_requires_settings(p, kb_item)
        if req_settings: candidate["requires_setting_tokens"] = req_settings

        req_dur = get_requires_duration(kb_item) if kb_idx else None
        if req_dur: candidate["requires_duration"] = req_dur

        req_aftercare = derive_requires_aftercare(desc, kb_item) if (desc or kb_item) else None
        if req_aftercare is not None:
            candidate["requires_aftercare_without_same_provider"] = req_aftercare

        obj = {"$schema": "mbs.reasoning_input.min.v1",
               "note_facts": note_facts,
               "candidate": candidate}

        out_path = out_dir / f"candidate_{item_no}.json"
        out_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        generated.append(str(out_path))

    idx_path = out_dir / "reasoning_inputs_index.json"
    idx_path.write_text(json.dumps({"files": generated}, indent=2), encoding="utf-8")
    print(json.dumps({"generated": generated, "index": str(idx_path)}, indent=2))

if __name__ == "__main__":
    main()
