"""Orchestrates: discover businesses -> score websites -> detect channel ->
draft outreach -> send or queue for manual send.

Usage:
    python main.py --query "hairdresser" --location "Berwick, VIC" --max-score 5
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from core import channel_detector, discovery, outreach_writer, scorer, sender
from models import Lead

OUTPUT_DIR = Path("output")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Web design outreach tool")
    p.add_argument("--query", required=True, help='Business type, e.g. "plumber"')
    p.add_argument("--location", required=True, help='e.g. "Berwick, VIC"')
    p.add_argument("--radius", type=int, default=5000, help="Search radius in meters")
    p.add_argument(
        "--max-score",
        type=float,
        default=5.0,
        help="Only outreach sites scoring <= this (0-10)",
    )
    p.add_argument("--limit", type=int, default=20, help="Max businesses to process")
    p.add_argument(
        "--dry-run", action="store_true", help="Score and draft but never send"
    )
    return p.parse_args()


def process_lead(
    lead: Lead, max_score: float, your_name: str, your_business: str, your_contact: str
) -> Lead:
    # 1. Score
    if lead.has_website:
        lead.score = scorer.score_website(
            lead.website, gemini_design_fn=outreach_writer.review_design
        )
    else:
        lead.score = scorer.score_no_website()

    if not lead.qualifies(max_score):
        return lead  # scored fine, not a target — still logged in leads.csv

    # 2. Channel
    lead = channel_detector.detect_channel(lead)

    # 3. Draft
    try:
        subject, body = outreach_writer.draft_outreach(
            lead, your_name, your_business, your_contact
        )
        lead.outreach_subject = subject
        lead.outreach_message = body
    except Exception as exc:  # noqa: BLE001
        lead.send_status = "failed"
        lead.send_notes = f"drafting failed: {exc}"
        print(f"    ! drafting failed for {lead.name}: {exc}")

    return lead


def write_csv(path: Path, leads: list[Lead], fields: list[str], extract) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for lead in leads:
            writer.writerow(extract(lead))


def main() -> None:
    load_dotenv()
    args = parse_args()

    places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    composio_key = os.environ.get("COMPOSIO_API_KEY") or None
    composio_user_id = os.environ.get("COMPOSIO_USER_ID", "default")
    your_name = os.environ.get("YOUR_NAME", "")
    your_business = os.environ.get("YOUR_BUSINESS_NAME", "")
    your_contact = os.environ.get("YOUR_CONTACT_EMAIL", "")

    if not places_key or not gemini_key:
        raise SystemExit(
            "Set GOOGLE_PLACES_API_KEY and GEMINI_API_KEY in your .env file first."
        )

    outreach_writer.configure(gemini_key)

    print(f"Searching for '{args.query}' near '{args.location}'...")
    leads = discovery.find_businesses(
        args.query, args.location, places_key, args.radius, args.limit
    )
    print(f"Found {len(leads)} businesses. Scoring and processing qualifying leads...")

    qualifying = []
    for i, lead in enumerate(leads, 1):
        print(f"  [{i}/{len(leads)}] {lead.name}...")
        lead = process_lead(
            lead, args.max_score, your_name, your_business, your_contact
        )
        if lead.outreach_message:
            lead = sender.send_or_queue(
                lead,
                composio_key,
                composio_user_id=composio_user_id,
                dry_run=args.dry_run,
            )
            qualifying.append(lead)
        time.sleep(0.5)  # basic courtesy delay between site fetches / API calls

    OUTPUT_DIR.mkdir(exist_ok=True)

    # All leads checked, qualifying or not — useful to see what was skipped and why
    write_csv(
        OUTPUT_DIR / "leads.csv",
        leads,
        [
            "name",
            "address",
            "phone",
            "website",
            "has_website",
            "overall_score",
            "qualifies",
        ],
        lambda l: {
            "name": l.name,
            "address": l.address,
            "phone": l.phone,
            "website": l.website or "",
            "has_website": l.has_website,
            "overall_score": l.score.overall if l.score else "",
            "qualifies": l.qualifies(args.max_score),
        },
    )

    manual = [l for l in qualifying if l.send_status == "manual_queue"]
    write_csv(
        OUTPUT_DIR / "manual_outreach.csv",
        manual,
        [
            "name",
            "phone",
            "website",
            "best_channel",
            "contact_email",
            "social_links",
            "subject",
            "message",
            "reason",
        ],
        lambda l: {
            "name": l.name,
            "phone": l.phone,
            "website": l.website or "",
            "best_channel": l.best_channel or "unknown",
            "contact_email": l.contact_email or "",
            "social_links": "; ".join(f"{k}: {v}" for k, v in l.social_links.items()),
            "subject": l.outreach_subject or "",
            "message": l.outreach_message or "",
            "reason": l.send_notes,
        },
    )

    sent = [l for l in qualifying if l.send_status == "sent"]
    write_csv(
        OUTPUT_DIR / "sent_log.csv",
        sent,
        ["name", "channel", "contact", "notes"],
        lambda l: {
            "name": l.name,
            "channel": l.best_channel,
            "contact": l.contact_email or "",
            "notes": l.send_notes,
        },
    )

    # Qualifying leads where drafting itself failed (e.g. Gemini call/parse error) —
    # these never made it into `qualifying` since there's no message to send/queue.
    failed = [
        l for l in leads if l.qualifies(args.max_score) and not l.outreach_message
    ]
    if failed:
        write_csv(
            OUTPUT_DIR / "failed.csv",
            failed,
            ["name", "website", "reason"],
            lambda l: {
                "name": l.name,
                "website": l.website or "",
                "reason": l.send_notes,
            },
        )

    print(
        f"\nDone. {len(leads)} checked, {len(qualifying) + len(failed)} qualified for outreach."
    )
    print(f"  Auto-sent: {len(sent)}")
    print(f"  Needs manual send: {len(manual)} -> output/manual_outreach.csv")
    if failed:
        print(f"  Drafting failed: {len(failed)} -> output/failed.csv")
    print(f"  Full results: output/leads.csv")


if __name__ == "__main__":
    main()
