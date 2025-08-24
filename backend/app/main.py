from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .models import SuggestionRequest, SuggestionResponse, CoverageBlock
from .rules import mock_suggest

app = FastAPI(title="AutoMBS API", version="0.1.0")

# CORS for local dev (adjust in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/mbs-codes", response_model=SuggestionResponse)
async def mbs_codes(req: SuggestionRequest):
    attachments_text = "\n".join([a.content for a in (req.attachments or []) if a.type.startswith("text")])
    suggestions = mock_suggest(req.noteText, attachments_text)
    coverage = CoverageBlock(
        eligible_suggested=len(suggestions),
        eligible_total=max(len(suggestions), 3),
        missed=[] if len(suggestions) >= 3 else ["(example) spirometry", "(example) care plan"][: 3 - len(suggestions)],
    )
    return SuggestionResponse(
        suggestions=suggestions,
        coverage=coverage,
        accuracy=None,
        meta={"prompt_version": "v-mock-1", "rule_version": "mock-2025-07", "model": "mock"},
        raw_debug={"note_len": len(req.noteText)},
    )