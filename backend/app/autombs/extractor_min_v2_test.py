#!/usr/bin/env python3
"""
Minimal extractor tester for Qwen3:4b-instruct via Ollama.

Usage:
  python extractor_min_v2_test.py --note sample_note1.txt
  # or
  python extractor_min_v2_test.py --inline "Free text note here"

Env overrides:
  OLLAMA_URL   (default http://localhost:11434)
  OLLAMA_MODEL (default qwen3:4b-instruct)
"""
import os, json, argparse, requests, sys
from pathlib import Path

DEF_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEF_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")

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
    }, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    text = data.get("response", "")
    if not text.strip().startswith("{"):
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            text = text[s:e+1]
    return text

def backfill_indices(note: str, obj):
    """Attach start/end to evidence spans when missing, by first occurrence in note."""
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", type=str, help="Path to a text file with the clinical note")
    ap.add_argument("--inline", type=str, help="Inline note text (overrides --note)")
    ap.add_argument("--prompt", type=str, default="prompt_extractor_min_v2.txt", help="Prompt template path")
    ap.add_argument("--no-fill", action="store_true", help="Do NOT backfill evidence indices")
    ap.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default 0.0)")
    args = ap.parse_args()

    if args.inline:
        note_text = args.inline
    elif args.note:
        note_text = Path(args.note).read_text(encoding="utf-8")
    else:
        print("Provide --inline \"...\" or --note file.txt", file=sys.stderr)
        sys.exit(1)

    prompt = load_prompt(Path(args.prompt), note_text)
    try:
        raw = call_ollama(prompt, model=os.environ.get("OLLAMA_MODEL", DEF_MODEL),
                          url=os.environ.get("OLLAMA_URL", DEF_URL),
                          temperature=args.temperature)
    except requests.exceptions.ConnectionError as e:
        print("Could not reach Ollama at", DEF_URL, "\nIs 'ollama serve' running? Did you pull the model?", e, file=sys.stderr)
        sys.exit(2)

    try:
        data = json.loads(raw)
    except Exception as e:
        print("Model did not return valid JSON. Raw text follows:\n", raw, file=sys.stderr)
        sys.exit(3)

    if not args.no_fill:
        backfill_indices(note_text, data)

    print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
