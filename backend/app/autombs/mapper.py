# app/autombs/mapper.py
from typing import List, Dict, Any
from ..models import Suggestion, EvidenceSpan, CoverageBlock, SuggestionResponse

def map_decisions_to_response(artefacts: Dict[str, Any], confidence_threshold: float = 0.0) -> SuggestionResponse:
    decisions = (artefacts.get("decisions") or {}).get("decisions") or []
    suggestions: List[Suggestion] = []
    for d in decisions:
        item = str(d.get("item_number") or d.get("item") or "")
        desc = d.get("item_description") or d.get("description_original") or d.get("description") or ""
        conf = float(d.get("confidence") or 0.0)
        schedule_fee = float(d.get("schedule_fee") or 0.0)
        rationale = d.get("rationale") or d.get("reasoning") or ""
        cits = d.get("citations") or d.get("evidence") or []
        ev_spans = [EvidenceSpan(text=str(c), start=0, end=0) for c in cits if isinstance(c, str)]
        suggestions.append(Suggestion(item=item, description=desc, confidence=conf, schedule_fee=schedule_fee, reasoning=rationale, evidence=ev_spans))

    coverage = CoverageBlock(
        eligible_suggested=len([s for s in suggestions if s.confidence >= confidence_threshold]),
        eligible_total=len(suggestions),
        missed=[],
    )
    return SuggestionResponse(
        suggestions=suggestions,
        coverage=coverage,
        accuracy=None,
        meta={"source": "autombs-pipeline", "confidence_threshold": confidence_threshold},
        raw_debug=None,
    )
