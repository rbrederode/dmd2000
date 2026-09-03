import json
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

from obs import inject
from tm.webhook_handler import WebhookHandler


OBSERVATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "alston"
    / "obs_md01_controller_test.json"
)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def test_build_webhook_payload_normalises_single_observation_file():
    payload, obs_ids = inject.build_webhook_payload(OBSERVATION_FILE)

    assert payload["event"] == "alston-rt.ui.odt"
    assert payload["message"]["_type"] == "ObsList"
    assert len(payload["message"]["obs_list"]) == 1
    assert payload["message"]["obs_list"][0]["_type"] == "ObsModel"
    assert payload["message"]["obs_list"][0]["scheduling_block_start"]["_type"] == "datetime"
    assert obs_ids == ["ODT-2026-07-21T150000Z-dish003-2h"]


def test_replace_now_tokens_uses_one_utc_base_time_for_every_reference():
    injection_time = datetime(2026, 7, 24, 14, 30, 45, tzinfo=timezone.utc)
    definition = {
        "obs_id": "ODT-{{NOW}}-dish001-1m",
        "references": [
            "{{NOW}}",
            "start-{{NOW+1m}}",
            "repeat-{{NOW+1m}}",
            "later-{{NOW+2m}}",
            "NOW",
            "NOW1",
            "{{NOW1}}",
        ],
    }

    replaced = inject.replace_now_tokens(definition, injection_time)

    assert replaced["obs_id"] == "ODT-2026-07-24T143045Z-dish001-1m"
    assert replaced["references"] == [
        "2026-07-24T143045Z",
        "start-2026-07-24T143145Z",
        "repeat-2026-07-24T143145Z",
        "later-2026-07-24T143245Z",
        "NOW",
        "NOW1",
        "{{NOW1}}",
    ]


def test_build_webhook_payload_replaces_now_tokens_before_validation(tmp_path):
    definition = json.loads(OBSERVATION_FILE.read_text(encoding="utf-8"))
    old_obs_id = definition["obs_id"]
    tokenised_obs_id = "ODT-{{NOW+2m}}-dish003-2h"

    def replace_obs_id(value):
        if isinstance(value, dict):
            return {
                key: tokenised_obs_id if key == "obs_id" and child == old_obs_id
                else replace_obs_id(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [replace_obs_id(child) for child in value]
        return value

    definition = replace_obs_id(definition)
    observation_file = tmp_path / "observation-with-now.json"
    observation_file.write_text(json.dumps(definition), encoding="utf-8")

    payload, obs_ids = inject.build_webhook_payload(
        observation_file,
        now=datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc),
    )

    expected_obs_id = "ODT-2026-07-24T143200Z-dish003-2h"
    assert obs_ids == [expected_obs_id]
    assert payload["message"]["obs_list"][0]["obs_id"] == expected_obs_id
    observation = payload["message"]["obs_list"][0]
    assert all(config["obs_id"] == expected_obs_id for config in observation["target_configs"])
    assert all(target["obs_id"] == expected_obs_id for target in observation["targets"])


def test_inject_observation_posts_to_tm_webhook(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"status": "success", "message": "Webhook processed"})

    monkeypatch.setattr(inject, "urlopen", fake_urlopen)

    result, obs_ids = inject.inject_observation(
        OBSERVATION_FILE,
        url="http://tm.example.test:5001/webhook",
        timeout=3.0,
    )

    request_payload = json.loads(captured["request"].data)
    assert captured["request"].full_url == "http://tm.example.test:5001/webhook"
    assert captured["request"].get_header("Content-type") == "application/json"
    assert captured["timeout"] == 3.0
    assert request_payload["event"] == "alston-rt.ui.odt"
    assert request_payload["message"]["_type"] == "ObsList"
    assert result["status"] == "success"
    assert obs_ids == ["ODT-2026-07-21T150000Z-dish003-2h"]


def test_main_reports_connection_failure(monkeypatch, capsys):
    def fail_to_connect(*args, **kwargs):
        raise URLError("connection refused")

    from urllib.error import URLError

    monkeypatch.setattr(inject, "urlopen", fail_to_connect)

    exit_code = inject.main(["-f", str(OBSERVATION_FILE)])

    assert exit_code == 2
    assert "Could not connect to Telescope Manager" in capsys.readouterr().err


def test_injection_payload_enters_tm_event_queue():
    event_queue = Queue()
    handler = WebhookHandler(event_queue=event_queue)
    payload, _ = inject.build_webhook_payload(OBSERVATION_FILE)

    response = handler.app.test_client().post("/webhook", json=payload)

    assert response.status_code == 200
    config_event = event_queue.get_nowait()
    assert config_event.category == "ODT"
    assert config_event.new_config["_type"] == "ObsList"
    assert len(config_event.new_config["obs_list"]) == 1
