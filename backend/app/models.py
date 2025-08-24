from pydantic import BaseModel, Field
from typing import List, Optional, Any

class Attachment(BaseModel):
    name: str
    type: str
    content: str  # base64 or plain text

class EvidenceSpan(BaseModel):
    text: str
    start: int
    end: int
    field: Optional[str] = "noteText"

class Suggestion(BaseModel):
    item: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence: List[EvidenceSpan] = []
    conflicts: Optional[List[str]] = None
    allowedWith: Optional[List[str]] = None
    warnings: Optional[List[str]] = None

class CoverageBlock(BaseModel):
    eligible_suggested: int
    eligible_total: int
    missed: Optional[List[str]] = None

class AccuracyBlock(BaseModel):
    correct: Optional[int] = None
    incorrect: Optional[int] = None

class SuggestionResponse(BaseModel):
    suggestions: List[Suggestion]
    coverage: Optional[CoverageBlock] = None
    accuracy: Optional[AccuracyBlock] = None
    meta: Optional[dict] = None
    raw_debug: Optional[Any] = None

class SuggestionRequest(BaseModel):
    noteText: str
    attachments: Optional[List[Attachment]] = None
    options: Optional[dict] = None