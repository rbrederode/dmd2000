"""Schedule controls validate live state and reuse execution transitions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from env.events import ObsEvent
from ipc.action import Action
from models.obs import ObsModel, ObsState, ObsTransition, validate_observation_control
from models.scan import ScanModel, ScanState
from models.target import TargetModel, PointingType, OffsetScan
from obs.oet import ObservationExecutionTool
from ui import opt_service


def observation(state=ObsState.EMPTY):
    now = datetime.now(timezone.utc)
    return ObsModel(obs_id='scheduled', obs_state=state, dsh_id='dish001',
                    scheduling_block_start=now-timedelta(minutes=1),
                    scheduling_block_end=now+timedelta(minutes=10))


def executor(obs):
    store = SimpleNamespace(obs_list=[obs])
    store.get_obs_by_id = lambda identifier: next((item for item in store.obs_list if item.obs_id == identifier), None)
    telescope = SimpleNamespace(oda=SimpleNamespace(obs_store=store))
    oet = ObservationExecutionTool(telescope, SimpleNamespace(set_last_err=lambda message: message))
    return oet, store


@pytest.mark.parametrize('state', list(ObsState))
@pytest.mark.parametrize('operation', ['abort', 'restart', 'delete'])
def test_live_state_controls(state, operation):
    obs = observation(state)
    allowed = {'abort': {ObsState.CONFIGURING, ObsState.READY, ObsState.SCANNING},
               'restart': {ObsState.ABORTED}, 'delete': {ObsState.EMPTY, ObsState.IDLE}}
    if state in allowed[operation]:
        validate_observation_control(obs, operation)
    else:
        with pytest.raises(ValueError):
            validate_observation_control(obs, operation)


def test_delete_releases_resources_removes_queue_entry_and_ignores_queued_start():
    obs = observation()
    oet, store = executor(obs)
    oet.release_resources = Mock(return_value=True)
    oet.start_next_obs_timer = Mock()
    waiting = observation(ObsState.IDLE)
    waiting.obs_id = 'waiting'
    store.obs_list.append(waiting)
    action = oet.control_observation(obs.obs_id, 'delete', Action())
    assert store.obs_list == [waiting]
    oet.release_resources.assert_called_once_with(obs, action)
    assert [(event.get_obs().obs_id, event.get_transition()) for event in action.obs_transitions] == [('waiting', ObsTransition.ASSIGN_RESOURCES)]
    oet.start_next_obs_timer.assert_called_once_with(action)
    # A START already queued by the scheduling timer must not revive it.
    ignored = oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.START))
    assert obs.obs_state == ObsState.EMPTY
    assert not ignored.obs_transitions
    with pytest.raises(ValueError, match='no longer'):
        oet.control_observation(obs.obs_id, 'delete', Action())


@pytest.mark.parametrize('field,value', [('read_start', datetime.now(timezone.utc)), ('tgt_acq_dt', datetime.now(timezone.utc)), ('status', ScanState.COMPLETE)])
def test_idle_observation_with_execution_history_cannot_be_deleted(field, value):
    obs = observation(ObsState.IDLE)
    scan = ScanModel(scan_id='scan')
    setattr(scan, field, value)
    obs.target_scans = [SimpleNamespace(scans=[scan])]
    with pytest.raises(ValueError, match='not started'):
        validate_observation_control(obs, 'delete')


def test_abort_and_restart_apply_existing_workflows_before_acknowledgement(monkeypatch):
    obs = observation(ObsState.SCANNING)
    oet, _ = executor(obs)
    oet.stop_obs_scanning = Mock()
    oet.reset_sdp_scan = Mock()
    monkeypatch.setattr(ObsModel, 'determine_scans', Mock())
    oet.control_observation(obs.obs_id, 'abort', Action())
    assert obs.obs_state == ObsState.ABORTED
    oet.stop_obs_scanning.assert_called_once()
    result = oet.control_observation(obs.obs_id, 'restart', Action())
    assert obs.obs_state == ObsState.IDLE
    assert [(entry.get_obs(), entry.get_transition()) for entry in result.obs_transitions] == [(obs, ObsTransition.ASSIGN_RESOURCES)]


def test_restart_clears_scan_cursor_gain_cache_and_offset_epoch(monkeypatch):
    obs = observation(ObsState.ABORTED)
    target = TargetModel(tgt_idx=0, pointing=PointingType.OFFSET_SCAN)
    target.scan = OffsetScan(start=datetime.now(timezone.utc))
    obs.targets = [target]
    obs.tgt_idx, obs.tgt_scan = 2, 3
    obs.auto_gain_cache = {'AUTO1': 35}
    determine = Mock()
    monkeypatch.setattr(ObsModel, "determine_scans", determine)
    oet, _ = executor(obs)
    oet.reset_sdp_scan = Mock()
    result = oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.RESET))
    assert (obs.tgt_idx, obs.tgt_scan, obs.auto_gain_cache) == (0, 0, {})
    assert target.scan.start is None
    assert obs.obs_state == ObsState.IDLE
    determine.assert_called_once()
    oet.reset_sdp_scan.assert_called_once_with(obs=obs, action=result)
    assert result.obs_transitions[0].get_transition() == ObsTransition.ASSIGN_RESOURCES


@pytest.mark.parametrize('end', [None, datetime.now(timezone.utc)-timedelta(seconds=1)])
def test_expired_restart_rejected_without_mutating_scan_state(end):
    obs = observation(ObsState.ABORTED)
    obs.scheduling_block_end = end
    obs.tgt_scan = 7
    oet, _ = executor(obs)
    with pytest.raises(ValueError, match='scheduling block'):
        oet.control_observation(obs.obs_id, 'restart', Action())
    result = oet.process_obs_event(ObsEvent(obs=obs, transition=ObsTransition.RESET))
    assert obs.tgt_scan == 7 and obs.obs_state == ObsState.ABORTED
    assert not result.obs_transitions


@pytest.mark.parametrize('operation,model', [('delete','ObservationDelete'), ('abort','ObservationAbort'), ('restart','ObservationRestart')])
def test_bridge_forwards_only_the_named_observation(tmp_path, monkeypatch, operation, model):
    calls = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {'status':'success', 'message':'Actions scheduled'}))
    monkeypatch.setattr(opt_service.requests, 'request', calls)
    app = opt_service.create_app([tmp_path], tmp_path/'cache', web_token='private-test-token')
    try:
        client = app.test_client()
        reply = client.post('/api/observation-control/scheduled', json={'operation':operation})
        assert reply.status_code == 200
        args, kwargs = calls.call_args
        assert args == ('POST', 'http://127.0.0.1:8000/webhook')
        assert kwargs['json'] == {'event':'alston-rt.ui.oet', 'message':{'_type':model, 'obs_id':'scheduled'}}
        assert kwargs['allow_redirects'] is False
        assert b'private-test-token' not in reply.data
        calls.reset_mock()
        assert client.post('/api/observation-control/scheduled', json={'operation':'stop'}).status_code == 400
        assert client.post('/api/observation-control/scheduled', json={'operation':'abort','all':True}).status_code == 400
        calls.assert_not_called()
    finally:
        app.extensions['opt_jobs'].close()
