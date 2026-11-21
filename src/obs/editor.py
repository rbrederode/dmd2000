"""Offline SKY-scan archive editor. Use --dry-run to inspect an exact edit plan.

No SDP/TM connections, scan planning, spectrum processing, or timestamp rewriting
are performed here. Stop other writers and back up the archive before editing.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid


class EditError(ValueError):
    """An edit cannot be performed without violating archive invariants."""


def value(item):
    return item.get('value') if isinstance(item, dict) else item


def scan_key(scan):
    return tuple(scan[name] for name in ('tgt_idx', 'freq_scan', 'scan_iter'))


def scan_id(scan):
    return scan['obs_id'] + '-' + '-'.join(map(str, scan_key(scan)))


def sky(scan):
    return value(scan.get('scan_type')) in ('SKY', 1)


def complete(scan):
    return value(scan.get('status')) in ('COMPLETE', 3)


def unrecorded_placeholder(origin):
    """Unstarted plans have UNKNOWN type until SDP supplies an actual scan."""
    scan = origin.scan
    return (value(scan.get('scan_type')) in ('UNKNOWN', 0)
            and value(scan.get('status')) in ('EMPTY', 0)
            and scan.get('load') is False
            and not origin.products and origin.metadata is None
            # Target acquisition details can be populated on every planned
            # iteration, including iterations that never received any samples.
            and all(scan.get(key) is None for key in ('files_prefix', 'read_start', 'read_end'))
            and not scan.get('pointing_refs') and not scan.get('sample_times'))


def parse_scan_id(text):
    if not re.fullmatch(r'\d+-\d+-\d+', text):
        raise argparse.ArgumentTypeError('Use tgt_idx-freq_scan-iter, for example 7-0-2')
    return tuple(map(int, text.split('-')))


def encoded(data):
    return (json.dumps(data, indent=4, ensure_ascii=False, allow_nan=False) + '\n').encode()


def fingerprint(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def bundle_files(root, prefix):
    """Scan products have a complete prefix; observation-level files are separate."""
    return {path for path in root.iterdir() if path.name.startswith(prefix + '-')
            and not path.name.endswith(('-obs.json', '-obs.fits'))}


@dataclass
class Origin:
    scan: dict
    products: dict[str, Path]
    metadata: dict | None


@dataclass
class Archive:
    path: Path
    data: dict
    original: dict
    cursor_group: dict | None
    cursor_scan: dict | None


@dataclass
class Plan:
    root: Path
    operation: str
    writes: dict[Path, bytes | Path] = field(default_factory=dict)
    removals: set[Path] = field(default_factory=set)
    observed: dict[Path, tuple] = field(default_factory=dict)
    bundles: dict[str, set[Path]] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def report(self):
        lines = [f'{self.operation.upper()} in {self.root}', *self.changes]
        for path in sorted(self.removals):
            lines.append(f'REMOVE {path.name}')
        for path, content in sorted(self.writes.items()):
            lines.append(f'{"WRITE JSON" if isinstance(content, bytes) else "COPY BYTES"} {path.name}'
                         + (f' <- {content.name}' if isinstance(content, Path) else ''))
        lines += ['NOTE ' + warning for warning in self.warnings]
        lines.append(f'{len(self.writes)} files to write; {len(self.removals)} old files to remove.')
        return '\n'.join(lines)

    def apply(self):
        """Stage all outputs, then publish; retain originals and a recovery manifest."""
        lock_path = self.root / '.obs-editor.lock'
        if lock_path.is_symlink():
            raise EditError('Editor lock must not be a symbolic link')
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise EditError('Another editor is modifying this directory') from error
            self._check_unchanged()
            journal_root = self.root / '.obs-editor'
            if journal_root.is_symlink():
                raise EditError('Editor journal directory must not be a symbolic link')
            transaction = journal_root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
            backup, staged = transaction / 'originals', transaction / 'staged'
            backup.mkdir(parents=True)
            staged.mkdir()
            affected = self.removals | set(self.writes)
            originals = {path for path in affected if path.exists()}
            manifest = {'operation': self.operation, 'state': 'staging', 'report': self.report(),
                        'originals': sorted(path.name for path in originals),
                        'outputs': sorted(path.name for path in self.writes)}
            journal = transaction / 'manifest.json'

            def save_state(state):
                manifest['state'] = state
                temporary = journal.with_suffix('.tmp')
                temporary.write_bytes(encoded(manifest))
                temporary.replace(journal)

            save_state('staging')
            try:
                # Originals retain their inode; published copies have independent bytes.
                # They are recovery copies for this transaction, not a substitute for
                # the user's backup against external in-place writers or disk failure.
                for path in sorted(originals):
                    os.link(path, backup / path.name)
                for path, content in self.writes.items():
                    destination = staged / path.name
                    if isinstance(content, bytes):
                        destination.write_bytes(content)
                    else:
                        shutil.copy2(content, destination)
                self._check_unchanged()
            except BaseException:
                save_state('staging_failed')
                raise
            save_state('applying')
            try:
                # Stage first so swaps and overlapping old/new filenames are safe.
                for path in sorted(self.removals):
                    path.unlink()
                # Observation catalogues are published last.
                for path in sorted(self.writes, key=lambda p: (p.name.endswith('-obs.json'), p.name)):
                    os.replace(staged / path.name, path)
            except BaseException:
                try:
                    for path in self.writes:
                        if path not in originals and path.exists():
                            path.unlink()
                    for path in originals:
                        restore = staged / path.name
                        shutil.copy2(backup / path.name, restore)
                        os.replace(restore, path)
                    save_state('rolled_back')
                except BaseException:
                    save_state('rollback_failed')
                    raise EditError(f'Rollback needs manual recovery: {transaction}')
                raise
            save_state('committed')
            return transaction

    def _check_unchanged(self):
        for prefix, paths in self.bundles.items():
            if bundle_files(self.root, prefix) != paths:
                raise EditError(f'Scan products changed since planning; rebuild the plan: {prefix}')
        for path, previous in self.observed.items():
            if path.is_symlink() or not path.is_file() or fingerprint(path) != previous:
                raise EditError(f'File changed since planning; rebuild the plan: {path.name}')
        for path in self.writes:
            if path.is_symlink() or (path.exists() and path not in self.observed):
                raise EditError(f'Output collision: {path.name}')


class Editor:
    def __init__(self, scan_store_dir):
        supplied = Path(scan_store_dir).expanduser()
        if any(part.is_symlink() for part in (supplied, *supplied.parents)):
            raise EditError('Use a real scan_store_dir, not a symbolic link')
        self.root = supplied.resolve(strict=True)
        if not self.root.is_dir():
            raise EditError('scan_store_dir must be a directory')
        journal = self.root / '.obs-editor'
        if journal.is_symlink():
            raise EditError('Editor journal directory must not be a symbolic link')
        for path in journal.glob('*/manifest.json'):
            if json.loads(path.read_text()).get('state') in ('applying', 'rollback_failed'):
                raise EditError(f'Unfinished edit requires recovery before further editing: {path}')
        self.archives = {}
        self.origins = {}
        self.plan = Plan(self.root, '')

    def read_json(self, path):
        if path.parent != self.root or path.is_symlink() or not path.is_file():
            raise EditError(f'Missing, linked or out-of-directory file: {path.name}')
        self.plan.observed[path] = fingerprint(path)
        try:
            data = json.loads(path.read_text())
        except (ValueError, UnicodeError) as error:
            raise EditError(f'Invalid JSON: {path.name}') from error
        if not isinstance(data, dict):
            raise EditError(f'Expected JSON object: {path.name}')
        return data

    def load(self, obs_id):
        if obs_id in self.archives:
            return self.archives[obs_id]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:+-]*', obs_id):
            raise EditError('Invalid observation identifier')
        path = self.root / (obs_id.replace(':', '') + '-obs.json')
        data = self.read_json(path)
        if data.get('_type') != 'ObsModel' or data.get('obs_id') != obs_id:
            raise EditError(f'Observation identifier/model mismatch: {path.name}')
        groups = data.get('target_scans', [])
        group_indices = [group.get('tgt_idx') for group in groups]
        target_indices = [target.get('tgt_idx') for target in data.get('targets', [])]
        config_indices = [config.get('tgt_idx') for config in data.get('target_configs', [])]
        if group_indices != list(range(len(groups))) or target_indices != group_indices or config_indices != group_indices:
            raise EditError(f'{obs_id}: targets, target_configs and target_scans must have matching contiguous target indices')
        keys = set()
        for group in groups:
            for scan in group['scans']:
                key = scan_key(scan)
                if (scan.get('obs_id') != obs_id or key[0] != group['tgt_idx'] or key in keys
                        or any(type(index) is not int or index < 0 for index in key)):
                    raise EditError(f'{obs_id}: invalid or duplicate scan identity {key}')
                keys.add(key)
                self.origins[id(scan)] = self.origin(scan)
        cursor = data.get('tgt_idx', 0)
        cursor_group = groups[cursor] if 0 <= cursor < len(groups) else None
        cursor_index = data.get('tgt_scan', 0)
        cursor_scan = (cursor_group['scans'][cursor_index] if cursor_group and
                       0 <= cursor_index < len(cursor_group['scans']) else None)
        archive = Archive(path, data, deepcopy(data), cursor_group, cursor_scan)
        self.archives[obs_id] = archive
        return archive

    def origin(self, scan):
        prefix = scan.get('files_prefix')
        products, metadata = {}, None
        if prefix:
            if not isinstance(prefix, str) or Path(prefix).name != prefix or '\\' in prefix or prefix in ('.', '..'):
                raise EditError('Invalid scan files_prefix')
            members = bundle_files(self.root, prefix)
            self.plan.bundles[prefix] = members
            for path in members:
                if path.is_symlink() or not path.is_file():
                    raise EditError(f'Linked/non-file scan product: {path.name}')
                products[path.name[len(prefix):]] = path
                self.plan.observed[path] = fingerprint(path)
            meta_path = products.get('-meta.json')
            if meta_path:
                metadata = self.read_json(meta_path)
                if metadata.get('obs_id') != scan['obs_id'] or scan_key(metadata) != scan_key(scan):
                    raise EditError(f'Scan metadata identity differs from observation entry: {meta_path.name}')
            if complete(scan) and ('-spr.csv' not in products or metadata is None):
                raise EditError(f'Completed scan lacks saved metadata/SPR: {scan_id(scan)}')
        elif complete(scan):
            raise EditError(f'Completed scan lacks files_prefix: {scan_id(scan)}')
        return Origin(deepcopy(scan), products, metadata)

    @staticmethod
    def require_aborted(archive):
        state = value(archive.data.get('obs_state'))
        if state not in ('ABORTED', 6):
            raise EditError(f'{archive.data["obs_id"]} must be ABORTED before modifying it (state={state})')

    @staticmethod
    def find(archive, key):
        for group in archive.data['target_scans']:
            for scan in group['scans']:
                if scan_key(scan) == key:
                    return scan
        raise EditError(f'Scan not found: {archive.data["obs_id"]}-{key[0]}-{key[1]}-{key[2]}')

    def insert(self, source, destination, selected, key, level):
        target, frequency, iteration = key
        data = destination.data
        groups = data['target_scans']
        src_target = selected['tgt_idx']
        if level == 'target':
            if target > len(groups) or frequency != 0 or iteration != 0:
                raise EditError('Target insertion needs tgt_idx in 0..target count and freq_scan=iter=0')
            definition = deepcopy(source.data['targets'][src_target])
            config = deepcopy(source.data['target_configs'][src_target])
            group = deepcopy(source.data['target_scans'][src_target])
            group['scans'] = []
            data['targets'].insert(target, definition)
            data['target_configs'].insert(target, config)
            groups.insert(target, group)
            self.reindex_targets(destination)
        else:
            if target >= len(groups):
                raise EditError('Destination target does not exist; use --insert_level target')
            group = groups[target]
            def identity(item):
                result = {k: v for k, v in item.items() if k not in ('obs_id', 'tgt_idx')}
                if isinstance(result.get('scan'), dict):
                    result['scan'] = {k: v for k, v in result['scan'].items() if k != 'start'}
                return result
            if identity(source.data['targets'][src_target]) != identity(data['targets'][target]):
                raise EditError('Target definitions differ; use --insert_level target to retain the source target')
            for field in ('feed_type', 'sample_rate', 'spectral_resolution', 'filter_bank'):
                if source.data['target_configs'][src_target].get(field) != data['target_configs'][target].get(field):
                    raise EditError(f'Target configurations differ in {field}; use --insert_level target')
            frequencies = {scan['freq_scan'] for scan in group['scans']}
            if level == 'frequency':
                if iteration != 0 or frequency > max(frequencies, default=-1) + 1:
                    raise EditError('Frequency insertion needs iter=0 and an existing or next frequency index')
                for scan in group['scans']:
                    if scan['freq_scan'] >= frequency:
                        scan['freq_scan'] += 1
            else:
                entries = [scan for scan in group['scans'] if scan['freq_scan'] == frequency]
                if not entries or iteration > max(scan['scan_iter'] for scan in entries) + 1:
                    raise EditError('Scan insertion needs an existing frequency group and an existing or next iteration index')
                fields = ('dig_id', 'center_freq', 'sample_rate', 'spectral_resolution', 'gain', 'filter_bank')
                if any(not sky(scan) or any(scan.get(field) != selected.get(field) for field in fields) for scan in entries):
                    raise EditError('Destination frequency group is not compatible SKY data (digitiser, tuning, gain or channels differ)')
                for scan in entries:
                    if scan['scan_iter'] >= iteration:
                        scan['scan_iter'] += 1
        copied = deepcopy(selected)
        self.origins[id(copied)] = self.origins[id(selected)]
        copied.update(obs_id=data['obs_id'], tgt_idx=target, freq_scan=frequency, scan_iter=iteration)
        group['scans'].append(copied)
        return copied

    def delete(self, archive, selected):
        groups = archive.data['target_scans']
        group = next(group for group in groups if any(scan is selected for scan in group['scans']))
        target, frequency, iteration = scan_key(selected)
        group['scans'] = [scan for scan in group['scans'] if scan is not selected]
        if not group['scans']:
            for field in ('targets', 'target_configs', 'target_scans'):
                archive.data[field].pop(target)
            self.reindex_targets(archive)
        elif any(scan['freq_scan'] == frequency for scan in group['scans']):
            for scan in group['scans']:
                if scan['freq_scan'] == frequency and scan['scan_iter'] > iteration:
                    scan['scan_iter'] -= 1
        else:
            for scan in group['scans']:
                if scan['freq_scan'] > frequency:
                    scan['freq_scan'] -= 1

    @staticmethod
    def reindex_targets(archive):
        for index, (target, config, group) in enumerate(zip(archive.data['targets'], archive.data['target_configs'], archive.data['target_scans'])):
            for item in (target, config, group, *group['scans']):
                item.update(obs_id=archive.data['obs_id'], tgt_idx=index)

    def build(self, src_obs_id, src_scan_id, operation, dest_obs_id=None, dest_scan_id=None, insert_level='scan', *, src_scan_range=None):
        if operation not in ('copy', 'move', 'delete') or insert_level not in ('scan', 'frequency', 'target'):
            raise EditError('Unknown operation or insert_level')
        self.plan.operation = operation
        source = self.load(src_obs_id)
        if operation != 'copy':
            self.require_aborted(source)
        if src_scan_range is not None:
            if operation != 'delete' or src_scan_id is not None:
                raise EditError('src_scan_range is only available for delete, instead of src_scan_id')
            start, end = src_scan_range
            if start > end:
                raise EditError('Scan range start must not be after its end')
            self.find(source, start)
            self.find(source, end)
            # Freeze the original membership before any indices are shifted.
            selections = [scan for group in source.data['target_scans'] for scan in group['scans']
                          if start <= scan_key(scan) <= end]
            self.plan.changes.append(f'DELETE RANGE {"-".join(map(str, start))} through '
                                     f'{"-".join(map(str, end))} inclusive: {len(selections)} entries')
        else:
            if src_scan_id is None:
                raise EditError('Supply src_scan_id or src_scan_range')
            selections = [self.find(source, src_scan_id)]
        for selected in selections:
            if not sky(selected) and not (operation == 'delete' and unrecorded_placeholder(self.origins[id(selected)])):
                raise EditError(f'Only SKY scans or unrecorded EMPTY/UNKNOWN placeholders may be deleted; '
                                f'only SKY scans may be copied/moved: {scan_id(selected)}')
        selected = selections[0]
        if operation != 'delete':
            if not dest_obs_id or dest_scan_id is None:
                raise EditError('Copy/move require dest_obs_id and dest_scan_id')
            if not complete(selected):
                raise EditError('Copy/move require a completed SKY scan with saved metadata and SPR')
            if self.origins[id(selected)].products['-spr.csv'].stat().st_size == 0:
                raise EditError('Copy/move require a non-empty SPR file')
            destination = self.load(dest_obs_id)
            self.require_aborted(destination)
            # An observation has one dish/site definition, so mixing sites would
            # silently give downstream coordinate calculations the wrong location.
            for field in ('dsh_id', 'latitude', 'longitude', 'height', 'diameter', 'f/d_ratio'):
                if source.data.get(field) != destination.data.get(field):
                    raise EditError(f'Observations differ in {field}; cannot combine their scan metadata')
            self.insert(source, destination, selected, dest_scan_id, insert_level)
        if operation in ('move', 'delete'):
            for selected in sorted(selections, key=scan_key, reverse=True):
                self.delete(source, selected)
        for archive in self.archives.values():
            if archive.data != archive.original:
                self.finalize(archive)
        self.prepare_files()
        self.plan.warnings += [
            'Original read/acquisition times, pointing references, sample values and load_scan_id are preserved.',
            'PNGs are retained byte-for-byte; embedded labels can refer to the original observation/scan.',
            'Reprocess edited observations. Existing FITS, aggregates, animations and Results caches are not refreshed.',
            'No TM/SDP/OPT/archive writers may modify edited observations. Copy sources are read-only; changes during staging block execution. The lock coordinates editors only.',
        ]
        for path in self.plan.writes:
            if path.exists() and path not in self.plan.removals and path not in (a.path for a in self.archives.values()):
                raise EditError(f'Output filename already exists: {path.name}')
        self.plan._check_unchanged()
        return self.plan

    def finalize(self, archive):
        data = archive.data
        groups = data['target_scans']
        for index, group in enumerate(groups):
            group['scans'].sort(key=lambda scan: scan_key(scan)[1:])
            frequencies = {scan['freq_scan'] for scan in group['scans']}
            group['freq_scans'] = len(frequencies)
            group['scan_iterations'] = max((sum(scan['freq_scan'] == f for scan in group['scans']) for f in frequencies), default=0)
            group['scan_duration'] = max((scan['duration'] for scan in group['scans']), default=0)
            data['target_configs'][index]['integration_time'] = max(
                (sum(scan['duration'] for scan in group['scans'] if scan['freq_scan'] == f) for f in frequencies), default=0)
        if archive.cursor_group is not None and any(group is archive.cursor_group for group in groups):
            data['tgt_idx'] = next(index for index, group in enumerate(groups) if group is archive.cursor_group)
            entries = archive.cursor_group['scans']
            data['tgt_scan'] = next((index for index, scan in enumerate(entries) if scan is archive.cursor_scan),
                                    min(data.get('tgt_scan', 0), max(0, len(entries) - 1)))
        elif archive.original.get('tgt_idx', 0) >= len(archive.original['target_scans']):
            data['tgt_idx'], data['tgt_scan'] = len(groups), 0
        else:
            data['tgt_idx'] = min(data.get('tgt_idx', 0), len(groups))
            data['tgt_scan'] = 0
        data['total_integration_time'] = sum(scan['duration'] for group in groups for scan in group['scans'])
        # Retain historical scheduling block and all recorded timestamps.
        seconds = int(data['total_integration_time'] + data.get('estimated_slewing_time', 0))
        data['estimated_observation_duration'] = f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'

    def prepare_files(self):
        from sdk.models.scan import ScanModel
        from sdk.util import gen_file_prefix

        owners = {}
        for archive in self.archives.values():
            for group in archive.data['target_scans']:
                for scan in group['scans']:
                    original = self.origins[id(scan)]
                    old = original.scan
                    if scan_id(scan) == scan_id(old):
                        continue
                    if not sky(old) and not unrecorded_placeholder(original):
                        raise EditError(f'Edit would renumber a non-SKY scan: {scan_id(old)}')
                    owners.setdefault(id(original), []).append(scan)
                    metadata = deepcopy(original.metadata if original.metadata is not None else scan)
                    metadata.update({key: scan[key] for key in ('obs_id', 'tgt_idx', 'freq_scan', 'scan_iter')})
                    if 'scan_id' in metadata:
                        metadata['scan_id'] = scan_id(scan)
                    if 'scan_id' in scan:
                        scan['scan_id'] = scan_id(scan)
                    if original.products:
                        model = ScanModel.from_dict(metadata)
                        prefix = gen_file_prefix(dt=model.read_start, entity_id=model.dig_id, gain=model.gain,
                            duration=model.duration, sample_rate=model.sample_rate, center_freq=model.center_freq,
                            spectral_resolution=model.spectral_resolution, instance_id=model.scan_id, scan_type=model.scan_type)
                        scan['files_prefix'] = metadata['files_prefix'] = prefix
                        scan['files_directory'] = metadata['files_directory'] = str(self.root)
                        for suffix, path in original.products.items():
                            output = self.root / (prefix + suffix)
                            if output in self.plan.writes:
                                raise EditError(f'Two scans would write the same file: {output.name}')
                            if suffix == '-meta.json':
                                content = encoded(metadata)
                            elif suffix == '-qa.json':
                                qa = self.read_json(path)
                                qa['scan_id'] = scan_id(scan)
                                content = encoded(qa)
                            else:
                                content = path
                            self.plan.writes[output] = content
                    self.plan.changes.append(f'IDENTITY {scan_id(old)} -> {scan_id(scan)}; LOAD reference {old.get("load_scan_id")!r} unchanged')
        # Remove bundles only when their original identity no longer exists.
        live_originals = {id(self.origins[id(scan)]) for a in self.archives.values()
                          for group in a.data['target_scans'] for scan in group['scans']
                          if scan_id(scan) == scan_id(self.origins[id(scan)].scan)}
        for origin in {id(origin): origin for origin in self.origins.values()}.values():
            if id(origin) not in live_originals:
                if not sky(origin.scan) and not unrecorded_placeholder(origin):
                    raise EditError(f'Edit would remove a non-SKY scan: {scan_id(origin.scan)}')
                self.plan.removals.update(origin.products.values())
                if id(origin) not in owners:
                    self.plan.changes.append(f'DELETE ENTRY {scan_id(origin.scan)}')
        for archive in self.archives.values():
            if archive.data == archive.original:
                continue
            # Rebuild the filename list from surviving physical scan products.
            archive.data['spr_scans'] = [scan['files_prefix'] + '-spr.csv'
                for group in archive.data['target_scans'] for scan in group['scans']
                if scan.get('files_prefix') and ((self.root / (scan['files_prefix'] + '-spr.csv')).exists()
                   or (self.root / (scan['files_prefix'] + '-spr.csv')) in self.plan.writes)]
            self.validate(archive)
            self.plan.writes[archive.path] = encoded(archive.data)
            self.plan.changes.append(f'UPDATE OBSERVATION {archive.data["obs_id"]}: {len(archive.data["targets"])} targets, '
                f'{sum(len(group["scans"]) for group in archive.data["target_scans"])} scans; state and recorded timestamps retained')

    @staticmethod
    def validate(archive):
        from sdk.models.obs import ObsModel
        keys = [scan_key(scan) for group in archive.data['target_scans'] for scan in group['scans']]
        if len(keys) != len(set(keys)):
            raise EditError('Edit would produce duplicate scan identities')
        try:
            ObsModel.from_dict(deepcopy(archive.data))
        except Exception as error:
            raise EditError(f'Edited observation does not validate: {archive.path.name}: {error}') from error


def parser():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--scan_store_dir', required=True, help='Single archive directory, e.g. ~/samples/rotation')
    arguments.add_argument('--src_obs_id', required=True)
    selection = arguments.add_mutually_exclusive_group(required=True)
    selection.add_argument('--src_scan_id', type=parse_scan_id, help='tgt_idx-freq_scan-iter')
    selection.add_argument('--src_scan_range', nargs=2, type=parse_scan_id, metavar=('START', 'END'),
                           help='Delete existing scans between two inclusive tgt_idx-freq_scan-iter IDs')
    arguments.add_argument('--operation', '--op', required=True, choices=('move', 'copy', 'delete'))
    arguments.add_argument('--dest_obs_id')
    arguments.add_argument('--dest_scan_id', type=parse_scan_id, help='Insertion position tgt_idx-freq_scan-iter')
    arguments.add_argument('--insert_level', default='scan', choices=('scan', 'frequency', 'target'))
    arguments.add_argument('--dry-run', action='store_true', help='Print and validate the plan without writing any files')
    return arguments


def main(argv=None):
    arguments = parser().parse_args(argv)
    execution_started = False
    try:
        if arguments.operation == 'delete' and (arguments.dest_obs_id is not None or arguments.dest_scan_id is not None):
            raise EditError('Delete does not accept destination identifiers')
        editor = Editor(arguments.scan_store_dir)
        plan = editor.build(arguments.src_obs_id, arguments.src_scan_id, arguments.operation,
                            arguments.dest_obs_id, arguments.dest_scan_id, arguments.insert_level,
                            src_scan_range=arguments.src_scan_range)
        print(plan.report())
        if arguments.dry_run:
            print('DRY RUN: no files or directories changed.')
        else:
            execution_started = True
            transaction = plan.apply()
            print(f'APPLIED. Recovery originals and manifest: {transaction}')
        return 0
    except (EditError, OSError, ValueError) as error:
        detail = ('Inspect .obs-editor recovery manifests if a transaction began.' if execution_started
                  else 'No files or directories changed.')
        print(f'BLOCKED/FAILED: {error}\n{detail}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
