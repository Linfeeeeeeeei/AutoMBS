# AutoMBS — AI‑Powered MBS Coding Assistant

This repo contains a minimal end‑to‑end implementation of **AutoMBS**:
- A FastAPI backend that turns a clinical note into **MBS item suggestions**.
- A Vite/React frontend that lets you paste notes, set a few options, and view results with evidence.

The first version of the system focuses on the **Emergency Department** (ED) slice of MBS (items 5001–5036 and selected 14xxx). KB has completed for other MBS departments (full KB file is under backend/app/autombs/KB).

---

## 1) Project layout

```
backend/
  app/
    main.py                 # FastAPI entrypoint (/mbs-codes)
    models.py               # Request/response models
    rules.py                # (Your project-wide rules helpers; imported by main.py)
    autombs/                # Self-contained pipeline used by the API
      extractor_min_v2_test.py
      prompt_extractor_min_v3.txt
      rule_engine_min_v3.py
      reasoning_input_builder.py
      reason_per_candidate.py
      prompt_reason_per_candidate_v1.txt
      pipeline_run.py
      extractor_output_skeleton_min_v3.json
      mbs_emergency_kb.jsonl   # <-- default KB (make sure this file is present)
frontend/
  index.html
  src/
    App.tsx
    AutoMBSApp.tsx
    main.tsx
    styles.css
  package.json
  vite.config.ts
  tailwind.config.js
```

> If your KB file lives elsewhere, pass its path in the request body (`options.kb_path`) or move it to `backend/app/autombs/mbs_emergency_kb.jsonl`.

---

## 2) Prerequisites

- **Python** 3.10+
- **Node** 18+ and **npm**
- **Ollama** running locally with the model `qwen3:4b-instruct`
  ```bash
  # Install: https://ollama.ai
  ollama pull qwen3:4b-instruct
  # optional smoke test
  ollama run qwen3:4b-instruct
  ```

> The backend talks to Ollama at `http://localhost:11434` by default. You can override via the API request (`options.ollama_url`).

---

## 3) Backend — run locally

From the `backend/` directory:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install fastapi uvicorn pydantic requests
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Endpoint

`POST /mbs-codes`

**Body** (example `request.json`):

```json
{
  "noteText": "Headache and leg pain after a bicycle accident ... Age: 11Y",
  "attachments": [],
  "options": {
    "department": "ED",
    "hospital_type": "private",
    "recognised_ed": true,
    "kb_path": "app/autombs/mbs_emergency_kb.jsonl",
    "ollama_url": "http://localhost:11434",
    "model": "qwen3:4b-instruct",
    "use_effective_dates": false,
    "confidence_threshold": 0.6
  }
}
```

**Curl**

```bash
curl -X POST http://localhost:8000/mbs-codes \
  -H 'Content-Type: application/json' \
  --data @request.json
```

**Response (shape)**

```json
{
  "suggestions": [
    {
      "item": "5012",
      "description": "Professional attendance ... more than ordinary but not high",
      "confidence": 0.90,
      "reasoning": "Why this code matches the note",
      "evidence": [
        { "text": "Age: 55Y", "field": "note_facts" },
        { "text": "CT brain, CT facial bones", "field": "note_facts" }
      ],
      "benefit": "Specialist ED attendance; higher schedule fee"
    }
  ],
  "coverage": { "eligible_suggested": 1, "eligible_total": null, "missed": [] },
  "accuracy": null,
  "meta": {
    "source": "autombs-pipeline",
    "confidence_threshold": 0.6,
    "kb_path": "app/autombs/mbs_emergency_kb.jsonl",
    "use_effective_dates": false,
    "department": "ED",
    "hospital_type": "private",
    "recognised_ed": true
  }
}
```

> The backend **does not** currently filter to a single “final decision”. It returns all items the LLM considered applicable, sorted by confidence. Your UI can decide what to display or how to group (e.g., one *attendance* + zero or more *procedures*).

---

## 4) Frontend — run locally

From the `frontend/` directory:

```bash
npm i
npm run dev
```

By default the UI posts to `http://localhost:8000/mbs-codes`. If your backend runs elsewhere, update `API_URL` inside `src/AutoMBSApp.tsx`.

### UI options
The UI includes three inputs at the top:
- **Department** (default `ED`)
- **Hospital type** (default `private`)
- **Confidence threshold** (default `0.6`)

These are sent to the backend in `options` and influence candidate filtering and confidence display.

### Evidence display
The frontend shows **evidence sentences** returned by the backend under each suggestion (no inline highlighting).

### Benefits
Each suggestion can show a short “benefit”. If the backend didn’t include one, the UI can look it up from `mbs_emergency_kb.jsonl` (field `display.benefit` if present).

---

## 5) Tips & Troubleshooting

- **KB file not found**  
  Ensure `options.kb_path` points to an existing file. Common value:
  `app/autombs/mbs_emergency_kb.jsonl` (relative to `backend/` working dir).

- **Ollama / model not found**  
  Make sure Ollama is running and `ollama pull qwen3:4b-instruct` has completed.
  You can change model via `options.model`.

- **CORS**  
  The backend enables permissive CORS for local dev. If you still see CORS errors, confirm the ports (frontend typically `5173`, backend `8000`).

- **Large outputs**  
  The pipeline currently returns *all* considered items. If you want only the “best attendance + related procedures”, add a light selector in the frontend (filter by code range and top‑N by confidence).

- **Effective dates**  
  If you want to enforce them, set `"use_effective_dates": true` and optionally pass `"encounter_date"` at the top level of the request JSON (ISO date string).

---

## 6) Extending

- Add more rules/items to `mbs_emergency_kb.jsonl`.
- Tune prompts in `app/autombs/prompt_*.txt`.
- Swap the model and temperature via request `options`.
- Add your own selector on the frontend (e.g., “attendance + procedures”).

---

## 7) License

Add your project’s license here (MIT/Apache-2.0/etc.).
