import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util
import io
from urllib.error import HTTPError

import pytest

from obs import inject


OBSERVATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "alston"
    / "obs_md01_controller_test.json"
)


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setenv('WEBHOOK_SECRET', 'test-secret')


@pytest.fixture
def observation_file(tmp_path):
    # The editable profile example changes during system tests. Keep unit-test
    # identity stable while still exercising its real observation structure.
    def stable_ids(value):
        if isinstance(value, dict):
            return {key: 'obs-test' if key == 'obs_id' else stable_ids(child)
                    for key, child in value.items()}
        if isinstance(value, list):
            return [stable_ids(child) for child in value]
        return value

    path = tmp_path / 'observation.json'
    path.write_text(json.dumps(stable_ids(json.loads(OBSERVATION_FILE.read_text()))))
    return path


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        if isinstance(self.data, bytes):
            return self.data
        return json.dumps(self.data).encode("utf-8")


def test_build_webhook_payload_normalises_single_observation_file(observation_file):
    payload, obs_ids = inject.build_webhook_payload(observation_file)

    assert payload["event"] == "alston-rt.ui.odt"
    assert payload["message"]["_type"] == "ObsList"
    assert len(payload["message"]["obs_list"]) == 1
    assert payload["message"]["obs_list"][0]["_type"] == "ObsModel"
    assert payload["message"]["obs_list"][0]["scheduling_block_start"]["_type"] == "datetime"
    assert obs_ids == ['obs-test']


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


def test_build_webhook_payload_replaces_now_tokens_before_validation(tmp_path, observation_file):
    definition = json.loads(observation_file.read_text(encoding="utf-8"))
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


@pytest.mark.parametrize('token', [None, 'explicit-token'])
def test_inject_observation_posts_to_authenticated_web_app(monkeypatch, observation_file, token):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"status": "success", "message": "ODT configuration processed; actions scheduled"})

    monkeypatch.setattr(inject, "urlopen", fake_urlopen)

    result, obs_ids = inject.inject_observation(
        observation_file,
        url="https://web.example.test/webhook",
        timeout=3.0,
        token=token,
    )

    request_payload = json.loads(captured["request"].data)
    assert captured["request"].full_url == "https://web.example.test/webhook"
    assert captured["request"].get_header("Content-type") == "application/json"
    assert captured['request'].get_header('X-webhook-token') == (token or 'test-secret')
    assert captured["timeout"] == 3.0
    assert request_payload["event"] == "alston-rt.ui.odt"
    assert request_payload["message"]["_type"] == "ObsList"
    assert result["status"] == "success"
    assert obs_ids == ['obs-test']


def test_main_reports_connection_failure(monkeypatch, capsys, observation_file):
    def fail_to_connect(*args, **kwargs):
        raise URLError("connection refused")

    from urllib.error import URLError

    monkeypatch.setattr(inject, "urlopen", fail_to_connect)

    exit_code = inject.main(["-f", str(observation_file)])

    assert exit_code == 2
    assert "Could not connect to web application" in capsys.readouterr().err


def test_injection_reaches_new_web_application(monkeypatch, tmp_path, observation_file):
    from env.events import ResponseEvent
    monkeypatch.chdir(tmp_path)
    path = Path(__file__).resolve().parents[2] / 'web' / 'web_server.py'
    spec = importlib.util.spec_from_file_location('inject_web_server_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.limiter.enabled = False
    runtime = SimpleNamespace(submit_configuration=Mock(return_value=(
        ResponseEvent('success', 'ODT configuration processed; actions scheduled'), 'test-request')))
    module.app.extensions['web_application'] = runtime

    def forward(request, timeout):
        response = module.app.test_client().post('/webhook', data=request.data, headers=dict(request.header_items()))
        assert response.status_code == 200
        return FakeResponse(response.json)

    monkeypatch.setattr(inject, 'urlopen', forward)
    result, obs_ids = inject.inject_observation(observation_file)
    assert result['status'] == 'success' and result['request_id'] == 'test-request'
    category, config = runtime.submit_configuration.call_args.args
    assert category == 'ODT'
    assert config['_type'] == 'ObsList'
    assert config['obs_list'][0]['obs_id'] == obs_ids[0] == 'obs-test'


def test_missing_token_fails_before_sending(monkeypatch, observation_file):
    monkeypatch.delenv('WEBHOOK_SECRET')
    send = Mock()
    monkeypatch.setattr(inject, 'urlopen', send)
    with pytest.raises(RuntimeError, match='WEBHOOK_SECRET'):
        inject.inject_observation(observation_file)
    send.assert_not_called()


@pytest.mark.parametrize('body', [[], {}, {'status': 'success'}, b'error code: 504'])
def test_invalid_response_fails_cleanly(monkeypatch, observation_file, body):
    monkeypatch.setattr(inject, 'urlopen', lambda *_args, **_kwargs: FakeResponse(body))
    with pytest.raises(RuntimeError, match='invalid'):
        inject.inject_observation(observation_file)


def test_http_200_timeout_result_is_failure(monkeypatch, capsys, observation_file):
    monkeypatch.setattr(inject, 'urlopen', lambda *_args, **_kwargs: FakeResponse({
        'status': 'error', 'message': 'TM response timed out; configuration outcome is unknown',
        'code': 'timeout', 'request_id': 'test-request',
    }))
    assert inject.main(['-f', str(observation_file)]) == 2
    error = capsys.readouterr().err
    assert 'timed out' in error and 'code=timeout' in error and 'test-request' in error


def test_main_reports_http_authentication_failure(monkeypatch, capsys, observation_file):
    monkeypatch.setattr(inject, 'urlopen', Mock(side_effect=HTTPError(
        inject.DEFAULT_URL, 401, 'Unauthorized', {}, io.BytesIO(b'{"status":"error","message":"Invalid authentication"}'))))
    assert inject.main(['-f', str(observation_file)]) == 2
    assert 'HTTP 401' in capsys.readouterr().err


def test_main_reports_transport_timeout_as_unknown_outcome(monkeypatch, capsys, observation_file):
    monkeypatch.setattr(inject, 'urlopen', Mock(side_effect=TimeoutError()))
    assert inject.main(['-f', str(observation_file)]) == 2
    assert 'outcome is unknown' in capsys.readouterr().err


def test_rest_defaults_and_cli_token():
    args = inject.parse_args(['-f', 'observation.json', '--token', 'explicit-token'])
    assert args.url == 'http://127.0.0.1:8000/webhook'
    assert args.timeout == 20
    assert args.token == 'explicit-token'
