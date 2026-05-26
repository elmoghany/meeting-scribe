"""Key-free outbound webhook: when a meeting finishes processing, POST its
summary + action items to a user-configured URL (Slack/Discord/Notion/Zapier/
n8n all accept incoming webhooks — no per-vendor API key needed).

Configured via MEETINGSCRIBE_WEBHOOK_URL. Sends a generic JSON payload plus a
``text`` field (Slack/Discord render that field automatically).
"""
from __future__ import annotations

from ..config import get_settings
from ..models import ActionItem, Meeting, Summary


def build_payload(meeting: Meeting | None, summary: Summary | None,
                  action_items: list[ActionItem], meeting_id: str) -> dict:
    title = meeting.title if meeting else meeting_id
    lines = [f"*{title}* — meeting notes ready"]
    if summary and summary.overview:
        lines.append("")
        lines.append(summary.overview)
    if action_items:
        lines.append("")
        lines.append("*Action items:*")
        for a in action_items:
            meta = " · ".join(x for x in [a.owner, a.due] if x)
            lines.append(f"• {a.text}" + (f" ({meta})" if meta else ""))
    return {
        "text": "\n".join(lines),                         # Slack/Discord-friendly
        "meeting_id": meeting_id,
        "title": title,
        "summary": summary.to_dict() if summary else None,
        "action_items": [a.to_dict() for a in action_items],
    }


def notify(meeting_id: str, meeting: Meeting | None, summary: Summary | None,
           action_items: list[ActionItem], url: str | None = None) -> bool:
    """POST the payload to the configured webhook. Returns True on 2xx.
    Never raises — webhook failures must not break processing."""
    target = url or get_settings().webhook_url
    if not target:
        return False
    try:
        import httpx
        payload = build_payload(meeting, summary, action_items, meeting_id)
        r = httpx.post(target, json=payload, timeout=15)
        return 200 <= r.status_code < 300
    except Exception as e:  # pragma: no cover - network dependent
        import sys
        print(f"[webhook] post failed: {e}", file=sys.stderr)
        return False
