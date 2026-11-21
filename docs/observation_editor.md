# Offline observation editor

`obs.editor` copies, moves or deletes individual SKY scans in a single scan-store
directory. It is an expert archive-maintenance utility for recovering observations
assembled across several observing attempts. It does not contact TM, SDP or WEB,
process spectra, change configuration profiles, or reschedule observations.

Back up the directory before editing. Stop applications that can write the
observations being modified, including TM, SDP and OPT/rendering jobs. An aborted observation
can still be present in TM's memory; a later save could overwrite an offline edit.
Reload/restart the relevant applications after editing and reprocess the affected
observations in Results or OPT. Existing derived results are not refreshed by the
editor.

## Command line

Run from the repository root:

```bash
PYTHONPATH=src python -m obs.editor \
  --scan_store_dir ~/samples/rotation \
  --src_obs_id SOURCE_OBSERVATION_ID \
  --src_scan_id 7-0-0 \
  --operation copy \
  --dest_obs_id DESTINATION_OBSERVATION_ID \
  --dest_scan_id 5-0-0 \
  --insert_level target \
  --dry-run
```

Replace the observation IDs with the full identifiers in their metadata, for
example `ODT-2026-11-13T154424Z-dish003-Rotation`. A scan ID is the three indices
`tgt_idx-freq_scan-iter`, without the observation ID.

`--operation` and `--op` are equivalent. Operations are `copy`, `move` and `delete`.
`--insert_level` defaults to `scan`. `--dry-run` validates the plan and prints its
file operations without creating or changing any files or directories. Omit it to
apply that operation; the plan is printed before execution as well.

Both observations must already exist directly inside `--scan_store_dir`, with
matching `<observation-id>-obs.json` files. Files in nested directories, external
recorded paths and symbolic links are not followed. Copy/move cannot cross scan
stores. Source and destination can be the same observation.

## Eligible observations and scans

A read-only copy source may be in **any observation state**, including READY,
SCANNING or completed/IDLE. The selected scan must still be completed with saved
metadata and SPR data. The source observation and products are not modified.
If they change during planning/staging, execution refuses the stale plan.

Every observation being changed must be **ABORTED**:

| Operation | Source observation | Destination observation |
| --- | --- | --- |
| Copy | Any state; selected scan completed | ABORTED |
| Move | ABORTED | ABORTED |
| Delete (including ranges) | ABORTED | Not applicable |

This also applies when source and destination are the same observation. An empty
aborted archive left after deleting its last target can receive a new target.

Recorded scans must be SKY. Unrecorded EMPTY/UNKNOWN placeholders may also be
selected for deletion; copy/move still require SKY. Copy/move require a completed scan with saved
metadata and a non-empty SPR file. Delete also accepts unrecorded or failed SKY
entries in an aborted observation, allowing unsuccessful planned scans to be
removed without requiring nonexistent files.

LOAD scans cannot be edited. An operation that would renumber a LOAD or another
non-SKY scan indirectly is also refused. Unrecorded `EMPTY`/`UNKNOWN` plan
entries can shift with their target when they have no files, read times or sample
references. Their type stays UNKNOWN; target acquisition details are preserved. Source and destination must describe the
same dish and observing site, because an observation has one site/dish definition
used by downstream coordinate calculations.

## Insertion and renumbering

| `--insert_level` | Meaning of `--dest_scan_id T-F-I` |
| --- | --- |
| `scan` (default) | Insert at iteration I in existing target T and frequency group F. Existing iterations at or above I shift up by one. |
| `frequency` | Insert a new frequency group F into existing target T. I must be 0. Existing frequency indices at or above F shift up by one. |
| `target` | Insert a new target T, copying the source target definition and configuration. F and I must both be 0. Existing target indices at or above T shift up by one. |

Each operation copies **one physical scan**, even when inserting a target or
frequency group. Append at the next index after the current maximum; target
insertion also accepts `T = number of targets` to append a target.

Scan/frequency insertion into an existing target requires compatible target
coordinates, pointing type and scan parameters, plus matching feed, sample rate,
channel count and filterbank configuration. The recorded start time of an offset
scan is not treated as a different target definition. Scan insertion also requires
matching digitiser, tuning and gain within that frequency group. Use target
insertion when the source describes a different target/configuration.

To bring a five-iteration source target into a destination:

1. Copy its first scan using `--insert_level target`, for example source `7-0-0`
   to destination `5-0-0`.
2. Copy the remaining scans using the default insertion level: source `7-0-1`
   to destination `5-0-1`, and so on.
3. Dry-run every command: earlier edits may have changed the relevant indices.

Deleting an iteration reduces that frequency group's scan count by one and shifts
its later iteration indices down by one. Deleting the highest-numbered iteration
does **not** remove the group when other iterations remain. Only deleting its
sole remaining scan removes the frequency group and shifts later frequency
indices down. If no scans remain for the target, its definition, configuration and
scan set are removed and later target indices shift down.

