"""Profile dish editing uses files only, with validated and atomic updates."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.dsh import DishList, DishModel
from web.dish_store import DishProfileStore


@pytest.fixture
def dish_api(monkeypatch, tmp_path):
    monkeypatch.setenv('WEBHOOK_SECRET', 'dish-test-secret')
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / 'profiles' / 'test'
    profile.mkdir(parents=True)
    initial = DishList(list_id='test', dish_list=[DishModel(dsh_id='dish001')]).to_dict()
    # Use a real legacy profile as one entry, to exercise preservation on update.
    source = Path(__file__).resolve().parents[2] / 'config/local/DishList.json'
    initial['dish_list'][0] = json.loads(source.read_text())['dish_list'][0]
    initial['custom_metadata'] = {'keep': True}
    path = profile / 'DishList.json'
    path.write_text(json.dumps(initial))
    spec = importlib.util.spec_from_file_location('dish_web_server_under_test',
        Path(__file__).resolve().parents[2] / 'web/web_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    module.limiter.enabled = False
    module.app.extensions['web_application'] = SimpleNamespace(profile_dir=profile)
    return module.app.test_client(), path, {'X-Webhook-Token': 'dish-test-secret'}


def test_dish_list_and_single_read_need_no_tm(dish_api):
    client, path, headers = dish_api
    before = path.read_bytes()
    response = client.get('/api/dishes', headers=headers)
    assert response.status_code == 200
    assert response.json['payload']['profile'] == 'test'
    assert response.json['payload']['count'] == 1
    dish = response.json['payload']['dishes'][0]
    assert dish['dsh_id'] == 'dish001' and dish['driver_type'] == 'MD01'
    assert 'mode' not in dish
    single = client.get('/api/dishes/dish001', headers=headers)
    assert single.json['payload']['dish'] == dish
    assert single.headers['ETag'] == response.headers['ETag']
    assert path.read_bytes() == before


@pytest.mark.parametrize('method, url, body', [
    ('GET', '/api/dishes', None), ('GET', '/api/dishes/dish001', None),
    ('POST', '/api/dishes', {'dsh_id': 'new', 'driver_type': 'DRIFT'}),
    ('PATCH', '/api/dishes/dish001', {'diameter': 2}),
])
def test_dish_calls_require_authentication(dish_api, method, url, body):
    client, path, _ = dish_api
    before = path.read_bytes()
    assert client.open(url, method=method, json=body).status_code == 401
    assert path.read_bytes() == before


def test_create_and_reload_dish(dish_api):
    client, path, headers = dish_api
    response = client.post('/api/dishes', headers=headers, json={
        'dsh_id': 'dish004', 'short_desc': 'New fixed dish', 'diameter': 1.2,
        'driver_type': 'DRIFT', 'feed_type': 'H3T_1420', 'driver_config': {'alt': 45, 'az': 180}})
    assert response.status_code == 201
    assert response.headers['Location'] == '/api/dishes/dish004'
    dish = response.json['payload']['dish']
    assert dish['driver_config'] == {'alt': 45.0, 'az': 180.0}
    store = DishList.load_from_disk(input_dir=str(path.parent), filename=path.name)
    assert [item.dsh_id for item in store.dish_list] == ['dish001', 'dish004']
    assert client.get('/api/dishes/dish004', headers=headers).json['payload']['dish'] == dish
    duplicate = client.post('/api/dishes', headers=headers, json={'dsh_id': 'dish004', 'driver_type': 'DRIFT'})
    assert duplicate.status_code == 409


def test_edit_preserves_unrelated_and_legacy_fields(dish_api):
    client, path, headers = dish_api
    before = json.loads(path.read_text())
    response = client.patch('/api/dishes/dish001', headers=headers,
        json={'short_desc': 'Updated name', 'driver_config': {'port': 65001}})
    assert response.status_code == 200
    after = json.loads(path.read_text())
    expected = before['dish_list'][0]
    actual = after['dish_list'][0]
    assert actual['short_desc'] == 'Updated name'
    assert actual['driver_config']['port'] == 65001
    assert actual['driver_config']['host'] == expected['driver_config']['host']
    assert actual['driver_config']['close_enough'] == expected['driver_config']['close_enough']
    assert after['custom_metadata'] == before['custom_metadata']
    assert actual['mode'] == expected['mode']


def test_etag_rejects_stale_editor(dish_api):
    client, path, headers = dish_api
    revision = client.get('/api/dishes', headers=headers).headers['ETag']
    editor_headers = dict(headers, **{'If-Match': revision})
    assert client.patch('/api/dishes/dish001', headers=editor_headers, json={'diameter': 2}).status_code == 200
    before = path.read_bytes()
    stale = client.patch('/api/dishes/dish001', headers=editor_headers, json={'diameter': 3})
    assert stale.status_code == 412 and stale.json['code'] == 'version_conflict'
    assert path.read_bytes() == before


@pytest.mark.parametrize('patch', [
    {'diameter': -1}, {'diameter': True}, {'latitude': 100}, {'driver_type': 'BAD'},
    {'mode': 'OPERATE'}, {'dsh_id': 'other'}, {'driver_poll_period': 0},
    {'driver_config': {'port': 'invalid'}}, {'driver_config': {'port': True}}, {'driver_config': {'typo': 1}},
    {'driver_config': None}, {'typo': True}, {}, [], {'diameter': float('nan')},
])
def test_invalid_edits_leave_file_unchanged(dish_api, patch):
    client, path, headers = dish_api
    before = path.read_bytes()
    response = client.patch('/api/dishes/dish001', headers=headers, json=patch)
    assert response.status_code == 400
    assert response.json['status'] == 'error'
    assert path.read_bytes() == before


def test_driver_change_replaces_incompatible_config(dish_api):
    client, _, headers = dish_api
    response = client.patch('/api/dishes/dish001', headers=headers,
                            json={'driver_type': 'DRIFT', 'driver_config': {'alt': 30}})
    assert response.status_code == 200
    assert response.json['payload']['dish']['driver_config'] == {'alt': 30.0, 'az': 0.0}


def test_unknown_dish_and_missing_profile_file(dish_api):
    client, path, headers = dish_api
    assert client.get('/api/dishes/unknown', headers=headers).status_code == 404
    assert client.patch('/api/dishes/unknown', headers=headers, json={'diameter': 2}).status_code == 404
    path.unlink()
    response = client.get('/api/dishes', headers=headers)
    assert response.status_code == 404 and response.json['code'] == 'config_not_found'


def test_atomic_write_failure_preserves_original(dish_api, monkeypatch):
    import web.dish_store as module
    client, path, headers = dish_api
    before = path.read_bytes()
    def fail(*args):
        raise OSError('simulated replace failure')
    monkeypatch.setattr(module.os, 'replace', fail)
    assert client.patch('/api/dishes/dish001', headers=headers, json={'diameter': 2}).status_code == 503
    assert path.read_bytes() == before
    assert not list(path.parent.glob('.DishList-*'))


def test_concurrent_creates_do_not_overwrite_each_other(dish_api):
    _, path, _ = dish_api
    def create(index):
        return DishProfileStore(path.parent).save({'dsh_id': f'dish{index}', 'driver_type': 'DRIFT'})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(create, range(10, 18)))
    dishes, _ = DishProfileStore(path.parent).read()
    assert len(dishes) == 9


def add_profile(path, name, store):
    from models.launch import LaunchConfigModel
    directory = path.parent.parent / name
    directory.mkdir()
    (directory / 'LaunchConfig.json').write_text(json.dumps(
        LaunchConfigModel(profile=name, scan_store_dir=store).to_dict()))
    data = json.loads(path.read_text())
    data['dish_list'][0]['short_desc'] = name
    (directory / 'DishList.json').write_text(json.dumps(data))
    return directory


def test_profile_list_switch_updates_dishes_and_observation_default(dish_api, tmp_path):
    client, path, headers = dish_api
    archive = tmp_path / 'new archive'
    archive.mkdir()
    (archive / 'new-obs.json').write_text('{"_type":"ObsModel","obs_id":"new"}')
    selected = add_profile(path, 'second', str(archive))
    listing = client.get('/api/profiles', headers=headers)
    assert listing.status_code == 200
    assert listing.json['payload']['profile'] == 'test'
    assert {item['profile'] for item in listing.json['payload']['profiles']} == {'test', 'second'}
    assert client.put('/api/profile', headers=headers, json={'profile': 'second'}).status_code == 200
    active = client.get('/api/profile', headers=headers).json['payload']
    from util.log_config import resolve_log_dir
    assert active == {'profile': 'second', 'scan_store_dir': str(archive), 'log_dir': str(resolve_log_dir())}
    dishes = client.get('/api/dishes', headers=headers).json['payload']
    assert dishes['profile'] == 'second'
    assert dishes['dishes'][0]['short_desc'] == 'second'
    assert client.get('/api/observations', headers=headers).json['payload']['observations'][0]['id'] == 'new'
    before = path.read_bytes()
    assert client.patch('/api/dishes/dish001', headers=headers, json={'diameter': 4}).status_code == 200
    assert path.read_bytes() == before
    assert json.loads((selected / 'DishList.json').read_text())['dish_list'][0]['diameter'] == 4
    # A request directory still overrides the selected profile.
    empty = tmp_path / 'empty'
    empty.mkdir()
    response = client.get('/api/observations', headers=headers, query_string={'directory': str(empty)})
    assert response.json['payload']['count'] == 0


@pytest.mark.parametrize('body, expected', [
    ({'profile': '../test'}, 400), ({'profile': '/tmp'}, 400),
    ({'profile': 'absent'}, 404), ({'profile': ''}, 400),
    ({'profile': None}, 400), ({'profile': 'test', 'extra': True}, 400), ([], 400),
])
def test_invalid_profile_switch_preserves_current_selection(dish_api, body, expected):
    client, _, headers = dish_api
    before = client.get('/api/profile', headers=headers).json
    assert client.put('/api/profile', headers=headers, json=body).status_code == expected
    assert client.get('/api/profile', headers=headers).json == before


@pytest.mark.parametrize('method, url', [('GET', '/api/profiles'), ('GET', '/api/profile'), ('PUT', '/api/profile')])
def test_profile_calls_require_authentication(dish_api, method, url):
    client, _, _ = dish_api
    response = client.open(url, method=method, json={'profile': 'test'} if method == 'PUT' else None)
    assert response.status_code == 401


def test_profile_without_store_clears_previous_default(dish_api, tmp_path):
    client, path, headers = dish_api
    add_profile(path, 'second', str(tmp_path))
    assert client.put('/api/profile', headers=headers, json={'profile': 'second'}).status_code == 200
    assert client.get('/api/observations', headers=headers).status_code == 200
    assert client.put('/api/profile', headers=headers, json={'profile': 'test'}).status_code == 200
    assert client.get('/api/profile', headers=headers).json['payload']['scan_store_dir'] is None
    assert client.get('/api/observations', headers=headers).json['code'] == 'store_not_known'


def test_invalid_launch_config_is_reported_without_switching(dish_api):
    client, path, headers = dish_api
    invalid = add_profile(path, 'invalid', '~/samples')
    (invalid / 'LaunchConfig.json').write_text('{broken')
    profiles = client.get('/api/profiles', headers=headers).json['payload']['profiles']
    assert next(item for item in profiles if item['profile'] == 'invalid')['available'] is False
    response = client.put('/api/profile', headers=headers, json={'profile': 'invalid'})
    assert response.status_code == 400 and response.json['code'] == 'invalid_configuration'
    assert client.get('/api/profile', headers=headers).json['payload']['profile'] == 'test'


def test_profile_symlink_cannot_escape_configuration_root(dish_api, tmp_path):
    client, path, headers = dish_api
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'DishList.json').write_text(path.read_text())
    (path.parent.parent / 'linked').symlink_to(outside, target_is_directory=True)
    assert client.put('/api/profile', headers=headers, json={'profile': 'linked'}).status_code == 400
    names = [item['profile'] for item in client.get('/api/profiles', headers=headers).json['payload']['profiles']]
    assert 'linked' not in names


def test_managed_web_selection_resets_startup_override_and_survives_resync(dish_api):
    from web.profiles import ProfileManager
    from web.web import WebServer
    from unittest.mock import Mock
    client, path, headers = dish_api
    second = add_profile(path, 'second', '~/samples/second')
    web = WebServer.__new__(WebServer)
    web.stop = lambda: None
    web.profile_manager = ProfileManager(path.parent, scan_store_dir='/startup/override')
    web._load_configuration = Mock()
    client.application.extensions['web_application'] = web
    assert client.get('/api/profile', headers=headers).json['payload']['scan_store_dir'] == '/startup/override'
    client.put('/api/profile', headers=headers, json={'profile': 'second'})
    assert web.profile_manager.current.directory == second
    web.process_resync()
    assert web.profile_manager.current.directory == second
    assert web.profile_manager.current.scan_store_dir == '~/samples/second'


def test_delete_dish_preserves_other_definitions_and_checks_version_profile_auth(dish_api):
    client, path, headers = dish_api
    stale = client.get('/api/dishes', headers=headers).headers['ETag']
    added = client.post('/api/dishes', headers=headers, json={'dsh_id': 'other', 'driver_type': 'DRIFT'})
    current = added.headers['ETag']
    before = path.read_bytes()
    assert client.delete('/api/dishes/dish001').status_code == 401
    assert client.delete('/api/dishes/dish001', headers=dict(headers, **{'If-Match': stale})).status_code == 412
    assert client.delete('/api/dishes/dish001', headers=dict(headers, **{'X-Config-Profile': 'other'})).status_code == 409
    assert path.read_bytes() == before
    response = client.delete('/api/dishes/dish001', headers=dict(headers, **{'If-Match': current}))
    assert response.status_code == 200 and response.json['payload']['deleted_id'] == 'dish001'
    data = json.loads(path.read_text())
    assert [dish['dsh_id'] for dish in data['dish_list']] == ['other']
    assert data['custom_metadata'] == {'keep': True}
    assert client.delete('/api/dishes/dish001', headers=headers).status_code == 404
    assert client.delete('/api/dishes/other', headers=headers).status_code == 200
    assert client.get('/api/dishes', headers=headers).json['payload']['count'] == 0
