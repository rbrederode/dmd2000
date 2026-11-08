import pytest

from api.tm_dig import TM_DIG
from api.tm_sdp import TM_SDP
from env.events import ConfigEvent
from models.comms import CommunicationStatus
from models.dig import DigitiserList, DigitiserModel
from models.telescope import TelescopeModel
from tm.tm import TelescopeManager


def manager():
    tm = TelescopeManager.__new__(TelescopeManager)
    tm.stop = lambda: None
    tm.telmodel = TelescopeModel()
    tm.app_model = tm.telmodel.tel_mgr.app
    tm.dig_api, tm.sdp_api = TM_DIG(), TM_SDP()
    tm.telmodel.dig_store = DigitiserList(dig_list=[DigitiserModel(dig_id='dig001',
        tm_connected=CommunicationStatus.ESTABLISHED)])
    tm.telmodel.sdp.dig_store = DigitiserList(dig_list=[DigitiserModel(dig_id='dig001')])
    tm.telmodel.tel_mgr.sdp_connected = CommunicationStatus.ESTABLISHED
    return tm


@pytest.mark.parametrize('extra, expected', [({}, 'absent'), ({'scanning': None}, 'absent'),
    ({'scanning': False}, False), ({'scanning': True}, 'context')])
def test_partial_gain_only_changes_scanning_when_explicit(extra, expected):
    tm = manager()
    action = tm.process_config(ConfigEvent(category='DIG', new_config={'dig_id': 'dig001', 'gain': 25, **extra}))
    scan = next(message.get_api_call()['value'] for message in action.msgs_to_remote if message.get_to_system() == 'sdp')
    assert scan['gain'] == 25.0
    if expected == 'absent':
        assert 'scanning' not in scan
    elif expected == 'context':
        assert scan['scanning']['obs_id'] == scan['obs_id']
    else:
        assert scan['scanning'] is False


def test_legacy_null_in_sdp_config_response_does_not_poison_tm_model():
    tm = manager()
    digitiser = tm.telmodel.sdp.dig_store.get_dig_by_id('dig001')
    tm.process_sdp_msg(None, {'timestamp': '2026-11-07T18:52:38.227824Z'}, {
        'msg_type': 'rsp', 'action_code': 'set', 'property': 'scan_config', 'status': 'success',
        'value': {'dig_id': 'dig001', 'gain': 25.0, 'scanning': None}}, None)
    assert digitiser.scanning is False
    assert digitiser.gain == 25.0
    # Subsequent status advice must remain consumable, rather than failing every poll.
    tm.process_sdp_msg(None, {'timestamp': '2026-11-07T18:53:07.690791Z'}, {
        'msg_type': 'adv', 'action_code': 'set', 'property': 'status', 'status': 'success',
        'value': tm.telmodel.sdp.to_dict()}, None)
    assert tm.telmodel.sdp.dig_store.get_dig_by_id('dig001').scanning is False