Existing retry-related gaps in numbering are not globally compacted. Each edit
shifts only the later indices affected by that insertion/deletion. Membership
counts and integration totals are updated; a group's `scan_iterations` records
the largest count among its frequency groups when their counts differ. Historical
frequency-planning extents/overlap and scheduling block datetimes remain intact.

Move validates both sides before writing. It plans an insertion and then removes
the original entry. All identifiers refer to positions **before the edit**. For a
move within the same observation, deleting the original can shift the inserted
scan down again. For example, moving iteration 0 to insertion position 3 among
three iterations puts it last, at final iteration 2. The dry-run shows final IDs.

## Delete example

```bash
PYTHONPATH=src python -m obs.editor \
  --scan_store_dir ~/samples/rotation \
  --src_obs_id SOURCE_OBSERVATION_ID \
  --src_scan_id 7-0-2 \
  --op delete \
  --dry-run
```

Delete needs no destination and rejects destination arguments.


For an inclusive range, use `--src_scan_range START END` instead of `--src_scan_id`:

```bash
PYTHONPATH=src python -m obs.editor \
  --scan_store_dir ~/samples/test_editor/ \
  --src_obs_id ODT-2026-11-13T154424Z-dish003-Rotation \
  --src_scan_range 8-0-3 34-0-4 \
  --op delete \
  --dry-run
```

The range includes both endpoints and all existing entries between them, ordered
by `(target, frequency, iteration)`. Both endpoints must exist; missing numbers
inside the range are skipped. Selection is frozen before any renumbering and
processed in descending order. Only the selected original entries are removed.
A range containing a LOAD or another protected entry blocks the entire operation.
The range option is available only for delete. It supports recorded SKY scans as
well as unrecorded EMPTY/UNKNOWN placeholders. Omit `--dry-run` to apply the plan.

## Files and historical measurements

For scans whose identities change, the editor updates observation/target/scan
indices, observation IDs, file prefixes/directories, QA scan IDs and the
observation's SPR filename list. Target/group membership counts, integration
summaries and execution-cursor references are adjusted. It preserves observation
state, all recorded timestamps and historical scheduling blocks.

Products under the exact old scan prefix are copied, renamed or deleted together:
SPR CSV, scan metadata, QA, signal PNG, optional raw IQ/filterbank products, and
other scan-prefixed sidecars. Observation-level `-obs.json` and `-obs.fits` files
are excluded from that bundle. Spectrum, image and other opaque product bytes
are not reprocessed or rewritten. Copies have independent file contents, rather
than hard links to the donor data.

Read start/end, per-second timing and gaps, acquisition times, pointing references,
measured powers, digitiser configuration and `sdp_config_profile` are retained.
In particular, `load_scan_id` remains the **original** calibration reference,
even when it names the source observation. LOAD files stay in place; do not remove
them manually while those references are needed. Later OPT processing retains its
normal calibration-selection behaviour and may choose a different LOAD.

PNGs are intentionally retained byte-for-byte. Their embedded IDs/titles describe
the original display, even though their filenames change. Existing observation
FITS files, total-power/aggregate products, animations and cached Results jobs can
be stale after an edit. Reprocess the edited observations instead of interpreting
those old derived products as newly generated results.

## Execution and recovery

The utility rejects missing required files, unsafe paths, incompatible insertions,
non-SKY renumbering, duplicate output identities and filename collisions. It also
checks that observed files and scan-product lists have not changed since planning.
A directory lock prevents two editors from applying edits concurrently; other
applications do not participate in that lock.

Execution creates `.obs-editor/<transaction>/` inside the scan store. It stages
all output files before removing originals, allowing overlapping renames. It
publishes observation JSON files last. Original files and a `manifest.json` remain
in that transaction directory; they include the ID/file-change report and state.
Ordinary write failures trigger rollback of both affected observations.

The recovery originals use hard links to the original files, while staged output
products are independently copied. These originals protect this transaction's
replacements/deletions, but **do not replace your backup** against unrelated
in-place writers or disk failure. This requires a filesystem that supports hard
links. Staging also needs free disk space for the output copies.

Power loss or forcibly killing the process can interrupt a multi-file commit.
A manifest left in `applying` or `rollback_failed` blocks further edits. Recover
from your backup, or restore the manifest's `originals` from its `originals/`
directory and remove published `outputs` that did not originally exist. Resolve
the transaction state before attempting another edit. `staging_failed` means
publication never began; `rolled_back` means the editor restored the originals;
`committed` means the operation completed.

Recovery directories are retained for inspection and can be archived/removed after
you have verified the edit and retained a separate backup. The editor never edits
real data merely to test itself: tests create synthetic archives in temporary
folders.
