"""Capture a screenshot of a website with Playwright, for feeding to Gemini vision.

Requires the Chromium browser binary to be installed once:
    playwright install chromium
(pip install alone is not enough — that only installs the Python driver.)
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1280, "height": 900}
NAV_TIMEOUT_MS = 15000


def capture_screenshot(url: str) -> bytes | None:
    """Returns PNG bytes of the rendered page above the fold, or None on failure
    (timeout, site down, blocked, etc.) — callers should fall back gracefully."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=VIEWPORT)
                page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                # Give lazy-loaded content/fonts a brief moment without hanging on
                # sites where "networkidle" never truly settles (trackers, chat widgets).
                page.wait_for_timeout(1500)
                screenshot = page.screenshot(full_page=False)
                return screenshot
            finally:
                browser.close()
    except Exception:
        return None
