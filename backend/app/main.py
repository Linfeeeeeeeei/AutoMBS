# app/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import os, json, tempfile, subprocess, textwrap, shutil
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AutoMBS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# -------------------- Request / Response models --------------------

class Options(BaseModel):
    department: Optional[str] = None
    hospital_type: Optional[str] = None
    recognised_ed: Optional[bool] = None
    kb_path: Optional[str] = None
    ollama_url: Optional[str] = None
    model: Optional[str] = None
    use_effective_dates: bool = False
    confidence_threshold: float = 0.8
    # NEW:
    return_mode: str = Field("final", description='“final” (default) or “all”')
    include_debug: bool = False
    temperature: float = 0.0

class MbsCodesRequest(BaseModel):
    noteText: str
    attachments: Optional[List[Dict[str, Any]]] = []
    options: Options

# -------------------- Helpers --------------------

def _resolve_kb_path(opt: Options) -> str:
    if opt.kb_path:
        return opt.kb_path
    # default KB in repo
    return os.path.join("app", "autombs", "mbs_emergency_kb.jsonl")

def _write_note_tmp(tmpdir: str, note: str) -> str:
    note_path = os.path.join(tmpdir, "note.txt")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)
    return note_path

def _run_pipeline(tmpdir: str, req: MbsCodesRequest) -> Dict[str, Any]:
    kb_path = _resolve_kb_path(req.options)
    kb_abs = os.path.abspath(kb_path)
    if not os.path.exists(kb_abs):
        raise HTTPException(status_code=400, detail=f"KB file not found at {kb_abs}")

    note_path = _write_note_tmp(tmpdir, req.noteText)

    facts_out = os.path.join(tmpdir, "facts_last.json")
    passlist_out = os.path.join(tmpdir, "passlist_last.json")
    reasoning_inputs_dir = os.path.join(tmpdir, "reasoning_inputs")
    reasoning_results_dir = os.path.join(tmpdir, "reasoning_results")

    cmd = [
        "python",
        os.path.abspath(os.path.join("app", "autombs", "pipeline_run.py")),
        "--kb", kb_abs,
        "--note", note_path,
        "--facts-out", facts_out,
        "--passlist-out", passlist_out,
        "--build-reasoning-inputs",
        "--run-reasoning",
        "--reasoning-out", reasoning_inputs_dir,
        "--reasoning-in", reasoning_inputs_dir,
        "--reasoning-results", reasoning_results_dir,
        "--temperature", str(req.options.temperature),
        "--confidence-threshold", str(req.options.confidence_threshold),
    ]

    # Context flags
    if req.options.department:
        cmd += ["--context-department", req.options.department]
    if req.options.hospital_type:
        cmd += ["--context-hospital-type", req.options.hospital_type]
    if req.options.recognised_ed:
        cmd += ["--context-recognised-ed"]
    if req.options.use_effective_dates:
        cmd += ["--use-effective-dates"]

    # Env for Ollama/model if provided (pipeline reads from env or prompt files)
    env = os.environ.copy()
    if req.options.ollama_url:
        env["AUTOMBS_OLLAMA_URL"] = req.options.ollama_url
    if req.options.model:
        env["AUTOMBS_MODEL"] = req.options.model

    proc = subprocess.run(
        cmd,
        cwd=os.path.abspath(os.path.join("app", "autombs")),
        capture_output=True,
        text=True,
        env=env
    )

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "facts_path": facts_out,
        "passlist_path": passlist_out,
        "reasoning_inputs_dir": reasoning_inputs_dir,
        "reasoning_results_dir": reasoning_results_dir,
    }

def _json_chunks_from_stdout(stdout: str) -> List[Dict[str, Any]]:
    """pipeline prints several JSON blobs separated by blank lines; parse the ones that are valid."""
    chunks = []
    for part in stdout.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        try:
            obj = json.loads(part)
            chunks.append(obj)
        except Exception:
            # ignore non-JSON lines
            pass
    return chunks

