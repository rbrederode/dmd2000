"""Exercise archive edits on synthetic files; never touch real observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from models.obs import ObsModel, ObsState
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, TargetConfig, TargetScanSet
from obs.editor import Editor, EditError, main
from util import gen_file_prefix


def write_json(path, data):
    path.write_text(json.dumps(data, indent=4))


def read(path):
    return json.loads(path.read_text())


def observation(root, name, groups, state=ObsState.ABORTED):
    targets, configs, scan_groups = [], [], []
    for target, (kind, counts) in enumerate(groups):
        targets.append(TargetModel(obs_id=name, tgt_idx=target, id='Test sky', altaz={'alt': 30., 'az': 180.}))
        configs.append(TargetConfig(obs_id=name, tgt_idx=target, gain=10., sample_rate=6000000.,
                                    spectral_resolution=4096, center_freq=1420000000., integration_time=60. * max(counts)))
        scans = []
        for frequency, count in enumerate(counts):
            for iteration in range(count):
                start = datetime(2026, 11, 13, 15, tzinfo=timezone.utc) + timedelta(minutes=target * 10 + iteration)
                model = ScanModel(obs_id=name, tgt_idx=target, freq_scan=frequency, scan_iter=iteration,
                    dig_id='dig001', gain=10., duration=60, sample_rate=6000000., spectral_resolution=4096,
                    center_freq=1420000000. + frequency * 1000000., scan_type=kind, load=kind == ScanType.LOAD,
                    status=ScanState.COMPLETE, read_start=start, read_end=start + timedelta(seconds=61),
                    created=start, sample_times=[{'read_start': start, 'read_end': start + timedelta(seconds=1), 'gap': .05}],
                    load_scan_id=f'{name}-0-0-0' if kind == ScanType.SKY else None, sdp_config_profile='rotation')
                prefix = gen_file_prefix(dt=start, entity_id=model.dig_id, gain=model.gain, duration=model.duration,
                    sample_rate=model.sample_rate, center_freq=model.center_freq, spectral_resolution=model.spectral_resolution,
                    instance_id=model.scan_id, scan_type=kind)
                model.files_prefix, model.files_directory = prefix, str(root)
                write_json(root / (prefix + '-meta.json'), model.to_dict())
                write_json(root / (prefix + '-qa.json'), {'_type': 'ScanQA', 'scan_id': model.scan_id, 'mpr_qa': None})
                for suffix in ('-spr.csv', '-sigfig.png', '-raw.iq', '-fb.dat'):
                    (root / (prefix + suffix)).write_bytes((model.scan_id + suffix + '\n').encode())
                scans.append(model)
        scan_groups.append(TargetScanSet(obs_id=name, tgt_idx=target, scans=scans,
                          freq_scans=len(counts), scan_iterations=max(counts), scan_duration=60))
    model = ObsModel(obs_id=name, obs_state=state, targets=targets, target_configs=configs, target_scans=scan_groups,
                     dsh_id='dish001', latitude=53., longitude=-2., height=88., tgt_idx=len(targets),
                     start_dt=datetime(2026, 11, 13, 15, tzinfo=timezone.utc),
                     end_dt=datetime(2026, 11, 13, 18, tzinfo=timezone.utc))
    path = root / (name + '-obs.json')
    write_json(path, model.to_dict())
    return path


def scans(path):
    return [scan for group in read(path)['target_scans'] for scan in group['scans']]


def product(root, scan, suffix):
    return root / (scan['files_prefix'] + suffix)


def digest(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_dry_run_is_read_only_and_reports_complete_plan(tmp_path, capsys):
    observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    observation(tmp_path, 'dst', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    before = digest(tmp_path)
    assert main(['--scan_store_dir', str(tmp_path), '--src_obs_id', 'src', '--src_scan_id', '1-0-0',
                 '--op', 'copy', '--dest_obs_id', 'dst', '--dest_scan_id', '1-0-1', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'src-1-0-0 -> dst-1-0-1' in output
    assert 'dst-1-0-1 -> dst-1-0-2' in output
    assert 'DRY RUN' in output and 'COPY BYTES' in output
    assert digest(tmp_path) == before
    assert not (tmp_path / '.obs-editor').exists()
    assert not (tmp_path / '.obs-editor.lock').exists()


def test_copy_preserves_payload_png_times_and_calibration_and_updates_ids(tmp_path):
    src = observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    dst = observation(tmp_path, 'dst', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    source_bytes = src.read_bytes()
    donor = scans(src)[1]
    original_meta = read(product(tmp_path, donor, '-meta.json'))
    original_payloads = {suffix: product(tmp_path, donor, suffix).read_bytes()
                         for suffix in ('-spr.csv', '-sigfig.png', '-raw.iq', '-fb.dat')}
    plan = Editor(tmp_path).build('src', (1, 0, 0), 'copy', 'dst', (1, 0, 1))
    recovery = plan.apply()
    assert read(recovery / 'manifest.json')['state'] == 'committed'
    assert src.read_bytes() == source_bytes
    inserted = scans(dst)[2]
    assert (inserted['obs_id'], inserted['tgt_idx'], inserted['freq_scan'], inserted['scan_iter']) == ('dst', 1, 0, 1)
    metadata = read(product(tmp_path, inserted, '-meta.json'))
    for key in ('read_start', 'read_end', 'sample_times', 'created', 'pointing_refs', 'load_scan_id', 'sdp_config_profile'):
        assert metadata[key] == original_meta[key]
    assert metadata['load_scan_id'] == 'src-0-0-0'
    assert read(product(tmp_path, inserted, '-qa.json'))['scan_id'] == 'dst-1-0-1'
    for suffix, content in original_payloads.items():
        assert product(tmp_path, inserted, suffix).read_bytes() == content
        assert product(tmp_path, inserted, suffix).stat().st_ino != product(tmp_path, donor, suffix).stat().st_ino
    ObsModel.from_dict(read(dst))
    assert [scan['scan_iter'] for scan in scans(dst)[1:]] == [0, 1, 2]


def test_delete_last_numbered_iteration_keeps_frequency_with_remaining_scans(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [3, 2])])
    Editor(tmp_path).build('src', (0, 0, 2), 'delete').apply()
    result = scans(path)
    assert [(scan['freq_scan'], scan['scan_iter']) for scan in result] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert read(path)['target_scans'][0]['freq_scans'] == 2


def test_delete_only_scan_in_frequency_closes_frequency_gap(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [1, 2])])
    payload = product(tmp_path, scans(path)[1], '-spr.csv').read_bytes()
    Editor(tmp_path).build('src', (0, 0, 0), 'delete').apply()
    result = scans(path)
    assert [(scan['freq_scan'], scan['scan_iter']) for scan in result] == [(0, 0), (0, 1)]
    assert product(tmp_path, result[0], '-spr.csv').read_bytes() == payload


def test_delete_last_scan_of_target_closes_target_gap_and_updates_definitions(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [1]), (ScanType.SKY, [2])])
    original = read(path)
    Editor(tmp_path).build('src', (1, 0, 0), 'delete').apply()
    result = read(path)
    for field in ('targets', 'target_configs', 'target_scans'):
        assert [row['tgt_idx'] for row in result[field]] == [0, 1]
    assert result['target_scans'][1]['scans'][0]['tgt_idx'] == 1
    for field in ('start_dt', 'end_dt', 'scheduling_block_start', 'scheduling_block_end', 'created', 'obs_state'):
        assert result[field] == original[field]


def test_target_and_frequency_insertion(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    dst = observation(tmp_path, 'dst', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    Editor(tmp_path).build('src', (0, 0, 1), 'copy', 'dst', (1, 0, 0), 'target').apply()
    result = read(dst)
    assert len(result['targets']) == 3
    assert len(result['target_scans'][1]['scans']) == 1
    assert result['targets'][1]['obs_id'] == 'dst'
    Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (2, 0, 0), 'frequency').apply()
    assert [(scan['freq_scan'], scan['scan_iter']) for scan in read(dst)['target_scans'][2]['scans']] == [(0, 0), (1, 0), (1, 1)]


def test_move_checks_both_archives_and_retains_original_load_reference(tmp_path):
    src = observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [1]), (ScanType.SKY, [1])])
    dst = observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    Editor(tmp_path).build('src', (1, 0, 0), 'move', 'dst', (1, 0, 0), 'target').apply()
    assert len(read(src)['targets']) == 2
    assert len(scans(dst)) == 2
    assert scans(dst)[1]['load_scan_id'] == 'src-0-0-0'
    assert product(tmp_path, scans(src)[0], '-spr.csv').exists()


@pytest.mark.parametrize('operation', ['copy', 'move', 'delete'])
def test_load_operations_refused_without_changes(tmp_path, operation):
    observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [1])])
    before = digest(tmp_path)
    with pytest.raises(EditError, match='Only SKY'):
        Editor(tmp_path).build('src', (0, 0, 0), operation, 'src', (1, 0, 0))
    assert digest(tmp_path) == before


def test_collateral_load_renumbering_blocks_entire_move(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1]), (ScanType.LOAD, [1])])
    observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    before = digest(tmp_path)
    with pytest.raises(EditError, match='non-SKY'):
        Editor(tmp_path).build('src', (0, 0, 0), 'move', 'dst', (1, 0, 0), 'target')
    assert digest(tmp_path) == before


def test_active_observations_and_other_directories_are_refused(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])], ObsState.SCANNING)
    with pytest.raises(EditError, match='must be ABORTED'):
        Editor(tmp_path).build('src', (0, 0, 0), 'delete')
    with pytest.raises(EditError, match='Invalid observation identifier'):
        Editor(tmp_path).build('../src', (0, 0, 0), 'delete')


def test_existing_output_collision_and_symlink_products_are_refused(tmp_path):
    src = observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    donor = scans(src)[0]
    collision = tmp_path / (donor['files_prefix'].replace('src-0-0-0-', 'dst-0-0-1-') + '-spr.csv')
    collision.write_bytes(b'not part of observation')
    with pytest.raises(EditError, match='already exists'):
        Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (0, 0, 1))
    collision.unlink()
    product(tmp_path, donor, '-sigfig.png').unlink()
    product(tmp_path, donor, '-sigfig.png').symlink_to(product(tmp_path, donor, '-spr.csv'))
    with pytest.raises(EditError, match='Linked'):
        Editor(tmp_path).build('src', (0, 0, 0), 'delete')


def test_plan_refuses_changed_source_before_execution(tmp_path):
    src = observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'delete')
    src.write_text(src.read_text() + '\n')
    with pytest.raises(EditError, match='changed since planning'):
        plan.apply()
    assert len(scans(src)) == 2


def test_failure_during_commit_restores_both_archives(tmp_path, monkeypatch):
    import obs.editor as module
    observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    observation(tmp_path, 'dst', [(ScanType.SKY, [2])])
    before = digest(tmp_path)
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'move', 'dst', (0, 0, 1))
    replace = module.os.replace
    published = 0
    def fail_once(source, destination):
        nonlocal published
        if Path(source).parent.name == 'staged':
            published += 1
            if published == 2:
                raise OSError('injected failure')
        return replace(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_once)
    with pytest.raises(OSError, match='injected'):
        plan.apply()
    after = digest(tmp_path)
    assert {name: after[name] for name in before} == before
    assert not {name for name in after if name not in before and not name.startswith('.obs-editor')}
    assert read(next((tmp_path / '.obs-editor').glob('*/manifest.json')))['state'] == 'rolled_back'


def test_copy_within_same_observation_handles_overlapping_names(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    payload = product(tmp_path, scans(path)[0], '-spr.csv').read_bytes()
    Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'src', (0, 0, 0)).apply()
    assert [scan['scan_iter'] for scan in scans(path)] == [0, 1, 2]
    assert product(tmp_path, scans(path)[0], '-spr.csv').read_bytes() == payload
    assert product(tmp_path, scans(path)[1], '-spr.csv').read_bytes() == payload


def test_delete_unrecorded_sky_placeholder_but_cannot_copy_it(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    data = read(path)
    pending = data['target_scans'][0]['scans'][1]
    for artifact in tmp_path.glob(pending['files_prefix'] + '-*'):
        artifact.unlink()
    pending.update(status={'_type': 'enum.IntEnum', 'instance': 'ScanState', 'value': 'EMPTY'},
                   files_prefix=None, files_directory=None, read_start=None, read_end=None)
    write_json(path, data)
    with pytest.raises(EditError, match='completed SKY'):
        Editor(tmp_path).build('src', (0, 0, 1), 'copy', 'src', (0, 0, 0))
    Editor(tmp_path).build('src', (0, 0, 1), 'delete').apply()
    assert len(scans(path)) == 1


def test_new_products_after_planning_block_execution(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'delete')
    product(tmp_path, scans(path)[0], '-extra.png').write_bytes(b'late renderer')
    with pytest.raises(EditError, match='products changed'):
        plan.apply()
    assert len(scans(path)) == 2


def test_incomplete_transaction_requires_recovery_before_next_edit(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    journal = tmp_path / '.obs-editor' / 'interrupted'
    journal.mkdir(parents=True)
    write_json(journal / 'manifest.json', {'state': 'applying'})
    with pytest.raises(EditError, match='requires recovery'):
        Editor(tmp_path)


def test_foreign_observation_matching_product_prefix_is_not_deleted(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    prefix = scans(path)[0]['files_prefix']
    foreign = tmp_path / (prefix + '-obs.json')
    foreign.write_bytes(b'foreign observation file')
    Editor(tmp_path).build('src', (0, 0, 0), 'delete').apply()
    assert foreign.read_bytes() == b'foreign observation file'


def test_move_within_observation_uses_pre_edit_insertion_position(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [3])])
    donor = product(tmp_path, scans(path)[0], '-spr.csv').read_bytes()
    Editor(tmp_path).build('src', (0, 0, 0), 'move', 'src', (0, 0, 3)).apply()
    assert [scan['scan_iter'] for scan in scans(path)] == [0, 1, 2]
    assert product(tmp_path, scans(path)[2], '-spr.csv').read_bytes() == donor


def test_empty_aborted_archive_can_receive_a_new_target(tmp_path):
    path = observation(tmp_path, 'dst', [(ScanType.SKY, [1])], ObsState.ABORTED)
    observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    Editor(tmp_path).build('dst', (0, 0, 0), 'delete').apply()
    assert read(path)['targets'] == []
    Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (0, 0, 0), 'target').apply()
    assert len(scans(path)) == 1


def test_incompatible_target_or_frequency_is_blocked(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1, 1])])
    path = observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    with pytest.raises(EditError, match='compatible SKY'):
        Editor(tmp_path).build('src', (0, 1, 0), 'copy', 'dst', (0, 0, 0))
    data = read(path)
    data['targets'][0]['id'] = 'Another target'
    write_json(path, data)
    with pytest.raises(EditError, match='Target definitions differ'):
        Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (0, 0, 0))


def test_staging_failure_changes_no_archive_files(tmp_path, monkeypatch):
    import obs.editor as module
    observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    before = digest(tmp_path)
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'delete')
    monkeypatch.setattr(module.shutil, 'copy2', lambda *args: (_ for _ in ()).throw(OSError('stage full')))
    with pytest.raises(OSError, match='stage full'):
        plan.apply()
    after = digest(tmp_path)
    assert {name: after[name] for name in before} == before
    assert read(next((tmp_path / '.obs-editor').glob('*/manifest.json')))['state'] == 'staging_failed'


def placeholder(root, path, index=-1):
    data = read(path)
    planned = data['target_scans'][-1]['scans'][index]
    for artifact in root.glob(planned['files_prefix'] + '-*'):
        artifact.unlink()
    planned.update(status={'_type': 'enum.IntEnum', 'instance': 'ScanState', 'value': 'EMPTY'},
                   scan_type={'_type': 'enum.IntEnum', 'instance': 'ScanType', 'value': 'UNKNOWN'},
                   files_prefix=None, files_directory=None, read_start=None, read_end=None,
                   tgt_acq_dt=None, tgt_acq_altaz=None, pointing_refs=[], sample_times=None)
    write_json(path, data)


def test_target_insertion_renumbers_unrecorded_unknown_placeholder(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    path = observation(tmp_path, 'dst', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    placeholder(tmp_path, path)
    data = read(path)
    data['target_scans'][1]['scans'][1]['tgt_acq_altaz'] = {'alt': 30., 'az': 180.}
    write_json(path, data)
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (1, 0, 0), 'target')
    assert 'dst-1-0-1 -> dst-2-0-1' in plan.report()
    plan.apply()
    planned = read(path)['target_scans'][2]['scans'][1]
    assert planned['scan_type']['value'] == 'UNKNOWN'
    assert planned['status']['value'] == 'EMPTY'
    assert planned['files_prefix'] is None
    assert planned['tgt_idx'] == 2


def test_unknown_entry_with_sample_read_evidence_is_still_protected(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    path = observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    placeholder(tmp_path, path)
    data = read(path)
    data['target_scans'][0]['scans'][0]['read_start'] = {'_type': 'datetime', 'value': '2026-11-13T16:22:05+00:00'}
    write_json(path, data)
    before = digest(tmp_path)
    with pytest.raises(EditError, match='non-SKY'):
        Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (0, 0, 0), 'target')
    assert digest(tmp_path) == before


def test_range_deletion_uses_original_indices_across_targets_and_frequencies(tmp_path):
    path = observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [4, 2]),
                                        (ScanType.SKY, [2]), (ScanType.SKY, [3])])
    before = scans(path)
    survivors = [product(tmp_path, scan, '-spr.csv').read_bytes() for scan in before
                 if not (1, 0, 3) <= (scan['tgt_idx'], scan['freq_scan'], scan['scan_iter']) <= (3, 0, 1)]
    plan = Editor(tmp_path).build('src', None, 'delete', src_scan_range=((1, 0, 3), (3, 0, 1)))
    assert 'inclusive: 7 entries' in plan.report()
    plan.apply()
    assert [product(tmp_path, scan, '-spr.csv').read_bytes() for scan in scans(path)] == survivors
    assert [(s['tgt_idx'], s['freq_scan'], s['scan_iter']) for s in scans(path)] == [
        (0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 0, 2), (2, 0, 0)]


def test_range_deletes_empty_unknown_plans_and_removes_empty_targets(tmp_path, capsys):
    path = observation(tmp_path, 'src', [(ScanType.SKY, [1]), (ScanType.SKY, [1]), (ScanType.SKY, [2])])
    # Turn all scans in targets 1 and 2 into unrecorded plan entries.
    data = read(path)
    for group in data['target_scans'][1:]:
        for scan in group['scans']:
            for artifact in tmp_path.glob(scan['files_prefix'] + '-*'):
                artifact.unlink()
            scan.update(scan_type={'_type': 'enum.IntEnum', 'instance': 'ScanType', 'value': 'UNKNOWN'},
                        status={'_type': 'enum.IntEnum', 'instance': 'ScanState', 'value': 'EMPTY'},
                        files_prefix=None, files_directory=None, read_start=None, read_end=None, sample_times=None)
    write_json(path, data)
    before = digest(tmp_path)
    args = ['--scan_store_dir', str(tmp_path), '--src_obs_id', 'src', '--src_scan_range', '1-0-0', '2-0-1', '--op', 'delete']
    assert main(args + ['--dry-run']) == 0
    assert 'inclusive: 3 entries' in capsys.readouterr().out
    assert digest(tmp_path) == before
    assert main(args) == 0
    assert len(read(path)['targets']) == 1
    assert len(scans(path)) == 1


@pytest.mark.parametrize('start,end', [((1, 0, 0), (0, 0, 0)), ((0, 0, 0), (2, 0, 0))])
def test_invalid_range_changes_nothing(tmp_path, start, end):
    observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    before = digest(tmp_path)
    with pytest.raises(EditError):
        Editor(tmp_path).build('src', None, 'delete', src_scan_range=(start, end))
    assert digest(tmp_path) == before


def test_range_containing_load_is_refused_in_full(tmp_path):
    observation(tmp_path, 'src', [(ScanType.LOAD, [1]), (ScanType.SKY, [2])])
    before = digest(tmp_path)
    with pytest.raises(EditError, match='Only SKY'):
        Editor(tmp_path).build('src', None, 'delete', src_scan_range=((0, 0, 0), (1, 0, 1)))
    assert digest(tmp_path) == before


def test_copy_cannot_take_range(tmp_path):
    observation(tmp_path, 'src', [(ScanType.SKY, [2])])
    with pytest.raises(EditError, match='only available for delete'):
        Editor(tmp_path).build('src', None, 'copy', 'src', (0, 0, 0), src_scan_range=((0, 0, 0), (0, 0, 1)))


@pytest.mark.parametrize('state', [ObsState.READY, ObsState.SCANNING, ObsState.IDLE])
def test_copy_can_read_completed_scan_from_any_source_state(tmp_path, state):
    src = observation(tmp_path, 'src', [(ScanType.SKY, [1])], state)
    dst = observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    original = src.read_bytes()
    plan = Editor(tmp_path).build('src', (0, 0, 0), 'copy', 'dst', (0, 0, 1))
    assert src not in plan.writes
    plan.apply()
    assert src.read_bytes() == original
    assert len(scans(dst)) == 2


@pytest.mark.parametrize('operation', ['move', 'delete'])
@pytest.mark.parametrize('state', [ObsState.READY, ObsState.IDLE])
def test_source_modification_requires_aborted_even_when_scans_completed(tmp_path, operation, state):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])], state)
    observation(tmp_path, 'dst', [(ScanType.SKY, [1])])
    before = digest(tmp_path)
    with pytest.raises(EditError, match='must be ABORTED'):
        Editor(tmp_path).build('src', (0, 0, 0), operation, 'dst', (0, 0, 1))
    assert digest(tmp_path) == before


@pytest.mark.parametrize('operation', ['copy', 'move'])
def test_destination_modification_requires_aborted(tmp_path, operation):
    observation(tmp_path, 'src', [(ScanType.SKY, [1])])
    observation(tmp_path, 'dst', [(ScanType.SKY, [1])], ObsState.IDLE)
    before = digest(tmp_path)
    with pytest.raises(EditError, match='must be ABORTED'):
        Editor(tmp_path).build('src', (0, 0, 0), operation, 'dst', (0, 0, 1))
    assert digest(tmp_path) == before
