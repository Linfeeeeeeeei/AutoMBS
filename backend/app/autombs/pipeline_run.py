#!/usr/bin/env python3
"""
End-to-end pipeline: Note -> LLM extractor -> Hard-rule engine -> Per-candidate reasoning inputs -> Reasoning -> Selection

Adds optional encounter context to satisfy ED setting via metadata.

Usage:
  python pipeline_run.py \
    --kb mbs_emergency_kb.jsonl \
    --note sample_note1.txt \
    --build-reasoning-inputs \
    --run-reasoning \
    --context-department ED \
    --context-hospital-type private \
    --context-recognised-ed \
    --reasoning-out reasoning_inputs \
    --reasoning-results reasoning_results

Env:
  OLLAMA_URL   (default http://localhost:11434)
  OLLAMA_MODEL (default qwen3:4b-instruct)
"""
import os, json, argparse, requests, sys, subprocess
from pathlib import Path

DEF_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEF_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")

DEF_PROMPT = "prompt_extractor_min_v3.txt"
RULE_ENGINE = "rule_engine_min_v3.py"
REASONING_BUILDER = "reasoning_input_builder.py"

def load_prompt(prompt_path: Path, note_text: str) -> str:
    tmpl = prompt_path.read_text(encoding="utf-8")
    return tmpl.replace("<<NOTE>>", note_text)

