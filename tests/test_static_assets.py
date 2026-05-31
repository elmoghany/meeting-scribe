"""Guard the dashboard's static assets — pytest can't run the JS, but it CAN
catch a truncated/garbled index.html / app.js / style.css (a real failure mode
when editing them by hand). Asserts each is served and still contains the
hooks the app depends on."""
from fastapi.testclient import TestClient

from app.server.main import app


def test_index_served_with_core_elements():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        html = r.text
    # element ids the JS binds to — their loss = a broken dashboard
    for needle in ('id="meeting-list"', 'id="ml-filter"', 'id="ml-select"',
                   'id="ml-del"', 'id="detail-domspk"', 'id="export-fmt"'):
        assert needle in html, f"index.html missing {needle}"
    assert 'value="html"' in html          # html export option present
    assert html.rstrip().endswith("</html>")


def test_app_js_served_and_intact():
    with TestClient(app) as c:
        r = c.get("/static/app.js")
        assert r.status_code == 200
        js = r.text
    assert len(js) > 15000, "app.js suspiciously short — truncated?"
    # feature wiring that must survive edits
    for needle in ("delete-batch", "ml-filter", "detail-domspk",
                   "refreshMeetings", "openMeeting"):
        assert needle in js, f"app.js missing {needle}"
    # balanced curly braces (cheap truncation tripwire)
    assert js.count("{") == js.count("}"), "unbalanced braces in app.js"


def test_style_css_served_with_new_rules():
    with TestClient(app) as c:
        r = c.get("/static/style.css")
        assert r.status_code == 200
        css = r.text
    for needle in (".domspk", ".ml-filter", "#meeting-list li.picked"):
        assert needle in css, f"style.css missing {needle}"