def _extract_final(chunks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Grab the last chunk with a 'final' key (that’s what pipeline prints at the end)."""
    final_obj = None
    for ch in chunks:
        if isinstance(ch, dict) and "final" in ch:
            final_obj = ch["final"]
    return final_obj

def _shape_final_as_suggestions(final_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions = []
    # attendance (single)
    att = final_obj.get("attendance")
    if att and att.get("applicable") is True:
        suggestions.append({
            "item": att.get("item_number"),
            "description": att.get("item_description"),
            "confidence": att.get("confidence"),
            "schedule_fee": att.get("schedule_fee"),
            "reasoning": att.get("rationale"),
            "evidence": [{"text": t, "field": "note_facts"} for t in (att.get("citations") or [])]
        })
    # procedures (list)
    for proc in (final_obj.get("procedures") or []):
        if proc.get("applicable") is True:
            suggestions.append({
                "item": proc.get("item_number"),
                "description": proc.get("item_description"),
                "confidence": proc.get("confidence"),
                "schedule_fee": proc.get("schedule_fee"),
                "reasoning": proc.get("rationale"),
                "evidence": [{"text": t, "field": "note_facts"} for t in (proc.get("citations") or [])]
            })
    return suggestions

def _shape_all_candidates(stdout_chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the full list that the reasoning layer produced (debug mode)."""
    # The pipeline’s last-but-one chunk is the big passlist; the last chunk points to decisions file.
    # The adapter previously read decisions_all.json separately; to keep this simple, we’ll pull
    # the final chunk’s paths and open decisions_all.json if present.
    decisions_path = None
    for ch in stdout_chunks:
        if isinstance(ch, dict) and "decisions_path" in ch:
            decisions_path = ch["decisions_path"]
    items = []
    if decisions_path and os.path.exists(decisions_path):
        try:
            with open(decisions_path, "r", encoding="utf-8") as f:
                decisions = json.load(f)
            # decisions is a dict with "decisions": [ ... ]
            for d in (decisions.get("decisions") or []):
                items.append({
                    "item": d.get("item_number"),
                    "description": d.get("item_description"),
                    "confidence": d.get("confidence"),
                    "schedule_fee": d.get("schedule_fee"),
                    "reasoning": d.get("rationale"),
                    "evidence": [{"text": t, "field": "note_facts"} for t in (d.get("citations") or [])],
                    "applicable": d.get("applicable"),
                    "missing_requirements": d.get("missing_requirements") or []
                })
        except Exception:
            pass
    return items

# -------------------- Endpoint --------------------

@app.post("/mbs-codes")
def post_mbs_codes(req: MbsCodesRequest):
    tmpdir = tempfile.mkdtemp(prefix="autombs_")
    try:
        run = _run_pipeline(tmpdir, req)
        if run["returncode"] != 0:
            return {
                "suggestions": [],
                "coverage": {"eligible_suggested": 0, "eligible_total": 0, "missed": []},
                "accuracy": None,
                "meta": {"error": "pipeline_run failed (code {}). See logs.".format(run["returncode"])},
                "raw_debug": req.options.include_debug and {"pipeline_logs": run} or None
            }

        chunks = _json_chunks_from_stdout(run["stdout"])
        final_obj = _extract_final(chunks)

        # Shape response based on return_mode
        if req.options.return_mode == "all":
            suggestions = _shape_all_candidates(chunks)
        else:
            # default "final"
            suggestions = _shape_final_as_suggestions(final_obj or {})

        # Minimal meta
        meta = {
            "source": "autombs-pipeline",
            "confidence_threshold": req.options.confidence_threshold,
            "kb_path": _resolve_kb_path(req.options),
            "use_effective_dates": req.options.use_effective_dates,
            "encounter_date": None,
            "department": req.options.department,
            "hospital_type": req.options.hospital_type,
            "recognised_ed": req.options.recognised_ed,
        }

        resp = {
            "suggestions": suggestions,
            "coverage": {"eligible_suggested": len(suggestions), "eligible_total": None, "missed": []},
            "accuracy": None,
            "meta": meta,
        }
        if req.options.include_debug:
            resp["raw_debug"] = {"pipeline_logs": run, "stdout_chunks": chunks, "final": final_obj}

        return resp

    finally:
        # tmpdir is auto-cleaned; keep for debugging by commenting next line
        shutil.rmtree(tmpdir, ignore_errors=True)
