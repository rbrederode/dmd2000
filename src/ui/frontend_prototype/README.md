# Local engineering console and OPT workspace

The existing frontend prototype now includes a working **Results → Observation
Processing** workspace backed by the Python OPT pipeline.

## Start

Activate the project's Python environment and install the repository dependencies
(`flask` is already listed in `src/requirements.txt`). From the repository root:

```bash
PYTHONPATH=src python -m ui.opt_service --data-root ~/samples
```

Open **http://127.0.0.1:8765/#results**. The existing Status, Schedule and other
prototype views are retained. The OPT workspace requires this service rather
than a plain static-file server.

Use repeatable `--data-root PATH` options to allow observations elsewhere.
`--port` defaults to 8765. The service binds only to 127.0.0.1, accepts localhost
Host headers and same-origin JSON requests, and only reads observation directories
inside configured roots (including resolved symlinks). It is a local workstation
prototype, not a multiuser/remote deployment.

Generated results go into the session directory printed at startup, not the
source observation directory. To retain them at a known location:

```bash
PYTHONPATH=src python -m ui.opt_service \
  --data-root ~/samples \
  --cache-dir /tmp/dmd2000-opt-results \
  --port 8765
```

Stop with Ctrl+C; running workers are terminated. Restarting the service creates
a new in-memory job registry. Previously exported files remain on disk, but the
workspace does not yet reopen historical job folders automatically.

## Review, process, explore

1. Choose an observation directory from the suggestions or enter a path.
   Observations load automatically when the directory changes; **Find observations**
   refreshes the list. The scrollable table shows ten rows at a time, with the
   observation name/ID, dish, completed/total scan counts, counts by completed
   scan type and completed integration time. Use the filter to search names,
   IDs, dishes or types using the filter beside **Find observations**. Select a
   row and click **Open observation** in that same control pane. The catalogue collapses after opening; expand its
   heading to choose another observation. OPT reads the usual `<id>-obs.json` and scan products;
   an Observation Velocity CSV alone is not a processing input.
2. In **Input scans**, use the checkboxes to include/exclude physical scan
   iterations. Selecting a row inspects it without changing its checkbox.
   Search, type filtering, Include/Exclude all, Previous/Next and arrow keys
   help navigate the list. Metadata scans not marked COMPLETE start excluded
   and their checkboxes are disabled.
3. Choose the **Configuration profile** in **Analysis settings** at the bottom.
   This selects the processing pipeline steps; changing it requires a full
   reprocess rather than a cached plot update. Click **Process observation**. A background process runs the accepted batch
   without terminal prompts, partial aggregate displays or GUI popups. Progress
   and an expandable log remain available. **Cancel** terminates the worker.
4. On completion, **Aggregate scans** lists complete load/calibration and SKY results
   by target, frequency scan and digitiser. Click an aggregate for its full
   signal display. Incomplete aggregates are omitted with a warning.
5. Switch directly between **Signal**, **Velocity**, **Total Power**,
   **L-V Diagram**, **Aggregate**, and **Details**. Timeline, overview and L–V
   show the observation-wide results. Velocity defaults to the selected SKY
   aggregate; **All SKY aggregates** overlays the complete observation.
   **Open image** opens the full-resolution PNG. Calibration rows have a signal
   view but no H I velocity spectrum.
6. Change baseline limits/type or contour levels/smoothing, then **Update plots**
   to re-render velocity/L–V views from cached native spectra, without loading
   and processing the recorded files again. The selected completed aggregate
   remains selected. Changing the input exclusions or processing profile requires
   **Reprocess observation**, which reruns the scientific pipeline.
7. **Export results** downloads a ZIP with completed plots, CSV products, native
   spectrum caches, the processing settings and a manifest.

Plots remain visible while a new job runs. Changed settings/exclusions show an
out-of-date banner. A failed or cancelled job does not replace the last completed
results. The workspace remembers directory, profile, analysis settings and scan
exclusions in browser-local storage; a page reload does not currently reconnect
to previous running/completed jobs automatically.

Baseline subtraction and L–V colour/contour semantics are the same as documented
in [OPT usage](../../../docs/opt.md). Amplitudes are not normalized. Signal displays
show the completed processing-pipeline spectra; baseline controls affect the
velocity and L–V analysis views. Cached reanalysis does not mutate those signal
images or original calibrated powers.

## Implementation

- `ui.opt_service`: Flask static/API server and isolated subprocess job lifecycle.
- `ui.opt_worker`: noninteractive OPT replay, completed-aggregate rendering and exports.
- `ui.opt_reanalysis`: derived plots from cached native LSRK data.
- `frontend_prototype/opt.js`: workspace state, scan selection, polling and plot controls.

Each worker renders with Matplotlib Agg and stdin disconnected. Python processing
and figure state never run in a Flask request thread. Job results are published
only after successful completion. Only completed, manifest-listed artifacts can
be downloaded. Raw observation inputs and their metadata are not rewritten.

The API supports configuration discovery, observation listing/opening, starting
and cancelling processing jobs, polling progress, cached reanalysis and artifact
download. The browser exchanges metadata and PNGs with this local service;
recorded spectra remain on the workstation.


## Catalogue totals and connection status

The directory catalogue reads metadata only, without loading spectra. It lists
newest observations first. Completed counts and integration times use only
physical scans whose status is COMPLETE; integration time is the sum of their
recorded scan durations, including SKY and calibration types. Planned observation
totals and incomplete scans are not included. An unreadable metadata file appears
as an unavailable row and does not prevent other observations being listed.

The badge displays **Local OPT connected** while the backend responds. A
lightweight health check runs every five seconds (with a four-second timeout).
On a failed connection, it turns red and reads **OPT Disconnected**. It returns
to connected automatically when the service responds again. A temporary outage
pauses job-status polling without discarding the workspace; when the same service
returns, polling resumes. Restarting the backend still clears its in-memory job
registry, as noted above. The Results heading is **Observation Processing**;
left tabs are **Input scans** and **Aggregate scans**, and the right view tabs
are **Signal**, **Velocity**, **Total Power**, **L-V Diagram**, **Aggregate** and
**Details**.
