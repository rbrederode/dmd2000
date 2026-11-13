"""OPT terminal measurements must delegate to the existing profiler estimators."""
import json

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np
import pytest
from scipy.special import erfc

from obs.terminal_velocity import measure_terminal_velocity, annotate_terminal_velocity
from sdp.channel_mask import ChannelFlag, empty_channel_flags, masked_values
from util import lab_profiler as lab


def record():
    velocity = np.linspace(-100.,150.,1001)
    power = .3 + np.random.default_rng(42).normal(0,.015,len(velocity))
    power += np.where(velocity >= 0., 2*erfc((velocity-65.)/8.), 0.)
    flags = empty_channel_flags(power.shape)
    flags[:30] = flags[-30:] = ChannelFlag.BANDPASS_EXCLUDED
    flags[850:853] = ChannelFlag.RFI_DETECTED
    power[850:853] = 100.  # This detached RFI must not become the terminal edge.
    return dict(velocity=velocity,power=power,channel_flags=flags,
                qa_settings=dict(noise_estimator='median_mad',window_frac=.15,smooth_window=9))


@pytest.mark.parametrize('method',['threshold','connected','erfc'])
def test_terminal_matches_shared_profiler_with_flags_limits_and_qa_policy(method):
    data = record()
    original = data['power'].copy()
    powers = np.ma.asarray(masked_values(data['power'],data['channel_flags'])).filled(np.nan)
    if method == 'erfc':
        expected = lab.fit_terminal_erfc(data['velocity'],powers,search_range=[0.,100.])
    else:
        estimator = lab.estimate_terminal_velocity if method == 'threshold' else lab.estimate_connected_terminal_velocity
        expected = estimator(data['velocity'],powers,-60.,100.,**data['qa_settings'])
    actual = measure_terminal_velocity(data,method,-60.,100.)
    assert actual == dict(expected,method=method,velocity_range_kms=[-60.,100.],source='observation')
    np.testing.assert_array_equal(data['power'],original)
    json.dumps(actual,allow_nan=False)
    figure, axis = plt.subplots()
    try:
        annotate_terminal_velocity(axis,actual)
        assert axis.texts[0].get_gid() == 'terminal-velocity'
        assert 'Terminal velocity' in axis.texts[0].get_text()
    finally:
        plt.close(figure)


def test_negative_only_range_reports_no_positive_edge():
    result = measure_terminal_velocity(record(),'threshold',-80.,-10.)
    assert result['status'] == 'no_positive_velocity_range'
    assert result['velocity_lsr_kms'] is None


@pytest.mark.parametrize('method',['threshold','connected','erfc',None])
def test_reanalysis_only_measures_the_selected_aggregate(tmp_path,monkeypatch,method):
    from ui.opt_reanalysis import reanalyse
    from ui.opt_service import atomic_json
    import obs.terminal_velocity as module
    source,out=tmp_path/'source',tmp_path/'out'
    source.mkdir();out.mkdir()
    data=record()
    arrays={};labels=[];rows=[]
    for index in range(2):
        arrays.update({f'{index}_velocity':data['velocity'],f'{index}_power':data['power'],
                       f'{index}_channel_flags':data['channel_flags']})
        labels.append(dict(id=f'sky{index}',target_id=index,freq_scan=0,longitude=30.,latitude=0.,center_freq=1420e6,
                           qa_settings=data['qa_settings']))
        rows.append(dict(id=f'sky{index}',kind='SKY',target_id=index,freq_scan=0,views={}))
    np.savez_compressed(source/'native-spectra.npz',**arrays)
    atomic_json(source/'native-spectra.json',labels)
    atomic_json(source/'result.json',dict(observation='test',aggregates=rows,views={},warnings=[]))
    calls=[]
    original=module.measure_terminal_velocity
    def measure(*args,**kwargs):
        calls.append(args[0]['id'])
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'measure_terminal_velocity',measure)
    settings=dict(baseline='none',baseline_vmin=-60.,baseline_vmax=100.,contours=None,contour_smoothing=0)
    request=dict(source_results=str(source),settings=settings)
    if method is not None:
        request['terminal_velocity']=dict(aggregate_id='sky0',method=method)
    result=reanalyse(request,out,lambda *args:None)
    assert calls == (['sky0'] if method else [])
    first,other=result['aggregates']
    assert 'terminal_velocity' not in other and 'velocity_terminal' not in other['views']
    assert 'velocity_terminal' not in result['views']
    if method:
        assert first['terminal_velocity']['method'] == method
        assert (out/first['views']['velocity_terminal']).read_bytes().startswith(b'\x89PNG')
        assert json.loads((out/'sky0-terminal-velocity.json').read_text()) == first['terminal_velocity']
    else:
        assert 'terminal_velocity' not in first
