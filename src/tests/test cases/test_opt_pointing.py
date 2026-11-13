import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from models.obs import ObsModel
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, PointingType, OffsetScan, FivePointScan
from ui.opt_pointing import PointingCache, load_frames, render_pointing_product, pointing_details
from ui.opt_pointing import suggested_animation_interval


@pytest.mark.parametrize('count,interval', [(0,100),(1,500),(10,500),(40,250),(100,100),(200,100)])
def test_suggested_animation_interval_is_bounded(count, interval):
    assert suggested_animation_interval(count) == interval


def pointing_cache(folder, *, references=True):
    cache = PointingCache()
    observation = ObsModel(obs_id='pointing-test',latitude=53.,longitude=-2.,height=80.,
        targets=[TargetModel(tgt_idx=1,id='Sun',pointing=PointingType.OFFSET_SCAN)])
    for index in range(2):
        start = datetime(2026,11,8,12,index,tzinfo=timezone.utc)
        model = ScanModel(obs_id='pointing-test',tgt_idx=1,freq_scan=0,scan_iter=index,dig_id='dig001',
            scan_type=ScanType.SKY,status=ScanState.COMPLETE,read_start=start,read_end=start+timedelta(seconds=60),
            sample_rate=1000.,spectral_resolution=16,center_freq=1420e6,
            pointing_refs=[dict(datetime=start+timedelta(milliseconds=100),pointing_altaz={'alt':30.,'az':180.+index})] if references else [])
        power=np.arange(16,dtype=float)+index
        cache.capture(SimpleNamespace(scan_model=model,mpr=power,mpr_flags=np.zeros(16,dtype=np.uint16)))
        power[:]=999  # The cache owns its data, not the scan's soon-to-be-released arrays.
    cache.save(folder,observation)
    return observation


def test_cache_keeps_physical_spectra_and_first_reference_without_scan_replay(tmp_path,monkeypatch):
    import obs.opt
    monkeypatch.setattr(obs.opt,'process_observation',lambda *a,**k: pytest.fail('No pipeline replay'))
    pointing_cache(tmp_path)
    frames, location, errors=load_frames(tmp_path)
    assert len(frames)==2 and not errors
    assert frames[0].time==datetime(2026,11,8,12,0,0,100000,tzinfo=timezone.utc)
    assert frames[0].pointing_source=='first_pointing_reference'
    np.testing.assert_array_equal(frames[0].spectrum,np.arange(16))
    assert frames[0].ra_deg != frames[1].ra_deg
    assert location.lat.deg==pytest.approx(53.)
    data=json.loads((tmp_path/'native-pointing.json').read_text())
    assert 'sample_times' not in data['scans'][0]
    assert 'spr' not in data['scans'][0]
    assert len(load_frames(tmp_path,dict(target_id=1,freq_scan=0,digitiser='dig001',kind='SKY'))[0])==2
    with pytest.raises(ValueError,match='No matching'):
        load_frames(tmp_path,dict(target_id=2,freq_scan=0,digitiser='dig001',kind='SKY'))


def test_position_details_use_first_acquisition_and_exact_outer_references(tmp_path):
    pointing_cache(tmp_path)
    metadata = json.loads((tmp_path/'native-pointing.json').read_text())
    scans = [ScanModel.from_dict(item) for item in metadata['scans']]
    scans[0].tgt_acq_dt = scans[0].read_start - timedelta(seconds=10)
    scans[0].tgt_acq_altaz = {'alt':28., 'az':179.}
    scans[0].pointing_refs[0].update(pointing_offset=-1.5, pointing_position_angle=90.)
    scans[-1].pointing_refs.append(dict(datetime=scans[-1].read_end,
        pointing_altaz={'alt':32.,'az':182.},pointing_offset=1.5,pointing_position_angle=270.))
    target = TargetModel(id='Sun',pointing=PointingType.OFFSET_SCAN,scan=OffsetScan(offset=-1.5,rate=.05,angle=90.))
    values = {row['label']:row['value'] for section in pointing_details(target, list(reversed(scans))) for row in section['rows']}
    assert values['Target Acquisition Alt / Az'] == 'Alt 28.000°, Az 179.000°'
    assert values['Target Acquisition Date time'] == '2026-11-08T11:59:50.000 UTC'
    assert values['Scan start Pointing Alt / Az'] == 'Alt 30.000°, Az 180.000°'
    assert values['Scan start Offset'] == '-1.500°'
    assert values['Scan end Pointing Alt / Az'] == 'Alt 32.000°, Az 182.000°'
    assert values['Scan end Position Angle'] == '270.000°'
    assert values['Scan end Date time'] == '2026-11-08T12:02:00.000 UTC'
    assert 'Target RA / Dec' not in values and 'Target l / b' not in values


