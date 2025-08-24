import re
from typing import List, Dict

NEEDLES = {
    "tele": ["telehealth", "video", "phone"],
    "suturing": ["suturing", "laceration", "local anaesthetic", "nylon"],
    "ecg": ["ecg", "sinus rhythm", "palpitations"],
    "path": ["throat swab", "fbc", "crp", "culture", "pathology"],
    "imaging": ["x-ray", "imaging", "report"],
    "consult": ["consult", "in-person", "mins", "review", "history", "exam", "time"],
}

MOCK_ITEMS = {
    "tele": {"item": "91823", "description": "Telehealth attendance by a GP (Level B–C)", "confidence": 0.78,
              "reasoning": "Telehealth modality and duration suggest Level B/C telehealth."},
    "suturing": {"item": "30026", "description": "Repair of superficial laceration (suturing)", "confidence": 0.82,
                  "reasoning": "Simple suturing with local anaesthetic documented."},
    "ecg": {"item": "11700", "description": "Electrocardiogram tracing and report", "confidence": 0.74,
             "reasoning": "ECG performed and interpreted."},
    "path": {"item": "65111", "description": "Pathology test request (example)", "confidence": 0.65,
              "reasoning": "Pathology orders present (throat swab, FBC/CRP)."},
    "imaging": {"item": "58503", "description": "Diagnostic imaging service (example)", "confidence": 0.70,
                 "reasoning": "Imaging performed and report available."},
    "consult": {"item": "23", "description": "GP attendance (Level B/C)", "confidence": 0.68,
                 "reasoning": "Consultation with Hx/Exam and time noted."},
}


def find_spans(text: str, needles: List[str]):
    spans = []
    lower = text.lower()
    for n in needles:
        i = lower.find(n.lower())
        if i >= 0:
            spans.append({"text": text[i:i+len(n)], "start": i, "end": i+len(n), "field": "noteText"})
    return spans


def mock_suggest(note: str, attachments_text: str = "") -> List[Dict]:
    joined = f"{note}\n{attachments_text}".strip()
    out = []
    for key, needles in NEEDLES.items():
        if any(re.search(r"\b" + re.escape(w) + r"\b", joined, flags=re.I) for w in needles):
            base = MOCK_ITEMS[key].copy()
            base["evidence"] = find_spans(joined, needles)
            # tiny heuristic: Level C if time >= 20 mins present
            if key in ("tele", "consult") and re.search(r"\b(20|25|30)\b", joined):
                base["item"] = "36" if key == "consult" else "91836"
            out.append(base)
    # conflict rule: 23 vs 36 same day
    has23 = any(s["item"] == "23" for s in out)
    has36 = any(s["item"] == "36" for s in out)
    if has23 and has36:
        for s in out:
            if s["item"] == "23":
                s["conflicts"] = ["36"]
            if s["item"] == "36":
                s["conflicts"] = ["23"]
    return out