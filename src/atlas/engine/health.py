"""Health scores: 0-100 per entity, min-biased fleet aggregate.

Deliberately explainable — a score is a starting deduction list, not a
learned number. The dashboard shows the score; the incidents screen shows
exactly why.
"""

from __future__ import annotations

from atlas.model import Severity
from atlas.store.incidents import IncidentStore

DEDUCT = {Severity.CRITICAL: 40, Severity.WARNING: 10}


async def health_scores(incidents: IncidentStore) -> dict[str, int]:
    """Score per affected entity, plus 'fleet'. Unlisted entities are 100."""
    open_incidents = await incidents.open_incidents()
    scores: dict[str, int] = {}
    for incident in open_incidents:
        entity = incident["entity_key"]
        deduction = DEDUCT.get(Severity(incident["severity"]), 10)
        scores[entity] = max(0, scores.get(entity, 100) - deduction)

    if not scores:
        fleet = 100
    else:
        # min-biased: the fleet is as sick as its sickest member, softened
        # by the average.
        worst = min(scores.values())
        avg = sum(scores.values()) / len(scores)
        fleet = int(worst * 0.6 + avg * 0.4)
    scores["fleet"] = fleet
    return scores
