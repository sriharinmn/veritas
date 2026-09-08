"""Uploading a document.

The upload endpoint takes attacker-controlled bytes and an attacker-controlled
filename and writes both to disk, so its validation is worth pinning down
precisely. The extraction itself is covered elsewhere — what matters here is
what happens at the boundary, and that a bad input fails on the request rather
than four seconds later inside a stream that had already told the reader
everything was fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.ingest import UPLOADS, _safe_stem
from api.main import app

SEED = Path(__file__).resolve().parents[1] / "seed"
DECK = SEED / "delhivery" / "03-delhivery-q4-fy24-earnings-presentation.pdf"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A client whose uploads land in a temp directory, not the real corpus."""
    monkeypatch.setattr("api.ingest.UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr("api.ingest.CORPUS_DIR", tmp_path / "corpus")
    return TestClient(app)


# ── the filename is a path, and paths are dangerous ──────────────────────────


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("annual-report.pdf", "annual-report"),
        ("../../../etc/passwd.pdf", "passwd"),
        ("..\\..\\windows\\system32.pdf", "system32"),
        ("report 2024 (final).pdf", "report-2024--final"),
        ("....pdf", "document"),
        ("", "document"),
        ("/absolute/path/doc.pdf", "doc"),
    ],
)
def test_a_filename_never_escapes_the_upload_directory(given, expected):
    """Traversal is discarded rather than sanitised.

    Path.stem already drops the directory, and everything that is not
    alphanumeric, dash or underscore is replaced — so the result cannot contain
    a separator, a drive letter or a parent reference regardless of input.
    """
    stem = _safe_stem(given)
    assert stem == expected
    assert "/" not in stem and "\\" not in stem and ".." not in stem


def test_a_very_long_filename_is_bounded():
    assert len(_safe_stem("a" * 500 + ".pdf")) == 80


# ── rejection at the boundary ────────────────────────────────────────────────


def test_a_non_pdf_is_refused(client):
    r = client.post("/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_an_empty_file_is_refused(client):
    r = client.post("/documents", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_something_that_is_not_really_a_pdf_fails_on_the_request(client):
    """Not four seconds later, inside a stream that opened hopefully.

    A renamed .docx is a plausible thing for a reviewer to drop on the page, and
    the error should arrive while they are still looking at the upload button.
    """
    r = client.post(
        "/documents",
        files={"file": ("renamed.pdf", b"PK\x03\x04 this is a zip", "application/pdf")},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "PDF" in detail or "corrupt" in detail


def test_an_oversized_upload_is_refused_and_leaves_nothing_behind(client, monkeypatch, tmp_path):
    monkeypatch.setattr("api.ingest.MAX_BYTES", 1024)
    r = client.post(
        "/documents",
        files={"file": ("big.pdf", b"%PDF-1.4" + b"x" * 5000, "application/pdf")},
    )
    assert r.status_code == 413
    assert not list((tmp_path / "uploads").glob("*.pdf")), "the partial file must be removed"


# ── job lookups ──────────────────────────────────────────────────────────────


def test_an_unknown_job_is_a_404_rather_than_an_empty_stream(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/jobs/nope/events").status_code == 404


@pytest.mark.skipif(not DECK.exists(), reason="seed corpus not present")
def test_a_real_pdf_is_accepted_and_returns_a_job(client):
    with DECK.open("rb") as f:
        r = client.post("/documents", files={"file": (DECK.name, f, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["job"] and body["bytes"] > 0

    status = client.get(f"/jobs/{body['job']}")
    assert status.status_code == 200
    assert status.json()["status"] in ("queued", "running", "done", "failed")
