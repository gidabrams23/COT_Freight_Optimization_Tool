import json
import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def test_load_tutorial_manifest_missing_file_returns_safe_payload(tmp_path):
    missing_path = tmp_path / "missing_tutorial_manifest.json"
    payload = app_module._load_tutorial_manifest(str(missing_path))

    assert payload["modules"] == []
    assert payload["error"] == "Tutorial content is unavailable right now."


def test_load_tutorial_manifest_skips_invalid_modules_and_steps(tmp_path):
    manifest_path = tmp_path / "tutorial_manifest.json"
    manifest = {
        "version": 1,
        "modules": [
            {
                "slug": "valid-module",
                "title": "Valid Module",
                "route_hint": "/orders",
                "summary": "Valid summary",
                "audience": ["all"],
                "steps": [
                    {
                        "id": "good-step",
                        "title": "Good Step",
                        "instruction": "Do the thing.",
                        "media": {
                            "type": "image",
                            "src": "tutorial/settings/01-settings-overview.svg",
                            "alt": "Valid image",
                        },
                    },
                    {
                        "id": "bad-step",
                        "title": "Bad Step",
                        "instruction": "Missing media src.",
                        "media": {"type": "image"},
                    },
                ],
            },
            {
                "slug": "invalid-module",
                "title": "Invalid Module",
                "summary": "",
                "steps": [],
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    payload = app_module._load_tutorial_manifest(str(manifest_path))

    assert payload["error"] == ""
    assert len(payload["modules"]) == 1
    module = payload["modules"][0]
    assert module["slug"] == "valid-module"
    assert len(module["steps"]) == 1
    step = module["steps"][0]
    assert step["id"] == "good-step"
    assert step["media"]["exists"] is True
