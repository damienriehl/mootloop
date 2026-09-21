"""Run inside the public image: python < tools/smoke_demo_runtime.py via docker exec."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
import urllib.error
import urllib.request


def get(path: str) -> bytes:
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=10) as response:
        return response.read()


def main() -> None:
    for attempt in range(20):
        try:
            ready = json.loads(get("/ready"))
            break
        except (urllib.error.URLError, OSError):
            if attempt == 19:
                raise
            time.sleep(0.25)
    assert ready["demos"] == 20
    catalog = json.loads(get("/api/demos"))
    for entry in catalog["entries"]:
        demo_id = entry["descriptor"]["demo_id"]
        revision = entry["revision"]
        base = "/api/demos/" + demo_id + "/" + revision
        snapshot = json.loads(get(base))
        assert snapshot["descriptor"] == entry["descriptor"]
        assert hashlib.sha256(get(base + "/inputs")).hexdigest() == entry["bundle_sha256"]
        assert len(snapshot["strategies"]) == (
            2 if entry["descriptor"]["collection"] == "public-record" else 1
        )
    assert len(json.loads(get("/api/requests"))) == 18
    assert b"Demo library" in get("/demos/")
    home = get("/")
    assert b"Put your argument" in home
    assert b'href="/demos/?collection=business"' in home
    assert b".home-hero" in get("/static/home.css")
    for module in (
        "mootloop.vault",
        "mootloop.orchestrator",
        "mootloop.web.publish",
        "mootloop.web.legacy_prepare",
        "mootloop.replay",
        "mootloop.cli",
    ):
        assert importlib.util.find_spec(module) is None, module
    assert not any(
        key.startswith(("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MOOTLOOP_INTERNAL_SECRET"))
        for key in os.environ
    )
    assert os.getuid() == 10001
    print(
        json.dumps(
            {
                "ready": ready,
                "verified_demo_downloads": 20,
                "strategy_trails": 25,
                "legacy_requests": 18,
                "uid": os.getuid(),
                "writer_modules": "absent",
                "provider_credentials": "absent",
            }
        )
    )


if __name__ == "__main__":
    main()
