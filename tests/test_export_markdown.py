"""Markdown export now embeds Topics / Sentiment / Talk time (when present)."""
import time

from app import db
from app.models import ActionItem, Meeting, Segment, Summary
from app.pipeline.process import export_markdown


def _mk_meeting():
    db.reset_connection()
    mid = "m-md-analytics"
    if not db.get_meeting(mid):
        db.create_meeting(Meeting(id=mid, title="Q3 planning", platform="zoom",
                                  started_at=time.time(), duration_sec=120.0))
    db.replace_segments(mid, [
        Segment(start=0, end=10, text="Great work everyone on the launch plan. Thanks team.",
                speaker="Alice", source="batch"),
        Segment(start=10, end=22, text="The launch plan is in great shape. The launch plan is critical.",
                speaker="Alice", source="batch"),
        Segment(start=22, end=30, text="I will ship the release on Friday.",
                speaker="Me", source="batch"),
        Segment(start=30, end=40, text="No blockers from my side, agreed perfect.",
                speaker="Alice", source="batch"),
    ], source="batch")
    db.save_summary(mid, Summary(overview="Q3 launch on track.",
                                 key_points=["Launch on track", "Friday ship date"],
                                 decisions=["Ship Friday"]))
    db.save_action_items(mid, [ActionItem(text="ship the release", owner="Me", due="Fri")])
    return mid


def test_markdown_includes_analytics_sections():
    mid = _mk_meeting()
    md = export_markdown(mid).read_text(encoding="utf-8")
    assert "# Q3 planning" in md
    assert "## Summary" in md
    assert "## Action items" in md
    assert "## Topics" in md and "launch plan" in md          # bigram surfaced
    assert "## Sentiment" in md and "positive" in md          # cue words → positive
    assert "## Talk time" in md and "| Alice |" in md          # markdown table
    assert "## Transcript" in md and "Alice:" in md
    # ordering: analytics before transcript
    assert md.index("## Talk time") < md.index("## Transcript")
