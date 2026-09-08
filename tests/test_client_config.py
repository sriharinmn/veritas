"""The browser must not be told to reach the API at an ambiguous address.

`localhost` is two addresses, and their order is not ours to choose. On Windows
— and on many Linux setups — it resolves to ::1 before 127.0.0.1, so a server
bound only to IPv4 is unreachable at the address the browser tries first.

The browser does fall back, which is what makes this so hard to see: requests
that reuse an already-established connection succeed, while a new connection —
opened for a larger file, say — tries ::1, is refused, and surfaces as "Failed
to fetch". Nothing appears in the server log, because the request never arrived.

The symptom was one evidence pane rendering while the other failed, on the same
page, at the same moment, with the server healthy and the file intact. It took
three passes to find, and the last of them only because the *absence* of a log
line was finally noticed.

An explicit 127.0.0.1 has no resolution order to get wrong. These tests keep it
that way, in every place a client is told where the API lives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Where a browser is told the API's address. Server *bind* addresses are a
# different question and are not covered here.
CLIENT_CONFIG = [
    ROOT / "web" / "lib" / "api.ts",
    ROOT / "docker-compose.yml",
    ROOT / ".env.example",
]

API_URL = re.compile(r"(?:API_BASE_URL[^\n]*?|\?\?\s*)[\"']?(https?://[^\"'\s]+)", re.IGNORECASE)


@pytest.mark.parametrize("path", CLIENT_CONFIG, ids=lambda p: p.name)
def test_no_client_is_pointed_at_localhost(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")

    offenders = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "API_BASE_URL" in line
        and "localhost" in line
        and not line.strip().startswith(("#", "//", "*"))
    ]
    assert not offenders, (
        f"{path.name} points a browser at localhost, which resolves to ::1 before "
        f"127.0.0.1 on Windows and refuses connections to an IPv4-only server:\n  "
        + "\n  ".join(offenders)
    )


def test_the_fallback_in_the_api_client_is_explicit():
    """The default used when no environment variable is set must be explicit
    too — it is what a reviewer gets if they run the web app without a .env."""
    source = (ROOT / "web" / "lib" / "api.ts").read_text(encoding="utf-8")
    match = re.search(r"NEXT_PUBLIC_API_BASE_URL\s*\?\?\s*\"([^\"]+)\"", source)
    assert match, "could not find the API base URL fallback"
    assert "localhost" not in match.group(1), (
        f"the fallback is {match.group(1)!r}; use an explicit 127.0.0.1"
    )


def test_cors_still_allows_a_browser_that_arrived_via_localhost():
    """The page itself is usually opened at http://localhost:3000, so that
    origin must stay allowed even though our own requests use 127.0.0.1.

    Fixing the address the client calls must not break the origin it calls
    *from* — those are different things and conflating them would trade one
    silent CORS failure for another.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    response = TestClient(app).get(
        "/health", headers={"Origin": "http://localhost:3000"}
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_the_configured_web_port_is_allowed_by_cors():
    """The browser's origin has to be on the allow-list, or nothing loads.

    This was `os.getenv("WEB_PORT", "3000")`, which reads the process
    environment and not `.env`. Under compose that is fine, because compose
    passes WEB_PORT explicitly. Started by hand it is not: `.env` said 3010, the
    API allowed 3000, and every response to the browser came back without an
    allow-origin header.

    A blocked fetch is indistinguishable from a dead backend to everything
    except the browser console — the interface renders perfectly, shows no data,
    and leaves the tier badge on "Checking…" — which is why this cost two
    separate evenings before anyone looked at the response headers.
    """
    from api.main import CORS_ORIGINS
    from core.settings import settings

    port = settings().web_port
    assert f"http://localhost:{port}" in CORS_ORIGINS
    assert f"http://127.0.0.1:{port}" in CORS_ORIGINS
    # The default stays allowed too, so overriding one port and not the other
    # cannot lock a reviewer out.
    assert "http://localhost:3000" in CORS_ORIGINS
