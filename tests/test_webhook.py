from app.integrations import webhook
from app.models import ActionItem, Meeting, Summary


def test_build_payload_has_text_and_items():
    m = Meeting(id="x", title="Standup", platform="zoom", started_at=1.0)
    p = webhook.build_payload(m, Summary(overview="we shipped v2"),
                              [ActionItem(text="email vendor", owner="Sam", due="Mon")], "x")
    assert p["title"] == "Standup"
    assert "Standup" in p["text"] and "email vendor" in p["text"] and "Sam" in p["text"]
    assert p["action_items"][0]["text"] == "email vendor"


def test_notify_posts_to_url(monkeypatch):
    import httpx
    captured = {}

    class _R:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    ok = webhook.notify("x", None, None, [], url="https://example.com/hook")
    assert ok is True
    assert captured["url"] == "https://example.com/hook"
    assert "meeting_id" in captured["json"]


def test_notify_no_url_is_noop():
    assert webhook.notify("x", None, None, [], url=None) is False


def test_notify_swallows_errors(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "post", boom)
    assert webhook.notify("x", None, None, [], url="https://example.com/hook") is False