def call_ollama(prompt: str, url: str = DEF_URL, model: str = DEF_MODEL, temperature: float = 0.0) -> str:
    resp = requests.post(f"{url}/api/generate", json={
        "model": model,
        "prompt": prompt,
        "format": "json",
        "options": {"temperature": temperature},
        "stream": False
    }, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    text = data.get("response", "")
    if not text.strip().startswith("{"):
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            text = text[s:e+1]
    return text

def backfill_indices(note: str, obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("evidence", "patient_age_evidence") and isinstance(v, list):
                for span in v:
                    if isinstance(span, dict) and "text" in span and span.get("text"):
                        if not isinstance(span.get("start"), int) or not isinstance(span.get("end"), int):
                            i = note.find(span["text"])
                            if i >= 0:
                                span["start"] = i
                                span["end"] = i + len(span["text"])
            else:
                backfill_indices(note, v)
    elif isinstance(obj, list):
        for it in obj: backfill_indices(note, it)

def run_rule_engine(kb_path: str, facts_path: str, use_effective_dates: bool, encounter_date: str, no_age: bool, no_duration: bool):
    cmd = ["python", RULE_ENGINE, "--kb", kb_path, "--facts", facts_path]
    if use_effective_dates:
        cmd.append("--use-effective-dates")
        if encounter_date:
            cmd += ["--encounter-date", encounter_date]
    if no_age:
        cmd.append("--no-age")
    if no_duration:
        cmd.append("--no-duration")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("Rule engine error:", proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout

def build_reasoning_inputs(facts_path: str, passlist_path: str, kb_path: str, out_dir: str, max_desc_len: int, context_department: str|None, context_hospital_type: str|None, context_recognised_ed: bool):
    cmd = ["python", REASONING_BUILDER, "--facts", facts_path, "--passlist", passlist_path, "--out-dir", out_dir, "--max-desc-len", str(max_desc_len)]
    if kb_path:
        cmd += ["--kb", kb_path]
    if context_department:
        cmd += ["--context-department", context_department]
    if context_hospital_type:
        cmd += ["--context-hospital-type", context_hospital_type]
    if context_recognised_ed:
        cmd += ["--context-recognised-ed"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("Reasoning builder error:", proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout

def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--note", type=str, help="Path to the note text file")
    src.add_argument("--inline", type=str, help="Inline note text")
    ap.add_argument("--kb", required=True, help="Path to KB jsonl")
    ap.add_argument("--prompt", type=str, default=DEF_PROMPT, help="Prompt template path")
    ap.add_argument("--facts-out", type=str, default="facts_last.json", help="Save extractor facts JSON here")
    ap.add_argument("--passlist-out", type=str, default="passlist_last.json", help="Save pass list JSON here")
    ap.add_argument("--use-effective-dates", action="store_true")
    ap.add_argument("--encounter-date", type=str, default=None, help="YYYY-MM-DD, used only if --use-effective-dates")
    ap.add_argument("--no-age", action="store_true")
    ap.add_argument("--no-duration", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--build-reasoning-inputs", action="store_true")
    ap.add_argument("--reasoning-out", type=str, default="reasoning_inputs")
    ap.add_argument("--max-desc-len", type=int, default=500)
    ap.add_argument("--run-reasoning", action="store_true")
    ap.add_argument("--reasoning-in", type=str, default="reasoning_inputs")
    ap.add_argument("--reasoning-results", type=str, default="reasoning_results")
    ap.add_argument("--confidence-threshold", type=float, default=0.6)
    ap.add_argument("--context-department", type=str, default=None)
    ap.add_argument("--context-hospital-type", type=str, default=None)
    ap.add_argument("--context-recognised-ed", action="store_true")
    args = ap.parse_args()

    # Load note
    if args.inline:
        note_text = args.inline
    else:
        note_text = Path(args.note).read_text(encoding="utf-8")

    # Build prompt
    prompt = load_prompt(Path(args.prompt), note_text)

    # Call Ollama (extractor)
    try:
        raw = call_ollama(prompt, model=os.environ.get("OLLAMA_MODEL", DEF_MODEL),
                          url=os.environ.get("OLLAMA_URL", DEF_URL),
                          temperature=args.temperature)
    except requests.exceptions.ConnectionError as e:
        print("Could not reach Ollama at", DEF_URL, "\\nIs 'ollama serve' running? Did you pull the model?", e, file=sys.stderr)
        sys.exit(2)

    try:
        facts = json.loads(raw)
    except Exception as e:
        print("Extractor did not return valid JSON. Raw text follows:\\n", raw, file=sys.stderr)
        sys.exit(3)

    backfill_indices(note_text, facts)
    Path(args.facts_out).write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    # Hard-rule engine
    passlist_text = run_rule_engine(
        kb_path=args.kb,
        facts_path=args.facts_out,
        use_effective_dates=args.use_effective_dates,
        encounter_date=args.encounter_date,
        no_age=args.no_age,
        no_duration=args.no_duration
    )
    Path(args.passlist_out).write_text(passlist_text, encoding="utf-8")
    print(passlist_text)

    # Build per-candidate reasoning inputs (optional)
    if args.build_reasoning_inputs:
        out = build_reasoning_inputs(args.facts_out, args.passlist_out, args.kb, args.reasoning_out, args.max_desc_len, args.context_department, args.context_hospital_type, args.context_recognised_ed)
        print(out)

    # Run per-candidate reasoning (optional)
    if args.run_reasoning:
        idx_path = Path(args.reasoning_in) / "reasoning_inputs_index.json"
        if not idx_path.exists():
            print("No reasoning inputs found. Run with --build-reasoning-inputs first.", file=sys.stderr)
            sys.exit(4)
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        files = idx.get("files", [])
        Path(args.reasoning_results).mkdir(parents=True, exist_ok=True)

        decisions = []
        for f in files:
            item_no = Path(f).stem.replace("candidate_", "")
            out_path = Path(args.reasoning_results) / f"decision_{item_no}.json"
            # call reasoner
            cmd = ["python", "reason_per_candidate.py", "--input", f, "--out", str(out_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                print("Reasoner error for", f, ":", proc.stderr, file=sys.stderr)
                continue
            try:
                decisions.append(json.loads(out_path.read_text(encoding="utf-8")))
            except Exception:
                pass

        # save all decisions
        decisions_path = Path(args.reasoning_results) / "decisions_all.json"
        decisions_path.write_text(json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2), encoding="utf-8")

        # simple selector: choose best attendance (5001-5036) and procedures >= threshold
        def is_attendance(it):
            try:
                n = int(str(it.get("item_number", "0")))
                return 5001 <= n <= 5036
            except Exception:
                return False

        applicable = [d for d in decisions if d.get("applicable") is True and isinstance(d.get("confidence"), (int,float))]
        applicable.sort(key=lambda x: x["confidence"], reverse=True)

        attendance = None
        for d in applicable:
            if is_attendance(d):
                attendance = d
                break

        procedures = [d for d in applicable if not is_attendance(d) and d["confidence"] >= args.confidence_threshold]

        final = {
            "attendance": attendance if attendance else None,
            "procedures": procedures
        }
        final_path = Path(args.reasoning_results) / "final_suggestions.json"
        final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"final": final, "decisions_path": str(decisions_path), "results_dir": args.reasoning_results}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
