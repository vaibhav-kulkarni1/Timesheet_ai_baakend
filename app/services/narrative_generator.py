"""
Step 3 — Narrative Generator. The LLM receives ONLY the already-computed
Checklist + Recommendation as JSON and is constrained to explain them, never
to calculate or invent numbers. A cheap regex guardrail checks the response
doesn't contain any number absent from the input facts before it's allowed
into the store.
"""
from __future__ import annotations

import json
import re

from app.config import get_settings
from app.models.schemas import Checklist, Narrative, Recommendation, RuleTrigger

NARRATIVE_PROMPT = """You are writing a short manager-facing explanation for a timesheet \
recommendation. You are given pre-computed facts. Do not invent or \
recalculate any numbers — use only the numbers provided.

Facts: {checklist_json}
Recommendation: {recommendation}
Triggered rule: {triggered_rule}

Return JSON only, no markdown fences, no preamble, matching this shape:
{{"reason": "1-2 sentences explaining why", "next_best_actions": ["action 1", "action 2"]}}
"""

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text))


def _allowed_numbers(checklist: Checklist) -> set[str]:
    """Every numeric value in the checklist, in a couple of plausible
    textual formats (e.g. 8.0 vs 8), so a legitimate restatement of a fact
    isn't flagged as a hallucination."""
    allowed: set[str] = set()
    for value in checklist.model_dump().values():
        if isinstance(value, (int, float)):
            allowed.add(str(value))
            if isinstance(value, float) and value == int(value):
                allowed.add(str(int(value)))
            allowed.add(f"{value:g}")
    return allowed


def _validate_no_hallucinated_numbers(narrative: Narrative, checklist: Checklist) -> tuple[bool, list[str]]:
    """Returns (is_clean, offending_numbers). Small numbers like 1, 2 that
    commonly show up in 'action 1 / action 2' style text or percentages are
    allowed through to avoid false positives on harmless phrasing."""
    allowed = _allowed_numbers(checklist) | {"0", "1", "2", "3"}
    text = narrative.reason + " " + " ".join(narrative.next_best_actions)
    found = _numbers_in(text)
    offending = sorted(found - allowed)
    return (len(offending) == 0, offending)


def _mock_narrative(checklist: Checklist, recommendation: Recommendation, trigger: RuleTrigger) -> Narrative:
    """Deterministic, template-based narrative — no external call. Lets the
    whole pipeline run end-to-end with MOCK_LLM=true."""
    action_map = {
        Recommendation.approve: ["Approve the timesheet."],
        Recommendation.review: [
            "Ask the employee for a brief note explaining the flagged item(s).",
            "Review before approving; return for correction if the explanation doesn't hold up.",
        ],
        Recommendation.flagged: [
            "Return the timesheet for correction.",
            "Ask the employee to resolve the flagged entries and resubmit.",
        ],
        Recommendation.not_ready: [
            "No action needed yet — wait for the employee to submit.",
        ],
    }
    return Narrative(
        reason=f"{recommendation.value}: {trigger.detail}",
        next_best_actions=action_map[recommendation],
    )


def _call_openai(checklist: Checklist, recommendation: Recommendation, trigger: RuleTrigger) -> Narrative:
    from openai import OpenAI  # imported lazily so MOCK_LLM=true never needs the package configured

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    prompt = NARRATIVE_PROMPT.format(
        checklist_json=json.dumps(checklist.model_dump()),
        recommendation=recommendation.value,
        triggered_rule=trigger.detail,
    )

    response = client.chat.completions.create(
        model=settings.openai_model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    payload = json.loads(response.choices[0].message.content)
    return Narrative(reason=payload["reason"], next_best_actions=payload["next_best_actions"])


def generate_narrative(checklist: Checklist, recommendation: Recommendation, trigger: RuleTrigger) -> Narrative:
    """Single entry point the pipeline calls. Falls back to the mock/
    templated narrative if MOCK_LLM is true, if no API key is configured, or
    if the model's response fails the hallucination check — the dashboard
    should never be blocked on the LLM."""
    settings = get_settings()

    if settings.mock_llm or not settings.openai_api_key:
        return _mock_narrative(checklist, recommendation, trigger)

    try:
        narrative = _call_openai(checklist, recommendation, trigger)
    except Exception:
        # Never let an LLM/API failure block a recommendation from being
        # computed — degrade to the deterministic template instead.
        return _mock_narrative(checklist, recommendation, trigger)

    is_clean, offending = _validate_no_hallucinated_numbers(narrative, checklist)
    if not is_clean:
        # Guardrail tripped: the model introduced numbers not present in the
        # facts it was given. Don't trust it — fall back to the template
        # rather than risk showing a manager a wrong number.
        return _mock_narrative(checklist, recommendation, trigger)

    return narrative
