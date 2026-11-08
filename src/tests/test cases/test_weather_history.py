from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dsh.dm import DM
from models.dsh import DishManagerModel
from models.ws import WeatherData, WeatherStationList
from models.telescope import TelescopeModel
from ipc.message import APIMessage
from web.model_snapshot import telescope_snapshot


def test_display_history_is_bounded_per_station_without_changing_summaries():
    now = datetime.now(timezone.utc)
    samples = [WeatherData(ws_id='ws001', obs_time=now - timedelta(seconds=index / 2),
                           wind_speed=float(index % 15)) for index in range(400)]
    store = WeatherStationList(weather_data=samples, retention_period=300, summary_window=60)
    summary = store.get_weather_summary('ws001', now=now)
    assert summary.sample_secs == 60
    assert summary.sample_count == 121  # Half-second readings over an inclusive 60-second window.
    history = store.get_display_history(now=now)
    assert len(history) == 300
    assert history[0]['obs_time'] == samples[-1].to_dict()['obs_time']
    assert history[-1]['obs_time'] == samples[0].to_dict()['obs_time']
    assert all('_type' not in sample and 'last_update' not in sample for sample in history)
    assert len(store.weather_data) == 400
    assert store.get_weather_summary('ws001', now=now).to_dict()['wind_avg'] == summary.wind_avg


def test_display_history_filters_expired_and_future_samples_and_preserves_stations():
    now = datetime.now(timezone.utc)
    store = WeatherStationList(weather_data=[
        WeatherData(ws_id='ws002', obs_time=now, wind_speed=0.0),
        WeatherData(ws_id='ws001', obs_time=now - timedelta(seconds=1), wind_speed=3.0),
        WeatherData(ws_id='ws001', obs_time=now - timedelta(seconds=301), wind_speed=99.0),
        WeatherData(ws_id='ws001', obs_time=now + timedelta(seconds=10), wind_speed=99.0),
    ])
    history = store.get_display_history(now=now)
    assert [sample['ws_id'] for sample in history] == ['ws001', 'ws002']
    assert [sample['wind_speed'] for sample in history] == [3.0, 0.0]


def test_dm_status_forwards_display_history_through_telescope_models():
    now = datetime.now(timezone.utc)
    store = WeatherStationList(weather_data=[WeatherData(ws_id='ws001', obs_time=now, wind_speed=4.0)])
    store.weather_summaries = store.get_weather_summaries(now=now)
    manager = DM.__new__(DM)
    manager.stop = lambda: None
    manager.dm_model = DishManagerModel(weather_store=store)
    manager.tm_api = SimpleNamespace(get_api_version=lambda: '1.0')
    wire_message = manager._construct_status_adv_to_tm()
    decoded = APIMessage()
    decoded.from_data(wire_message.to_data())
    status = decoded.get_api_call()['value']
    assert status['weather_store']['weather_data'] == []
    assert len(store.weather_data) == 1
    received = DishManagerModel.from_dict(status).weather_store
    assert received.weather_history[0]['wind_speed'] == 4.0
    assert received.weather_summaries[0].sample_count == 1
    assert received.weather_metrics['ws001']['sample_count'] == 1
    assert received.weather_metrics['ws001']['avg_wind'] == 4.0
    telescope = TelescopeModel()
    telescope.dsh_mgr = DishManagerModel.from_dict(status)
    snapshot = telescope_snapshot(telescope)
    forwarded = snapshot['telescope_model']['dsh_mgr']['weather_store']['weather_history']
    assert forwarded[0]['ws_id'] == 'ws001'
    assert forwarded[0]['wind_speed'] == 4.0
    assert forwarded[0]['obs_time']['_type'] == 'datetime'
    old = store.to_dict()
    del old['weather_history']
    assert WeatherStationList.from_dict(old).weather_history == []


def test_display_metrics_use_full_alarm_window_and_report_expired_sample_age():
    now = datetime.now(timezone.utc)
    samples = [WeatherData(ws_id='ws001', obs_time=now - timedelta(seconds=index), wind_speed=speed)
               for index, speed in enumerate([11.0, 12.0, 0.0, 13.0, 14.0])]
    store = WeatherStationList(weather_data=samples, threshold_timeout=30,
        threshold_wind_avg=20.0, threshold_wind_gust=10.0, threshold_wind_count=3)
    metrics = store.get_display_metrics(now=now)['ws001']
    assert metrics['window_secs'] == 30
    assert metrics['sample_count'] == 5
    assert metrics['gust_count'] == 4
    assert metrics['avg_wind'] == 10.0
    assert metrics['wind_avg_triggered'] is False
    assert metrics['gust_count_triggered'] is True
    assert metrics['alarm_triggered'] is True
    assert 'latest_sample' not in metrics
    expired = store.get_display_metrics(now=now + timedelta(seconds=60))['ws001']
    assert expired['sample_count'] == 0
    assert expired['timeout_triggered'] is True
    assert expired['latest_age_sec'] == 60.0
    assert expired['last_sample_time'] == samples[0].to_dict()['obs_time']
