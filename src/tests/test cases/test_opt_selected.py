"""Individual Velocity options preserve the observation and other aggregate products."""
import json
from pathlib import Path

import numpy as np
import pytest

from ui.opt_selected import render_selected_product
from ui.opt_service import atomic_json, validate_settings


def source_results(folder):
    folder.mkdir()
    velocity=np.linspace(-100.,150.,501)
    power=1.+.01*np.sin(np.arange(501))+np.exp(-.5*((velocity-30.)/18.)**2)
    arrays={}; labels=[]; rows=[]
    for index in range(2):
        identifier=f'sky{index}'
        arrays.update({f'{index}_velocity':velocity, f'{index}_power':power,
                       f'{index}_channel_flags':np.zeros(501,dtype=np.uint16)})
        labels.append(dict(id=identifier,target_id=index,freq_scan=0,longitude=30.+index,latitude=0.,center_freq=1420e6))
        name=f'{identifier}-velocity.png'
        (folder/name).write_bytes(b'original individual plot')
        rows.append(dict(id=identifier,target_id=index,freq_scan=0,kind='SKY',digitiser='dig001',views={'velocity':name}))
    np.savez_compressed(folder/'native-spectra.npz',**arrays)
    atomic_json(folder/'native-spectra.json',labels)
    for name in ('velocity.png','frequency.png','timeline.png','timeline-seconds.png','lv.png'):
        (folder/name).write_bytes(name.encode())
    result=dict(observation='test',settings=validate_settings({}),aggregates=rows,
                views={key:key+'.png' for key in ('velocity','frequency','timeline','lv')},warnings=[])
    atomic_json(folder/'result.json',result)
    return result


def update(source,out,*,aggregate='sky0',method='threshold',beam=None):
    out.mkdir()
    messages=[]
    request=dict(source_results=str(source),aggregate_product=dict(aggregate_id=aggregate))
    if method:
        request['terminal_velocity']=dict(aggregate_id=aggregate,method=method)
    if beam:
        request['lab_comparison']=dict(aggregate_id=aggregate,beam=beam)
    result=render_selected_product(request,out,lambda text,fraction:messages.append(text))
    atomic_json(out/'result.json',result)
    return result,messages


def test_selected_update_never_regenerates_other_spectra_or_observation_plots(tmp_path,monkeypatch):
    import ui.opt_selected as selected
    source=tmp_path/'source'; original=source_results(source)
    original_record=selected.velocity_record
    loaded=[]
    def capture(arrays,index,label,*args):
        loaded.append(label['id'])
        assert index == 0
        return original_record(arrays,index,label,*args)
    monkeypatch.setattr(selected,'velocity_record',capture)
    result,messages=update(source,tmp_path/'updated')
    assert loaded == ['sky0']
    assert all('1/1' in message for message in messages)
    assert result['aggregates'][1] == original['aggregates'][1]
    assert result['views'] == original['views']
    for name in ('sky0-velocity.png','sky1-velocity.png','velocity.png','frequency.png','timeline.png','timeline-seconds.png','lv.png','native-spectra.npz'):
        assert (tmp_path/'updated'/name).read_bytes() == (source/name).read_bytes()
    assert len(result['aggregates'][0]['velocity_products']) == 1
    assert 'terminal_velocity' not in json.loads((source/'result.json').read_text())['aggregates'][0]


def test_products_survive_other_aggregate_updates_and_cache_hits_do_not_load_spectra(tmp_path,monkeypatch):
    source=tmp_path/'source';source_results(source)
    first,_=update(source,tmp_path/'first')
    second,_=update(tmp_path/'first',tmp_path/'second',aggregate='sky1',method='connected')
    assert second['aggregates'][0]['velocity_products'] == first['aggregates'][0]['velocity_products']
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('Cache hit must not load spectra'))
    cached,messages=update(tmp_path/'second',tmp_path/'cached')
    assert messages == ['Using cached options for selected aggregate']
    assert cached['aggregates'][0]['terminal_velocity'] == first['aggregates'][0]['terminal_velocity']


def test_lab_settings_are_cached_per_aggregate_and_variant(tmp_path,monkeypatch):
    import ui.opt_lab as lab
    source=tmp_path/'source';source_results(source)
    calls=[]
    def compare(records,selection,settings,out,result,progress=None):
        assert len(records) == 1
        row=next(row for row in result['aggregates'] if row['id']==records[0]['id'])
        calls.append((row['id'],selection['beam']))
        metadata=dict(aggregate_id=row['id'],beam_fwhm_deg=selection['beam'],sweep_ranges=selection.get('sweep'))
        result['lab_comparison']=metadata
        row['lab_comparison']=metadata
        row['views']['lab']=row['id']+'-lab.png'
        (out/row['views']['lab']).write_bytes(str(selection['beam']).encode())
    monkeypatch.setattr(lab,'render_lab_comparison',compare)
    first,_=update(source,tmp_path/'first',method=None,beam=5.)
    original_name=first['aggregates'][0]['views']['lab']
    second,_=update(tmp_path/'first',tmp_path/'second',method=None,beam=8.)
    assert len(second['aggregates'][0]['velocity_products']) == 2
    assert (tmp_path/'second'/original_name).read_bytes() == b'5.0'
    cached,messages=update(tmp_path/'second',tmp_path/'cached',method=None,beam=5.)
    assert calls == [('sky0',5.),('sky0',8.)]
    assert cached['aggregates'][0]['views']['lab'] == original_name
    assert messages == ['Using cached options for selected aggregate']


def test_worker_dispatches_selected_update_without_global_reanalysis(tmp_path,monkeypatch):
    import ui.opt_reanalysis as global_update
    import ui.opt_worker as worker
    source=tmp_path/'source'; result=source_results(source)
    job=tmp_path/'job';job.mkdir()
    atomic_json(job/'request.json',dict(source_results=str(source),settings=result['settings'],
        aggregate_product={'aggregate_id':'sky0'},terminal_velocity={'aggregate_id':'sky0','method':'threshold'}))
    monkeypatch.setattr(global_update,'reanalyse',lambda *a,**k:pytest.fail('No observation-wide plot refresh'))
    monkeypatch.setattr(worker.plt,'show',lambda *a,**k:None)
    worker.run(job)
    updated=json.loads((job/'out/result.json').read_text())
    assert updated['aggregates'][0]['terminal_velocity']['method'] == 'threshold'
    assert updated['aggregates'][1] == result['aggregates'][1]
    assert (job/'out/results.zip').is_file()
