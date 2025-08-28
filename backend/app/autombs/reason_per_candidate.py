#!/usr/bin/env python3
import os, json, argparse, requests, sys
from pathlib import Path

DEF_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEF_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")
PROMPT_PATH = "prompt_reason_per_candidate_v1.txt"

def load_payload(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))

def build_prompt(payload: dict, prompt_path: str) -> str:
    tmpl = Path(prompt_path).read_text(encoding="utf-8")
    return tmpl.replace("<<INPUT_JSON>>", json.dumps(payload, ensure_ascii=False, indent=2))

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to per-candidate reasoning input JSON")
    ap.add_argument("--out", required=True, help="Path to write decision JSON")
    ap.add_argument("--prompt", default=PROMPT_PATH, help="Prompt template")
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    payload = load_payload(args.input)
    prompt = build_prompt(payload, args.prompt)

    try:
        raw = call_ollama(prompt, model=os.environ.get("OLLAMA_MODEL", DEF_MODEL),
                          url=os.environ.get("OLLAMA_URL", DEF_URL),
                          temperature=args.temperature)
    except requests.exceptions.ConnectionError as e:
        print("Could not reach Ollama at", DEF_URL, "\\nIs 'ollama serve' running? Did you pull the model?", e, file=sys.stderr)
        sys.exit(2)

    try:
        decision = json.loads(raw)
    except Exception as e:
        print("Reasoner returned invalid JSON. Raw text follows:\\n", raw, file=sys.stderr)
        sys.exit(3)

    Path(args.out).write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=False))

if __name__ == "__main__":
    main()
