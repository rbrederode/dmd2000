"""Abort causes survive event delivery, cleanup, persistence, and resource release."""

from datetime import datetime, timedelta, timezone
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from obs.catalogue import list_observations
from obs.oet import ObservationExecutionTool
from sdk.env.app_processor import AppProcessor
from sdk.env.events import ObsEvent
from sdk.ipc.action import Action
from sdk.models.obs import ACTIVE_OBSERVATION_STATES, ObsModel, ObsState, ObsTransition
from tm.tm import TelescopeManager


@pytest.fixture
def observation(tmp_path):
    now = datetime.now(timezone.utc)
    obs = ObsModel(obs_id='obs-abort-errors', obs_state=ObsState.SCANNING,
                   scheduling_block_start=now - timedelta(minutes=1),
                   scheduling_block_end=now + timedelta(minutes=5))
    store = SimpleNamespace(obs_list=[obs], get_obs_by_id=lambda identifier: obs if identifier == obs.obs_id else None)
    model = SimpleNamespace(get_scan_store_dir=lambda: str(tmp_path), oda=SimpleNamespace(obs_store=store))
    oet = ObservationExecutionTool(model, SimpleNamespace(set_last_err=lambda message: message))
    oet.stop_obs_scanning = Mock()
    oet.reset_sdp_scan = Mock()
    oet.release_resources = Mock(return_value=False)
    return obs, oet, tmp_path / f'{obs.obs_id}-obs.json'


def reload(path):
    return ObsModel.load_from_disk(str(path.parent), path.name)


def abort(obs, oet, reason='SDR stale samples: age 2.196 s exceeds 2.000 s'):
    return oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.ABORT, error_message=reason))


def test_old_observation_files_default_error_fields_to_none():
    legacy = ObsModel().to_dict()
    legacy.pop('last_error_msg')
    legacy.pop('last_error_dt')
    restored = ObsModel.from_dict(legacy)
    assert restored.last_error_msg is None
    assert restored.last_error_dt is None


def test_abort_payload_crosses_processor_queue_without_changing_echo(observation):
    obs, oet, path = observation
    queue = Queue()
    processor = AppProcessor(name='abort-error-test', event_q=queue)
    echo = {'request_id': 'request-1'}
    actions = Action().set_obs_transition(obs=obs, transition=ObsTransition.ABORT,
                                         echo_data=echo, error_message='Original device failure')
    processor.performActions(actions)
    event = queue.get_nowait()
    assert event.user_ref is echo
    assert event.error_message == 'Original device failure'
    oet.process_obs_event(event)
    assert reload(path).last_error_msg == 'Original device failure'


def test_abort_is_saved_before_cleanup_and_preserves_first_cause(observation):
    obs, oet, path = observation
    before = datetime.now(timezone.utc)

    def cleanup(*args):
        saved = reload(path)
        assert saved.obs_state == ObsState.ABORTED
        assert saved.last_error_msg.startswith('SDR stale samples')
        assert saved.last_error_dt.tzinfo == timezone.utc

    oet.stop_obs_scanning.side_effect = cleanup
    abort(obs, oet)
    saved_bytes = path.read_bytes()
    first_dt = obs.last_error_dt
    assert before <= first_dt <= datetime.now(timezone.utc)
    assert reload(path).last_error_dt == first_dt
    assert obs.last_update == first_dt
    result = abort(obs, oet, 'Secondary cleanup error')
    assert obs.last_error_dt == first_dt
    assert path.read_bytes() == saved_bytes
    assert result.is_empty()
    oet.stop_obs_scanning.assert_called_once()


def test_disk_failure_does_not_prevent_stop_actions(observation, monkeypatch, caplog):
    obs, oet, _ = observation
    monkeypatch.setattr(ObsModel, 'save_to_disk', Mock(side_effect=OSError('disk full')))
    result = abort(obs, oet, 'Device read failed')
    oet.stop_obs_scanning.assert_called_once()
    assert result.timer_actions
    assert obs.obs_state == ObsState.ABORTED
    assert obs.last_error_msg == 'Device read failed'
    assert 'disk full' in caplog.text


def test_cleanup_failure_does_not_lose_saved_cause(observation):
    obs, oet, path = observation
    def cleanup(obs, action):
        action.set_timer_action(Action.Timer(name='prepared-stop', timer_action=Action.Timer.TIMER_STOP))
        raise RuntimeError('Cleanup failed')
    oet.stop_obs_scanning.side_effect = cleanup
    actions = abort(obs, oet, 'Original hardware error')
    assert 'prepared-stop' in [timer.get_name() for timer in actions.timer_actions]
    assert reload(path).last_error_msg == 'Original hardware error'
    assert reload(path).obs_state == ObsState.ABORTED


def test_resource_release_retains_abort_details(observation, monkeypatch):
    obs, oet, path = observation
    abort(obs, oet)
    error_dt = obs.last_error_dt
    monkeypatch.setattr(ObsModel, 'save_fits_to_disk', Mock())
    oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.RELEASE_RESOURCES))
    assert obs.obs_state == ObsState.IDLE
    assert reload(path).last_error_dt == error_dt
    assert reload(path).last_error_msg.startswith('SDR stale samples')


def test_reset_clears_and_persists_error_details(observation):
    obs, oet, path = observation
    abort(obs, oet)
    oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.RESET))
    saved = reload(path)
    assert saved.obs_state == ObsState.IDLE
    assert saved.last_error_msg is None
    assert saved.last_error_dt is None
    # A later execution gets its own cause.
    abort(obs, oet, 'New attempt failed')
    assert reload(path).last_error_msg == 'New attempt failed'


def test_rejected_reset_keeps_error(observation):
    obs, oet, path = observation
    abort(obs, oet)
    obs.scheduling_block_end = datetime.now(timezone.utc) - timedelta(seconds=1)
    oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.RESET))
    assert obs.last_error_msg == reload(path).last_error_msg


def test_operator_abort_records_explicit_reason(observation):
    obs, oet, path = observation
    oet.control_observation(obs.obs_id, 'abort', Action())
    assert reload(path).last_error_msg == 'Observation aborted by operator.'


def test_catalogue_exposes_error_fields_in_saved_metadata(observation):
    obs, oet, path = observation
    abort(obs, oet)
    entry = list_observations(path.parent).payload['observations'][0]
    assert entry['last_error_msg'] == obs.last_error_msg
    assert entry['last_error_dt'] == obs.last_error_dt.isoformat()


@pytest.mark.parametrize('state', list(ObsState))
def test_sdp_error_carries_originating_message_only_for_active_observation(state):
    obs = ObsModel(obs_id='obs-sdp-errors', obs_state=state)
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.set_last_err = lambda message: message
    manager.telmodel = SimpleNamespace(sdp=SimpleNamespace(last_update=None),
        oda=SimpleNamespace(obs_store=SimpleNamespace(get_obs_by_id=lambda _: obs)))
    actions = manager.process_sdp_msg(None, {}, {
        'msg_type': 'adv', 'action_code': 'set', 'property': 'status', 'status': 'error',
        'message': 'Processing failed for scan 8', 'obs_data': {'obs_id': obs.obs_id}}, bytearray())
    if state in ACTIVE_OBSERVATION_STATES:
        assert actions.obs_transitions[0].get_error_message() == 'Science Data Processor: Processing failed for scan 8'
    else:
        assert actions.obs_transitions == []
