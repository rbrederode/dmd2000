import json
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
