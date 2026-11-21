"""Digitiser and weather-station CRUD preserves selected profile configurations."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdk.models.dig import DigitiserList, DigitiserModel
from sdk.models.ws import WeatherStation, WeatherStationList
from web.resource_store import DigitiserProfileStore, WeatherStationProfileStore


@pytest.fixture(params=['digitisers', 'weather-stations'])
def api(request, monkeypatch, tmp_path):
    monkeypatch.setenv('WEBHOOK_SECRET', 'resource-secret')
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / 'profiles' / 'first'
    profile.mkdir(parents=True)
    is_dig = request.param == 'digitisers'
    data = (DigitiserList(dig_list=[DigitiserModel(dig_id='dig001', sdr_config={'keep': True})])
            if is_dig else WeatherStationList(weather_stations=[WeatherStation(ws_id='ws001', location='Original')])).to_dict()
    data['custom_metadata'] = {'keep': True}
    data['dig_list' if is_dig else 'weather_stations'][0]['legacy_field'] = 'preserve'
    path = profile / ('DigitiserList.json' if is_dig else 'WeatherStationList.json')
    path.write_text(json.dumps(data))
    spec = importlib.util.spec_from_file_location('resource_web_under_test',
        Path(__file__).resolve().parents[2] / 'web/web_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    module.limiter.enabled = False
    module.app.extensions['web_application'] = SimpleNamespace(profile_dir=profile)
    return SimpleNamespace(client=module.app.test_client(), path=path,
        url='/api/' + request.param, dig=is_dig, headers={'X-Webhook-Token': 'resource-secret'},
        identifier='dig001' if is_dig else 'ws001', id_field='dig_id' if is_dig else 'ws_id',
        single='digitiser' if is_dig else 'weather_station',
        plural='digitisers' if is_dig else 'weather_stations',
        list_field='dig_list' if is_dig else 'weather_stations',
        store_class=DigitiserProfileStore if is_dig else WeatherStationProfileStore)


def creation(api, identifier):
    return ({'dig_id': identifier, 'sdr_type': 'rtlstream', 'local_host': '127.0.0.2'} if api.dig
            else {'ws_id': identifier, 'location': 'New station', 'latitude': 53, 'longitude': -2, 'elevation': 90})


def test_list_single_create_edit_and_model_reload(api):
    listed = api.client.get(api.url, headers=api.headers)
    assert listed.status_code == 200 and listed.json['payload']['count'] == 1
    assert listed.json['payload']['profile'] == 'first'
    assert 'app' not in listed.json['payload'][api.plural][0]
    assert 'last_update' not in listed.json['payload'][api.plural][0]
    assert api.client.get(api.url + '/' + api.identifier, headers=api.headers).status_code == 200
    created = api.client.post(api.url, headers=api.headers, json=creation(api, 'new001'))
    assert created.status_code == 201 and created.json['payload']['phase'] == 'stored'
    assert created.headers['Location'] == api.url + '/new001'
    patch = {'gain': 20, 'sdr_config': {'ring_seconds': 5}} if api.dig else {'location': 'Updated', 'latitude': 45}
    edited = api.client.patch(api.url + '/' + api.identifier, headers=api.headers, json=patch)
    assert edited.status_code == 200
    raw = json.loads(api.path.read_text())
    assert raw['custom_metadata'] == {'keep': True}
    assert raw[api.list_field][0]['legacy_field'] == 'preserve'
    if api.dig:
        assert edited.json['payload'][api.single]['gain'] == 20.0
        assert edited.json['payload'][api.single]['sdr_config'] == {'keep': True, 'ring_seconds': 5}
    else:
        assert raw['threshold_wind_avg'] == 20.0
        assert edited.json['payload'][api.single]['latitude'] == 45.0
    model = DigitiserList if api.dig else WeatherStationList
    assert len(getattr(model.load_from_disk(input_dir=str(api.path.parent), filename=api.path.name), api.list_field)) == 2


@pytest.mark.parametrize('method', ['GET', 'POST', 'PATCH'])
def test_authentication_before_file_access(api, method):
    before = api.path.read_bytes()
    url = api.url + '/' + api.identifier if method == 'PATCH' else api.url
    assert api.client.open(url, method=method, json=creation(api, 'new001')).status_code == 401
    assert api.path.read_bytes() == before


def test_duplicate_unknown_id_and_stale_version(api):
    assert api.client.post(api.url, headers=api.headers, json=creation(api, api.identifier)).status_code == 409
    assert api.client.get(api.url + '/missing', headers=api.headers).status_code == 404
    assert api.client.patch(api.url + '/missing', headers=api.headers, json=creation(api, 'missing')).status_code == 404
    revision = api.client.get(api.url, headers=api.headers).headers['ETag']
    patch = {'gain': 25} if api.dig else {'location': 'Updated'}
    headers = dict(api.headers, **{'If-Match': revision})
    assert api.client.patch(api.url + '/' + api.identifier, headers=headers, json=patch).status_code == 200
    before = api.path.read_bytes()
    stale = api.client.patch(api.url + '/' + api.identifier, headers=headers, json=patch)
    assert stale.status_code == 412 and stale.json['code'] == 'version_conflict'
    assert api.path.read_bytes() == before


def test_invalid_updates_preserve_file(api):
    patches = ([{'gain': -1}, {'gain': True}, {'sdr_type': 'bad'}, {'sdr_config': []},
                {'temp_type': 'bad'}, {'bpf_type': 'bad'}, {'temp_max': 101},
                {'app_cmd_port': 99999}, {'app_cmd_port': True}, {'freq_correction': True},
                {'scanning': True}, {'app': {}}, {'bpf_config': {'typo': 1}}] if api.dig
               else [{'latitude': 100}, {'longitude': 200}, {'elevation': -1},
                     {'latitude': True}, {'location': ''}, {'weather_data': []}])
    patches += [{api.id_field: 'different'}, {'typo': True}, {}, [], {'gain' if api.dig else 'latitude': float('nan')}]
    before = api.path.read_bytes()
    for patch in patches:
        response = api.client.patch(api.url + '/' + api.identifier, headers=api.headers, json=patch)
        assert response.status_code == 400, (patch, response.json)
        assert api.path.read_bytes() == before


def test_profile_switch_redirects_all_resource_operations(api):
    other = api.path.parent.parent / 'second'
    other.mkdir()
    (other / api.path.name).write_text(api.path.read_text())
    assert api.client.put('/api/profile', headers=api.headers, json={'profile': 'second'}).status_code == 200
    before = api.path.read_bytes()
    assert api.client.post(api.url, headers=api.headers, json=creation(api, 'new001')).status_code == 201
    assert api.client.get(api.url, headers=api.headers).json['payload']['profile'] == 'second'
    assert api.path.read_bytes() == before
    assert len(json.loads((other / api.path.name).read_text())[api.list_field]) == 2


def test_atomic_failure_keeps_original(api, monkeypatch):
    import web.profile_store as module
    before = api.path.read_bytes()
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(module.os, 'replace', fail)
    response = api.client.post(api.url, headers=api.headers, json=creation(api, 'new001'))
    assert response.status_code == 503
    assert api.path.read_bytes() == before
    assert not list(api.path.parent.glob('.' + api.path.stem + '-*'))


def test_parallel_creates_preserve_both_entries(api):
    def create(index):
        api.store_class(api.path.parent).save(creation(api, f'new{index}'))
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(create, range(6)))
    assert len(api.store_class(api.path.parent).read()[0]) == 7


def test_filter_and_driver_configuration(api):
    if not api.dig:
        return
    url = api.url + '/' + api.identifier
    response = api.client.patch(url, headers=api.headers, json={
        'bpf_type': 'SAWBIRD_H1_BAREBONES', 'bpf_config': {'gpio_pin_load': None, 'gpio_pin_power': 17},
        'temp_type': 'BME280', 'temp_config': {'bus_number': 1},
        'sdr_config': {'device': 'new'}, 'sdr_type': 'soapy',
        'app_cmd_host': '127.0.0.2', 'app_cmd_port': 60007})
    assert response.status_code == 200
    digitiser = response.json['payload']['digitiser']
    assert digitiser['bpf_config'] == {'gpio_pin_load': None, 'gpio_pin_power': 17}
    assert digitiser['sdr_config'] == {'device': 'new'}  # Old SDR settings are cleared on a type change.
    assert digitiser['temp_config'] == {'bus_number': 1}
    assert digitiser['app_cmd_port'] == 60007
    assert api.client.patch(url, headers=api.headers, json={'bpf_config': {'gpio_pin_load': 99}}).status_code == 400


def test_delete_definition_uses_profile_version_and_preserves_other_entries(api):
    stale = api.client.get(api.url, headers=api.headers).headers['ETag']
    added = api.client.post(api.url, headers=api.headers, json=creation(api, 'other'))
    before = api.path.read_bytes()
    original = json.loads(before)
    url = api.url + '/' + api.identifier
    assert api.client.delete(url).status_code == 401
    assert api.client.delete(url, headers=dict(api.headers, **{'If-Match': stale})).status_code == 412
    assert api.client.delete(url, headers=dict(api.headers, **{'X-Config-Profile': 'wrong'})).status_code == 409
    assert api.path.read_bytes() == before
    response = api.client.delete(url, headers=dict(api.headers, **{'If-Match': added.headers['ETag']}))
    assert response.status_code == 200 and response.json['payload']['deleted_id'] == api.identifier
    after = json.loads(api.path.read_text())
    assert after[api.list_field] == original[api.list_field][1:]
    assert after['custom_metadata'] == original['custom_metadata']
    if not api.dig:
        assert after['threshold_wind_avg'] == original['threshold_wind_avg']
    assert api.client.delete(url, headers=api.headers).status_code == 404
    assert api.client.delete(api.url + '/other', headers=api.headers).status_code == 200
    assert api.client.get(api.url, headers=api.headers).json['payload']['count'] == 0


def test_delete_failure_preserves_original_file(api, monkeypatch):
    import web.profile_store as module
    before = api.path.read_bytes()
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(module.os, 'replace', fail)
    assert api.client.delete(api.url + '/' + api.identifier, headers=api.headers).status_code == 503
    assert api.path.read_bytes() == before


@pytest.mark.parametrize('api', ['weather-stations'], indirect=True)
def test_weather_shared_policy_and_station_edits_share_one_revision_without_losing_data(api):
    raw = json.loads(api.path.read_text())
    raw['weather_metrics'] = {'ws001': {'sample_count':7}}
    raw['last_mth_alarm_count'] = 2
    api.path.write_text(json.dumps(raw))
    result = api.client.get(api.url, headers=api.headers)
    assert result.json['payload']['settings']['retention_period'] == 300
    assert result.json['payload']['settings']['threshold_wind_count'] == 3
    revision = result.headers['ETag']
    settings = dict(weather_enabled=False, threshold_timeout=45, retention_period=900, summary_window=120,
                    threshold_wind_avg=15, threshold_wind_gust=20, threshold_wind_count=4, threshold_precipitation=7)
    response = api.client.patch('/api/weather-settings', json=settings, headers={**api.headers, 'If-Match':revision})
    assert response.status_code == 200
    assert response.json['payload']['profile'] == 'first'
    after = json.loads(api.path.read_text())
    assert after['weather_stations'] == raw['weather_stations']
    assert after['weather_metrics'] == raw['weather_metrics']
    assert after['custom_metadata'] == raw['custom_metadata']
    assert after['last_mth_alarm_count'] == 2
    assert type(after['threshold_wind_avg']) is float
    assert type(after['threshold_wind_count']) is int
    assert api.client.patch(api.url+'/ws001', json={'location':'Stale'}, headers={**api.headers, 'If-Match':revision}).status_code == 412
    updated = api.client.patch(api.url+'/ws001', json={'location':'New location'}, headers={**api.headers, 'If-Match':response.headers['ETag']})
    assert updated.status_code == 200
    assert api.client.patch('/api/weather-settings', json={'retention_period':600}, headers={**api.headers, 'If-Match':response.headers['ETag']}).status_code == 412
    assert api.client.get('/api/weather-settings', headers=api.headers).json['payload']['settings'] == settings
    model = WeatherStationList.load_from_disk(input_dir=api.path.parent, filename=api.path.name)
    assert model.retention_period == 900 and model.weather_stations[0].location == 'New location'


@pytest.mark.parametrize('api', ['weather-stations'], indirect=True)
@pytest.mark.parametrize('patch', [{'weather_enabled':1}, {'threshold_wind_avg':True}, {'threshold_timeout':1.5},
    {'retention_period':-1}, {'summary_window':float('nan')}, {'threshold_wind_gust':float('inf')},
    {'threshold_wind_count':True}, {'threshold_precipitation':-1}, {'weather_data':[]}, {}])
def test_invalid_weather_policy_never_changes_file(api, patch):
    revision = api.client.get(api.url, headers=api.headers).headers['ETag']
    before = api.path.read_bytes()
    response = api.client.patch('/api/weather-settings', json=patch, headers={**api.headers, 'If-Match':revision})
    assert response.status_code == 400
    assert api.path.read_bytes() == before


@pytest.mark.parametrize('api', ['weather-stations'], indirect=True)
def test_weather_policy_auth_profile_guard_revision_and_atomic_failure(api, monkeypatch):
    before = api.path.read_bytes()
    assert api.client.get('/api/weather-settings').status_code == 401
    assert api.client.patch('/api/weather-settings', json={'weather_enabled':False}).status_code == 401
    assert api.client.patch('/api/weather-settings', json={'weather_enabled':False}, headers=api.headers).status_code == 428
    assert api.client.get('/api/weather-settings', headers={**api.headers, 'X-Config-Profile':'other'}).status_code == 409
    revision = api.client.get('/api/weather-settings', headers=api.headers).headers['ETag']
    def fail(*args):
        raise OSError('test replace failure')
    monkeypatch.setattr('web.profile_store.os.replace', fail)
    assert api.client.patch('/api/weather-settings', json={'weather_enabled':False}, headers={**api.headers, 'If-Match':revision}).status_code == 503
    assert api.path.read_bytes() == before


@pytest.mark.parametrize('api', ['weather-stations'], indirect=True)
def test_weather_driver_fields_and_simulation_persist_and_roundtrip(api):
    result = api.client.get(api.url, headers=api.headers)
    assert {'UNKNOWN','MODBUS','ADS1115'} == set(result.json['payload']['driver_types'])
    assert result.json['payload']['driver_types']['MODBUS']['driver_poll_period'] == 1000
    changes = {'driver_type':'MODBUS','driver_config':{'port':'/dev/sensor-test','driver_poll_period':250,'slave_address':3},'sim_mode':'windy'}
    result = api.client.patch(api.url+'/ws001', json=changes, headers={**api.headers,'If-Match':result.headers['ETag']})
    assert result.status_code == 200
    config = result.json['payload']['weather_station']
    assert config['driver_config']['port'] == '/dev/sensor-test'
    assert config['driver_config']['driver_poll_period'] == 250
    assert config['sim_mode'] == 'windy'
    model = WeatherStationList.load_from_disk(input_dir=api.path.parent, filename=api.path.name)
    assert model.weather_stations[0].driver_type.name == 'MODBUS'
    assert model.weather_stations[0].driver_config.driver_poll_period == 250
    assert 'driver_poll_period' not in model.weather_stations[0].to_dict()
    for patch in [{'driver_config':{'driver_poll_period':0}}, {'driver_config':{'unknown':1}},
                  {'driver_config':{'driver_poll_period':True}}, {'sim_mode':'bad'},
                  {'driver_type':'INVALID'}, {'driver_failures':2}]:
        before=api.path.read_bytes()
        response=api.client.patch(api.url+'/ws001', json=patch, headers={**api.headers,'If-Match':result.headers['ETag']})
        assert response.status_code == 400
        assert api.path.read_bytes() == before
    response=api.client.patch(api.url+'/ws001',json={'driver_type':'ADS1115','driver_config':{'adc_backend':'mock','adc_channel':1}},headers={**api.headers,'If-Match':result.headers['ETag']})
    assert response.status_code == 200
    config=response.json['payload']['weather_station']['driver_config']
    assert config['adc_channel']==1 and 'port' not in config
