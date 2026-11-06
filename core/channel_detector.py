"""Find how to reach a business, and make a best-effort guess at where they're
most active.

There's no reliable free API for "which platform does this business actually use",
so this scrapes their website (if they have one) for a contact email and social
links, then applies a simple priority order. If nothing is found, the lead is
flagged for manual outreach rather than guessing.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from models import Lead

USER_AGENT = "Mozilla/5.0 (compatible; WebsiteAuditBot/1.0; +outreach-tool)"
TIMEOUT = 10

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"facebook\.com/[\w\.\-/]+", re.IGNORECASE),
    "instagram": re.compile(r"instagram\.com/[\w\.\-/]+", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com/[\w\.\-/]+", re.IGNORECASE),
}

# Preference order when multiple channels are found and there's no stronger signal.
# Small local/service businesses skew toward Facebook/Instagram over LinkedIn.
DEFAULT_PRIORITY = ["email", "facebook", "instagram", "linkedin"]


def detect_channel(lead: Lead) -> Lead:
    if not lead.website:
        lead.best_channel = None
        lead.channel_confidence = (
            "No website to find contact details on — needs manual research."
        )
        return lead

    try:
        resp = requests.get(
            lead.website, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException:
        lead.best_channel = None
        lead.channel_confidence = "Website unreachable — needs manual research."
        return lead

    soup = BeautifulSoup(resp.text, "html.parser")
    html = str(soup)

    # Email
    email_pattern = re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,24}")
    image_exts = (".png", ".jpg", ".svg")

    # (a) Check mailto: links first as the most reliable signal
    for a_tag in soup.find_all("a", href=re.compile(r"^mailto:", re.IGNORECASE)):
        href = a_tag.get("href", "")
        raw_email = href.split(":", 1)[1].split("?", 1)[0].strip()
        match = email_pattern.search(raw_email)
        if match and not match.group(0).lower().endswith(image_exts):
            lead.contact_email = match.group(0)
            break

    # (b) Fall back to visible text if no mailto link found
    if not lead.contact_email:
        visible_text = soup.get_text(separator=" ", strip=True)
        match = email_pattern.search(visible_text)
        if match and not match.group(0).lower().endswith(image_exts):
            lead.contact_email = match.group(0)

    # Social links
    for platform, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        if match:
            url = match.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            lead.social_links[platform] = url

    # Pick best channel by priority order among what was actually found
    found = (["email"] if lead.contact_email else []) + list(lead.social_links.keys())
    for candidate in DEFAULT_PRIORITY:
        if candidate in found:
            lead.best_channel = candidate
            break

    if lead.best_channel:
        lead.channel_confidence = (
            f"Chose '{lead.best_channel}' — found via site scan "
            f"(available: {', '.join(found)})."
        )
    else:
        lead.channel_confidence = (
            "No email or social links found on site — needs manual research."
        )

    return lead