@pytest.mark.parametrize('native_frame', ['icrs','galactic'])
def test_sidereal_details_show_both_coordinate_systems_and_no_offsets(native_frame):
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    centre = SkyCoord(ra=120.*u.deg,dec=30.*u.deg).transform_to(native_frame)
    target = TargetModel(id='Star',pointing=PointingType.SIDEREAL_TRACK,sky_coord=centre)
    values = {row['label']:row['value'] for section in pointing_details(target, []) for row in section['rows']}
    assert values['Target RA / Dec'] == 'RA 120.000°, Dec 30.000° (ICRS)'
    assert values['Target l / b'].startswith('l ')
    assert not any('Offset' in label or 'Position Angle' in label or 'Rate' in label for label in values)
    assert 'Target Alt / Az' not in values
    assert values['Scan start Date time'] == 'Unavailable'
    assert values['Target Acquisition Alt / Az'] == 'Unavailable'


def test_five_point_details_show_direction_without_offset_scan_rate():
    target = TargetModel(id='Sun',pointing=PointingType.FIVE_POINT_SCAN,scan=FivePointScan(offset=2.,direction='N'))
    values = {row['label']:row['value'] for section in pointing_details(target, []) for row in section['rows']}
    assert values['Target Offset'] == '2.000°' and values['Target Direction'] == 'N'
    assert 'Target Rate' not in values and 'Target Angle' not in values
    assert values['Scan start Offset'] == 'Unavailable'


def test_altaz_target_details_omit_equatorial_galactic_and_offset_fields():
    target = TargetModel(id='Zenith',pointing=PointingType.DRIFT_SCAN,altaz={'alt':90.,'az':0.})
    values = {row['label']:row['value'] for section in pointing_details(target, []) for row in section['rows']}
    assert values['Target Alt / Az'] == 'Alt 90.000°, Az 0.000°'
    assert 'Target RA / Dec' not in values and 'Target l / b' not in values
    assert not any('Offset' in label or 'Position Angle' in label for label in values)


def test_pointing_cache_preserves_acquisition_metadata(tmp_path):
    cache = PointingCache()
    scan = ScanModel(spectral_resolution=2,status=ScanState.COMPLETE,
        tgt_acq_dt=datetime(2026,11,8,12,tzinfo=timezone.utc),tgt_acq_altaz={'alt':30.,'az':180.})
    cache.capture(SimpleNamespace(scan_model=scan,mpr=np.ones(2)))
    restored = ScanModel.from_dict(cache.scans[0])
    assert restored.tgt_acq_dt == scan.tgt_acq_dt
    assert restored.tgt_acq_altaz == scan.tgt_acq_altaz


def test_offset_animation_needs_measured_reference(tmp_path):
    pointing_cache(tmp_path,references=False)
    with pytest.raises(ValueError,match='first recorded pointing reference'):
        load_frames(tmp_path)


def test_pointing_job_renders_cached_products_and_preserves_existing_results(tmp_path,monkeypatch):
    import ui.opt_pointing as module
    import obs.animation as animation
    source,out=tmp_path/'source',tmp_path/'out'
    source.mkdir();out.mkdir()
    pointing_cache(source)
    cache_data=json.loads((source/'native-pointing.json').read_text())
    cache_data['scans'][1]['tgt_idx']=2
    cache_data['observation']['targets'].append(TargetModel(tgt_idx=2,id='Sun',pointing=PointingType.OFFSET_SCAN).to_dict())
    (source/'native-pointing.json').write_text(json.dumps(cache_data))
    row=dict(id='aggregate-0001',target_id=1,freq_scan=0,digitiser='dig001',kind='SKY',views={'signal':'signal.png'})
    result=dict(observation='pointing-test',aggregates=[row,dict(id='aggregate-0002',target_id=2,freq_scan=0,digitiser='dig001',kind='SKY',views={})],views={},warnings=[],pointing_cache=True)
    (source/'result.json').write_text(json.dumps(result))
    (source/'signal.png').write_bytes(b'original')
    captured=[]
    def chart(frames,location,path):
        captured.append([frame.time for frame in frames]);path.write_bytes(b'PNG')
    def gif(frames,location,path,**kwargs):
        kwargs['progress_callback'](1,len(frames));path.write_bytes(b'GIF89a')
    monkeypatch.setattr(module,'save_pointing_chart',chart)
    monkeypatch.setattr(animation,'create_animation',gif)
    updated=render_pointing_product(dict(source_results=str(source),pointing_product={'aggregate_id':'aggregate-0001','animation':True,'interval_ms':100}),out,lambda *a:None)
    assert updated['aggregates'][0]['views']['pointing']=='aggregate-0001-pointing.png'
    assert updated['aggregates'][0]['pointing_summary']['first_references']==1
    assert updated['aggregates'][1]['views']['pointing']=='aggregate-0002-pointing.png'
    assert updated['pointing_charts_complete'] is True
    assert updated['animation_frames']==2
    assert (out/updated['views']['animation']).read_bytes()==b'GIF89a'
    assert (out/'signal.png').read_bytes()==b'original'
    assert json.loads((source/'result.json').read_text())==result
    assert len(captured)==2 and len(captured[0])==1
    assert (out/'native-pointing.npz').read_bytes()==(source/'native-pointing.npz').read_bytes()


