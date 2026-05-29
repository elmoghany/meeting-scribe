"""Custom vocabulary: prompt builder, config term-loading, and the API."""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.pipeline.asr import vocab_prompt
from app.server.main import app


def test_vocab_prompt_builds_glossary():
    p = vocab_prompt(["Andrej Karpathy", "OAuth", "MeetingScribe"])
    assert p.startswith("Glossary of names and terms:")
    assert "Andrej Karpathy" in p and "OAuth" in p and p.endswith(".")


def test_vocab_prompt_none_when_empty():
    assert vocab_prompt([]) is None
    assert vocab_prompt(["", "  "]) is None


def test_vocab_prompt_caps_length():
    p = vocab_prompt([f"term{i}" for i in range(200)], limit=5)
    assert p.count(",") == 4  # 5 terms => 4 separators


def test_vocab_terms_from_file_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETINGSCRIBE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEETINGSCRIBE_VOCAB", "OAuth, Karpathy")
    get_settings.cache_clear()
    s = get_settings()
    (s.data_dir / "vocabulary.txt").write_text("MeetingScribe\nWASAPI\nOAuth\n",
                                               encoding="utf-8")
    terms = s.vocab_terms()
    # env + file merged, case-insensitive dedupe (OAuth appears once)
    assert "OAuth" in terms and "Karpathy" in terms and "WASAPI" in terms
    assert sum(1 for t in terms if t.lower() == "oauth") == 1
    get_settings.cache_clear()


def test_vocab_api_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETINGSCRIBE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MEETINGSCRIBE_VOCAB", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as c:
        r = c.put("/api/vocab", json={"title": "Karpathy, Fridman\nOAuth"})
        terms = r.json()["terms"]
        assert set(terms) == {"Karpathy", "Fridman", "OAuth"}
        assert c.get("/api/vocab").json()["terms"] == terms
    get_settings.cache_clear()
