"""All Gemini calls live here: the subjective design score and the outreach draft."""
from __future__ import annotations

import io
import json
import re
import time

from google import genai
from google.genai import errors as genai_errors
from PIL import Image

from core.screenshotter import capture_screenshot
from models import Lead, ScoreBreakdown

# gemini-2.0-flash was retired March 2026, and gemini-2.5-flash stopped
# accepting new users as of this build (2.5-series models are being shut down
# entirely in October 2026). gemini-3.6-flash is the current GA model with
# vision support and a free tier. If you hit rate limits, gemini-3.5-flash-lite
# has a higher free RPD cap at slightly lower quality — swap it in below.
MODEL_NAME = "gemini-3.6-flash"

_client: genai.Client | None = None

# Free-tier Gemini occasionally returns transient errors that have nothing to
# do with your code — 503 ("model overloaded") and 429 ("rate limited") are
# both worth a short retry rather than failing the lead outright.
_RETRYABLE_CODES = {429, 503}
_MAX_ATTEMPTS = 3
_RETRY_DELAYS = [2, 5, 10]  # seconds, one per retry


def configure(api_key: str) -> None:
    global _client
    _client = genai.Client(api_key=api_key)


def _generate_with_retry(**kwargs):
    """Wraps generate_content with a short retry/backoff for transient
    (429/503) errors. Raises on the final attempt so the caller's existing
    try/except (in main.py) still logs and records the failure as usual."""
    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _client.models.generate_content(**kwargs)
        except genai_errors.APIError as exc:
            code = getattr(exc, "code", None)
            last_exc = exc
            if code not in _RETRYABLE_CODES or attempt == _MAX_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAYS[attempt])
    raise last_exc  # pragma: no cover - unreachable, satisfies type checkers


def _parse_json_response(raw_text: str) -> dict:
    """Extract a JSON object from a model response, tolerating markdown fences
    or stray text around it — models don't always follow "ONLY JSON" exactly."""
    raw = raw_text.strip()
    raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fall back to grabbing the first {...} block anywhere in the text.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model response: {raw_text[:300]!r}")
    return json.loads(match.group(0))


def _review_design_visual(screenshot_png: bytes) -> tuple[float, str]:
    image = Image.open(io.BytesIO(screenshot_png))
    prompt = """You are evaluating a small local business's website for how
professional, modern, and trustworthy it looks, based on this screenshot of
the homepage as a visitor would actually see it. Judge layout, visual
polish, use of whitespace, imagery quality, and whether it looks current
vs. dated/templated/neglected.

Respond with ONLY a JSON object, no markdown fences, no extra text:
{"score": <number 0-10>, "note": "<one sentence, specific, visual detail>"}"""
    response = _generate_with_retry(model=MODEL_NAME, contents=[prompt, image])
    data = _parse_json_response(response.text)
    return float(data["score"]), data.get("note", "")


def _review_design_text(text_excerpt: str) -> tuple[float, str]:
    prompt = f"""You are evaluating a small local business's website for how
professional and modern it looks, based only on this extracted page text
(you cannot see images/layout, so judge from writing quality, structure,
and content signals):

---
{text_excerpt}
---

Respond with ONLY a JSON object, no markdown fences, no extra text:
{{"score": <number 0-10>, "note": "<one sentence, specific>"}}"""
    response = _generate_with_retry(model=MODEL_NAME, contents=prompt)
    data = _parse_json_response(response.text)
    return float(data["score"]), data.get("note", "")


def review_design(url: str, text_excerpt: str) -> tuple[float, str]:
    """Judge design/professionalism. Tries a real screenshot + Gemini vision
    first (much more accurate); falls back to the text-only read if Playwright
    can't render the page (timeout, blocked, JS-heavy site that errors, etc.)
    so a single bad site never crashes the whole run.

    Returns (score 0-10, one-line note).
    """
    screenshot = capture_screenshot(url)
    if screenshot:
        try:
            return _review_design_visual(screenshot)
        except Exception:
            pass  # fall through to text-based review below
    return _review_design_text(text_excerpt)


def draft_outreach(
    lead: Lead,
    your_name: str,
    your_business: str,
    your_contact: str,
) -> tuple[str, str]:
    """Draft a personalized outreach message. Returns (subject, body).

    Subject is only meaningful for email; callers can ignore it for social DMs.
    """
    if lead.score:
        weaknesses = "; ".join(lead.score.weaknesses[:4]) or "general dated/underperforming site"
        strengths = "; ".join(lead.score.strengths[:2])
        score_context = f"Website quality score: {lead.score.overall}/10. Key issues: {weaknesses}."
        if strengths:
            score_context += f" What's working: {strengths}."
    else:
        score_context = "This business has no website at all."

    channel = lead.best_channel or "email"
    tone_note = {
        "email": "Write it as a short, professional cold email.",
        "facebook": "Write it as a short, friendly Facebook message — casual but respectful, no email formalities.",
        "instagram": "Write it as a short Instagram DM — casual, brief, no email formalities.",
        "linkedin": "Write it as a concise, professional LinkedIn message.",
    }.get(channel, "Write it as a short, professional message.")

    prompt = f"""Write a cold outreach message from a freelance web designer to a
local business, offering to build/fix their website.

Business name: {lead.name}
Business category: {lead.category or "local business"}
{score_context}

Sender: {your_name}, {your_business}. Contact: {your_contact}.

Requirements:
- {tone_note}
- Reference ONE specific, concrete issue from the context above — not generic
  "your website could be better" language.
- No hype, no exclamation-point overload, no "I hope this finds you well".
- End with a low-pressure call to action (e.g. offer to send a free demo/mockup).
- Keep it under 120 words.
- Sign off with the sender's name.

Respond with ONLY a JSON object, no markdown fences:
{{"subject": "<short email subject, keep it plain even if unused>", "body": "<the message>"}}"""

    response = _generate_with_retry(model=MODEL_NAME, contents=prompt)
    data = _parse_json_response(response.text)
    return data.get("subject", f"Quick note about {lead.name}'s website"), data["body"]