def test_static_pointings_do_not_load_mpr_spectra(tmp_path,monkeypatch):
    pointing_cache(tmp_path)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('Static sky view must not load spectra'))
    frames,location,errors=load_frames(tmp_path,include_spectra=False)
    assert len(frames)==2 and not errors
    assert all(frame.spectrum.size==0 for frame in frames)


def test_existing_animation_and_charts_are_reused_without_rendering(tmp_path,monkeypatch):
    import ui.opt_pointing as module
    import obs.animation as animation
    source,out=tmp_path/'source',tmp_path/'out'
    source.mkdir();out.mkdir()
    (source/'sky-pointing.png').write_bytes(b'PNG')
    (source/'pointing-animation.gif').write_bytes(b'GIF89a')
    result=dict(aggregates=[dict(id='sky',views={'pointing':'sky-pointing.png'})],
                views={'animation':'pointing-animation.gif'},warnings=[],animation_frames=2)
    (source/'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(module,'load_frames',lambda *a,**k:pytest.fail('Cached products must not reload frames'))
    monkeypatch.setattr(animation,'create_animation',lambda *a,**k:pytest.fail('Cached GIF must not be regenerated'))
    updated=render_pointing_product(dict(source_results=str(source),pointing_product={'aggregate_id':'sky','animation':True,'interval_ms':100}),out,lambda *a:None)
    assert updated['pointing_charts_complete'] is True
    assert (out/'pointing-animation.gif').read_bytes()==b'GIF89a'
    changed = tmp_path/'changed'; changed.mkdir()
    intervals = []
    def retime(path, interval_ms):
        intervals.append(interval_ms)
        path.write_bytes(b'GIF-new-interval')
        return 2
    monkeypatch.setattr(module,'set_animation_interval',retime)
    updated=render_pointing_product(dict(source_results=str(source),pointing_product={
        'aggregate_id':'sky','animation':True,'interval_ms':300}),changed,lambda *a:None)
    assert intervals == [300]
    assert updated['animation_interval_ms'] == 300
    assert (source/'pointing-animation.gif').read_bytes() == b'GIF89a'


def test_animation_interval_update_preserves_frames(tmp_path):
    from PIL import Image, ImageSequence
    from ui.opt_pointing import set_animation_interval
    path = tmp_path/'test.gif'
    frames = [Image.new('RGB',(8,8),colour) for colour in ['red','blue','green']]
    frames[0].save(path,save_all=True,append_images=frames[1:],duration=100,loop=0)
    original_bytes = path.read_bytes()
    with Image.open(path) as gif:
        before = [np.array(frame.convert('RGB')) for frame in ImageSequence.Iterator(gif)]
    assert set_animation_interval(path,300) == 3
    updated_bytes = path.read_bytes()
    assert len(updated_bytes) == len(original_bytes)
    allowed_changes = {index + offset for index in range(len(original_bytes))
                       if original_bytes[index:index+3] == b'\x21\xf9\x04' for offset in (4,5)}
    assert {index for index,(before_byte,after_byte) in enumerate(zip(original_bytes,updated_bytes))
            if before_byte != after_byte} <= allowed_changes
    with Image.open(path) as gif:
        for index, frame in enumerate(ImageSequence.Iterator(gif)):
            assert frame.info['duration'] == 300
            np.testing.assert_array_equal(np.array(frame.convert('RGB')),before[index])


@pytest.mark.parametrize('contents', [b'bad gif',b'GIF89a'+b'\0'*7,b'GIF89a'+b'\0'*7+b'\x3b'])
def test_invalid_animation_is_not_overwritten_when_retiming(tmp_path,contents):
    from ui.opt_pointing import set_animation_interval
    path=tmp_path/'broken.gif'
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        set_animation_interval(path,300)
    assert path.read_bytes() == contents


def test_animation_job_from_old_web_server_rejects_missing_interval(tmp_path):
    with pytest.raises(ValueError,match='Restart the web server'):
        render_pointing_product(dict(source_results=str(tmp_path),pointing_product={
            'aggregate_id':'sky','animation':True}),tmp_path,lambda *a:None)
