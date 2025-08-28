# app/autombs/server_adapter.py
import json, subprocess, tempfile, os
from pathlib import Path
from typing import Optional, Dict, Any

HERE = Path(__file__).resolve().parent

# We expect your existing pipeline files to live here (HERE):
#   - pipeline_run.py
#   - prompt_extractor_min_v3.txt
#   - prompt_reason_per_candidate_v1.txt
#   - rule_engine_min_v3.py
#   - reasoning_input_builder.py
#   - reason_per_candidate.py
#   - mbs_emergency_kb.jsonl
PIPELINE = HERE / "pipeline_run.py"
KB_DEFAULT = HERE / "mbs_emergency_kb.jsonl"

class AutoMBSError(RuntimeError):
    def __init__(self, msg, logs=None):
        super().__init__(msg)
        self.logs = logs or {}

def run_autombs_subprocess(
    note_text: str,
    kb_path: Optional[str] = None,
    context_department: Optional[str] = None,     # e.g., "ED"
    context_hospital_type: Optional[str] = None,  # "private" | "public"
    context_recognised_ed: bool = False,
    use_effective_dates: bool = False,
    encounter_date: Optional[str] = None,
    no_age: bool = False,
    no_duration: bool = False,
    temperature: float = 0.0,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Wraps your CLI pipeline end-to-end and returns artefacts as dicts:
      { "facts": {...}, "passlist": {...}, "decisions": {"decisions":[...]}, "logs": {...} }
    No selection is applied; ALL decisions are returned.
    """
    if not PIPELINE.exists():
        raise AutoMBSError(f"pipeline_run.py not found at {PIPELINE}. Place your existing files under {HERE}.")

    kb = Path(kb_path) if kb_path else KB_DEFAULT
    if not kb.is_absolute():
        kb = kb.resolve()  # make absolute
    if not kb.exists():
        raise AutoMBSError(f"KB file not found at {kb}")

    with tempfile.TemporaryDirectory(prefix="autombs_") as tmpd:
        tmp = Path(tmpd)
        note_file = tmp / "note.txt"
        note_file.write_text(note_text, encoding="utf-8")

        facts_out = tmp / "facts_last.json"
        passlist_out = tmp / "passlist_last.json"
        reasoning_out = tmp / "reasoning_inputs"
        reasoning_results = tmp / "reasoning_results"

        cmd = [
            "python", str(PIPELINE),
            "--kb", str(kb),
            "--note", str(note_file),
            "--facts-out", str(facts_out),
            "--passlist-out", str(passlist_out),
            "--build-reasoning-inputs",
            "--run-reasoning",
            "--reasoning-out", str(reasoning_out),
            "--reasoning-in", str(reasoning_out),
            "--reasoning-results", str(reasoning_results),
            "--temperature", str(temperature),
            "--confidence-threshold", "0.0",
        ]
        if use_effective_dates:
            cmd.append("--use-effective-dates")
            if encounter_date:
                cmd += ["--encounter-date", encounter_date]
        if no_age:
            cmd.append("--no-age")
        if no_duration:
            cmd.append("--no-duration")
        if context_department:
            cmd += ["--context-department", context_department]
        if context_hospital_type:
            cmd += ["--context-hospital-type", context_hospital_type]
        if context_recognised_ed:
            cmd.append("--context-recognised-ed")

        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        # IMPORTANT: run from the directory that contains the prompt files
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(HERE))
        logs = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode, "cwd": str(HERE), "cmd": cmd}
        if proc.returncode != 0:
            # Bubble up logs so FastAPI can include them in the response
            raise AutoMBSError(f"pipeline_run failed (code {proc.returncode}). See logs.", logs)

        # Collect outputs
        facts = _safe_read_json(facts_out, default={})
        passlist = _safe_read_json(passlist_out, default={"passed_items": []})

        decisions_all = reasoning_results / "decisions_all.json"
        final_path = reasoning_results / "final_suggestions.json"
        if decisions_all.exists():
            decisions = _safe_read_json(decisions_all, default={"decisions": []})
            if isinstance(decisions, list):
                decisions = {"decisions": decisions}
        elif final_path.exists():
            final = _safe_read_json(final_path, default={})
            decs = []
            att = final.get("attendance")
            if isinstance(att, dict):
                decs.append(att)
            for p in (final.get("procedures") or []):
                if isinstance(p, dict): decs.append(p)
            decisions = {"decisions": decs}
        else:
            decisions = {"decisions": []}

        return {"facts": facts, "passlist": passlist, "decisions": decisions, "logs": logs}

def _safe_read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
