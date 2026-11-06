"""Send drafted outreach, or queue it for manual sending.

Composio's SDK surface changes fairly often, so this wraps the actual call in a
narrow function (`_composio_send`) that's easy to swap out if Composio renames
things on their end — check https://docs.composio.dev for the current action
names for your connected apps (Gmail, Outlook, etc.) before relying on this in
production. Everything else in the pipeline works fine without Composio; leads
just fall through to the manual queue instead.
"""

from __future__ import annotations
import os

from models import Lead

# Only channels we're willing to attempt auto-sending for. Social DMs generally
# need per-platform connector setup in Composio and are safer left manual unless
# you've specifically connected and tested that action.
AUTO_SEND_CHANNELS = {"email"}


def _composio_send(
    lead: Lead,
    composio_api_key: str,
    subject: str,
    body: str,
    composio_user_id: str = "default",
) -> tuple[bool, str]:
    """Attempt to send via Composio. Returns (success, notes)."""
    try:
        from composio import Composio
    except ImportError:
        return False, "composio not installed (pip install composio)"

    if not lead.contact_email:
        return False, "no contact email to send to"

    try:
        composio = Composio(api_key=composio_api_key)
        result = composio.tools.execute(
            "GMAIL_SEND_EMAIL",
            user_id=composio_user_id,
            arguments={
                "recipient_email": lead.contact_email,
                "subject": subject,
                "body": body,
            },
            # "latest" tools require this opt-in per Composio's current API.
            # We don't parse Gmail's response beyond success/failure, so it's safe here.
            dangerously_skip_version_check=True,
        )
        if isinstance(result, dict) and result.get(
            "successful", result.get("success", False)
        ):
            return True, "sent via Composio (Gmail)"
        return False, f"Composio reported failure: {result}"
    except (
        Exception
    ) as exc:  # noqa: BLE001 - surface any SDK error as a send failure, not a crash
        return False, f"Composio error: {exc}"


def send_or_queue(
    lead: Lead,
    composio_api_key: str | None,
    composio_user_id: str = "default",
    dry_run: bool = False,
) -> Lead:
    """Mutates and returns `lead` with send_status set appropriately."""
    if not lead.outreach_message:
        lead.send_status = "failed"
        lead.send_notes = "no drafted message"
        return lead

    if dry_run:
        lead.send_status = "manual_queue"
        lead.send_notes = "dry-run — not sent"
        return lead

    if not composio_api_key or lead.best_channel not in AUTO_SEND_CHANNELS:
        lead.send_status = "manual_queue"
        reason = (
            "Composio not configured"
            if not composio_api_key
            else f"channel '{lead.best_channel}' needs manual send"
        )
        lead.send_notes = reason
        return lead

    success, notes = _composio_send(
        lead,
        composio_api_key,
        lead.outreach_subject or "",
        lead.outreach_message,
        composio_user_id=composio_user_id,
    )
    lead.send_status = "sent" if success else "manual_queue"
    lead.send_notes = notes
    return lead
