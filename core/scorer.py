"""Score a business's website 0-10 across categories.

Objective categories (technical, performance, mobile, content, trust, conversion)
are computed from a plain requests/BeautifulSoup fetch — no headless browser needed.
`design` is the one category that benefits from judgment, so it's handed to Gemini
along with a text excerpt of the page.

`design` now uses a real Playwright screenshot + Gemini vision review (see
core/outreach_writer.review_design), with an automatic fallback to a text-only
read if the screenshot fails for a given site.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from models import ScoreBreakdown

TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; WebsiteAuditBot/1.0; +outreach-tool)"


def score_no_website() -> ScoreBreakdown:
    """A business with no website at all: floor score, obvious talking points."""
    sb = ScoreBreakdown()
    sb.weaknesses = ["No website found at all — losing customers who search before calling."]
    sb.compute_overall()
    return sb


def _fetch(url: str) -> tuple[requests.Response | None, float]:
    start = time.time()
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True
        )
        elapsed = time.time() - start
        return resp, elapsed
    except requests.RequestException:
        return None, time.time() - start


def _check_broken_links(soup: BeautifulSoup, base_url: str, sample_size: int = 8) -> tuple[int, int]:
    """Returns (broken_count, checked_count) for a sample of internal links."""
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    domain = urlparse(base_url).netloc
    internal = [
        urljoin(base_url, href)
        for href in links
        if href and not href.startswith(("mailto:", "tel:", "javascript:", "#"))
        and (urlparse(urljoin(base_url, href)).netloc == domain)
    ]
    sample = internal[:sample_size]
    broken = 0
    for link in sample:
        try:
            r = requests.head(
                link, headers={"User-Agent": USER_AGENT}, timeout=5, allow_redirects=True
            )
            if r.status_code >= 400:
                broken += 1
        except requests.RequestException:
            broken += 1
    return broken, len(sample)


def _score_technical(resp: requests.Response, soup: BeautifulSoup, url: str) -> tuple[float, list[str], list[str]]:
    points, strengths, weaknesses = 0.0, [], []
    if url.startswith("https://") or (resp is not None and resp.url.startswith("https://")):
        points += 4
        strengths.append("Site uses HTTPS.")
    else:
        weaknesses.append("No HTTPS — browsers flag this as 'Not Secure'.")

    if resp is not None and resp.status_code == 200:
        points += 3
    else:
        weaknesses.append(f"Site returned an error (status {resp.status_code if resp else 'unreachable'}).")

    broken, checked = _check_broken_links(soup, url) if resp is not None else (0, 0)
    if checked:
        ratio_ok = 1 - (broken / checked)
        points += 3 * ratio_ok
        if broken:
            weaknesses.append(f"{broken} of {checked} sampled links are broken.")
        else:
            strengths.append("No broken links found in sample.")
    else:
        points += 1.5  # neutral if nothing to check

    return min(points, 10), strengths, weaknesses


def _score_performance(load_time: float) -> tuple[float, list[str], list[str]]:
    strengths, weaknesses = [], []
    if load_time <= 1.5:
        score = 10
        strengths.append("Fast page load.")
    elif load_time <= 3:
        score = 7
    elif load_time <= 5:
        score = 4
        weaknesses.append(f"Slow page load (~{load_time:.1f}s).")
    else:
        score = 1
        weaknesses.append(f"Very slow page load (~{load_time:.1f}s) — likely losing visitors.")
    return score, strengths, weaknesses


def _score_mobile(soup: BeautifulSoup) -> tuple[float, list[str], list[str]]:
    strengths, weaknesses = [], []
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if viewport:
        score = 8
        strengths.append("Has a mobile viewport tag.")
        content = viewport.get("content", "")
        if "width=device-width" in content:
            score = 10
    else:
        score = 2
        weaknesses.append("No mobile viewport meta tag — likely not mobile-friendly.")
    return score, strengths, weaknesses


def _score_content(soup: BeautifulSoup) -> tuple[float, list[str], list[str]]:
    points, strengths, weaknesses = 0.0, [], []
    title = soup.find("title")
    if title and title.text.strip():
        points += 3
    else:
        weaknesses.append("Missing or empty page title.")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content", "").strip():
        points += 3
    else:
        weaknesses.append("No meta description (hurts search listing appearance).")

    text = soup.get_text(separator=" ", strip=True)
    word_count = len(text.split())
    if word_count >= 300:
        points += 4
        strengths.append("Reasonable amount of on-page content.")
    elif word_count >= 100:
        points += 2
    else:
        weaknesses.append("Very little text content on the page.")

    # stale copyright year is a common "this hasn't been touched in years" signal
    match = re.search(r"(?:©|copyright)\s*(\d{4})", text, re.IGNORECASE)
    if match:
        year = int(match.group(1))
        if year < datetime.now().year - 3:
            weaknesses.append(f"Copyright year ({year}) suggests the site hasn't been updated recently.")

    return min(points, 10), strengths, weaknesses


def _score_trust(soup: BeautifulSoup, text: str) -> tuple[float, list[str], list[str]]:
    points, strengths, weaknesses = 0.0, [], []
    has_phone = bool(re.search(r"(\+?\d[\d\s\-\(\)]{7,}\d)", text))
    has_email = bool(re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text))
    has_address_hint = bool(re.search(r"\b\d{1,5}\s+\w+\s+(St|Street|Rd|Road|Ave|Avenue)\b", text, re.IGNORECASE))
    privacy_link = soup.find("a", string=re.compile("privacy", re.IGNORECASE))

    if has_phone or has_email:
        points += 5
        strengths.append("Contact details are visible on the page.")
    else:
        weaknesses.append("No visible phone/email on the page — adds friction to getting in touch.")

    if has_address_hint:
        points += 3
        strengths.append("Physical address appears on the page.")
    else:
        weaknesses.append("No physical address visible — reduces local trust signals.")

    if privacy_link:
        points += 2
    else:
        weaknesses.append("No privacy policy link found.")

    return min(points, 10), strengths, weaknesses


def _score_conversion(soup: BeautifulSoup) -> tuple[float, list[str], list[str]]:
    points, strengths, weaknesses = 0.0, [], []
    tel_links = soup.find_all("a", href=re.compile(r"^tel:"))
    mail_links = soup.find_all("a", href=re.compile(r"^mailto:"))
    form = soup.find("form")
    cta_words = re.compile(r"book now|contact us|get a quote|call now|enquire|schedule", re.IGNORECASE)
    cta_text = soup.find(string=cta_words)

    if tel_links:
        points += 3
        strengths.append("Click-to-call phone link present.")
    if mail_links:
        points += 2
    if form:
        points += 3
        strengths.append("Has a contact form.")
    if cta_text:
        points += 2

    if points == 0:
        weaknesses.append("No clear call-to-action (booking, contact form, click-to-call).")

    return min(points, 10), strengths, weaknesses


def score_website(url: str, gemini_design_fn=None) -> ScoreBreakdown:
    """Fetch `url` and compute a full ScoreBreakdown.

    `gemini_design_fn(url: str, text_excerpt: str) -> (score: float, note: str)`
    is injected so this module doesn't have a hard dependency on the Gemini
    client — pass `core.outreach_writer.review_design` from main.py. It now
    handles screenshotting internally (via Playwright) with a text-only
    fallback if the screenshot fails.
    """
    sb = ScoreBreakdown()
    resp, load_time = _fetch(url)

    if resp is None:
        sb.technical, t_s, t_w = 1.0, [], ["Site did not respond — may be down."]
        sb.performance, sb.mobile, sb.content, sb.trust, sb.conversion, sb.design = 0, 0, 0, 0, 0, 0
        sb.weaknesses = t_w
        sb.compute_overall()
        return sb

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    sb.technical, s1, w1 = _score_technical(resp, soup, url)
    sb.performance, s2, w2 = _score_performance(load_time)
    sb.mobile, s3, w3 = _score_mobile(soup)
    sb.content, s4, w4 = _score_content(soup)
    sb.trust, s5, w5 = _score_trust(soup, text)
    sb.conversion, s6, w6 = _score_conversion(soup)

    if gemini_design_fn:
        try:
            sb.design, design_note = gemini_design_fn(url, text[:3000])
            if design_note:
                (sb.strengths if sb.design >= 6 else sb.weaknesses).append(design_note)
        except Exception:
            sb.design = 5.0  # neutral fallback if Gemini call fails
    else:
        sb.design = 5.0

    sb.strengths = s1 + s2 + s3 + s4 + s5 + s6 + sb.strengths
    sb.weaknesses = w1 + w2 + w3 + w4 + w5 + w6 + sb.weaknesses

    sb.compute_overall()
    return sb
