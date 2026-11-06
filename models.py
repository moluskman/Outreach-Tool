"""Core data models shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScoreBreakdown:
    """Per-category scores, each 0-10. `overall` is the weighted total."""

    technical: float = 0.0
    performance: float = 0.0
    mobile: float = 0.0
    content: float = 0.0
    trust: float = 0.0
    conversion: float = 0.0
    design: float = 0.0

    overall: float = 0.0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    # Category weights — tune these to taste. Must sum to 1.0.
    WEIGHTS = {
        "technical": 0.15,
        "performance": 0.15,
        "mobile": 0.15,
        "content": 0.15,
        "trust": 0.15,
        "conversion": 0.10,
        "design": 0.15,
    }

    def compute_overall(self) -> float:
        total = sum(getattr(self, cat) * weight for cat, weight in self.WEIGHTS.items())
        self.overall = round(total, 2)
        return self.overall


@dataclass
class Lead:
    """A single business found via discovery, enriched as it moves through the pipeline."""

    name: str
    place_id: str
    address: str = ""
    phone: str = ""
    website: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    category: str = ""

    has_website: bool = False
    score: Optional[ScoreBreakdown] = None

    contact_email: Optional[str] = None
    social_links: dict[str, str] = field(default_factory=dict)  # e.g. {"facebook": "https://..."}
    best_channel: Optional[str] = None  # "email" | "facebook" | "instagram" | "linkedin" | None
    channel_confidence: str = ""  # short note on why this channel was picked

    outreach_message: Optional[str] = None
    outreach_subject: Optional[str] = None  # for email

    send_status: str = "not_attempted"  # not_attempted | sent | manual_queue | failed
    send_notes: str = ""

    def qualifies(self, max_score: float) -> bool:
        if not self.has_website:
            return True
        if self.score is None:
            return False
        return self.score.overall <= max_score
