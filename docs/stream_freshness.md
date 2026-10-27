# Streaming digitiser freshness

`airstream` and `rtlstream` start a new acquisition phase when scanning starts
for a different observation, target or frequency scan. Stopping scanning stops
the producer, stops hardware acquisition and clears buffered samples. Configuration
changes also stop and clear the stream. Consecutive integrations with the same
scanning identifiers keep the acquisition running. The SDP read counter remains
continuous across resets.

On restart, Airspy acquisition is deactivated/reactivated to reset the SoapyAirspy
FIFO. RTL acquisition resets the USB buffer before synchronous reads resume.
Both discard a settling interval before recording. Automatic gain measurements
are isolated from the recording ring; Soapy gain candidates restart acquisition
and discard settling samples before measurement. This does not change the
Gaussianity criterion used to select gain.

Optional settings belong in the digitiser's `sdr_config`:

```json
{
  "settle_seconds": 0.1,
  "max_sample_age_sec": 2.0
}
```

`settle_seconds` defaults to 0.1 seconds and accepts zero to disable settling.
The correct relay/RF settling interval should be checked on the hardware.
`max_sample_age_sec` must be positive; its default is the greater of two seconds
or twice the duration of a consumer sample block. Age measures how far delivery
lags the estimated acquisition end of that block, using a monotonic clock.
Exceeding this limit raises a hardware error and sends no samples from that read.
A live stream reset failure also propagates an error. The existing digitiser
error response/advice allows TM to abort the associated observation.

Timestamps are anchored to completion of the first producer read, minus its
sample duration. Later timestamps follow sample indexes, including gaps caused
by overwritten samples. Every restarted phase obtains a new anchor, so an idle
pause does not appear as uninterrupted acquisition. These are **host estimates**:
they cannot independently detect stale data inside a device or provide precise
hardware timing. FIFO restart establishes freshness at that layer.

Sample advice includes `timestamp_source: "host_estimated"`, `stream_phase`,
`sample_age_sec`, `sample_index_start`, `sample_index_end`, `dropped_samples` and
`stream_backend`. Logs report hardware restart, settling discard, ring discard,
first-sample timing and freshness failures. The phase number is local to the
SDR instance; dropped-sample totals include unread samples discarded on reset.
Diagnostic fields travel in sample advice and traces; they are not added to the
saved scan-model schema.

The digitiser invalidates the timer generation before a blocking phase reset.
After each read it checks generation and configuration again, and constructs
sample advice from the captured configuration. A read completing during a
transition cannot be relabelled as the next target.

Fake-backend tests cover FIFO restart, settling, auto-gain isolation, pauses,
dropped-sample timing, stale delivery, failed resets and phase-transition reads.
For hardware validation, compare LOAD/SKY transition times with first-sample
timestamps and the power timeline, then repeat multiple integrations of one
target to confirm continuity. Restart both DIG and SDP with the updated API
metadata definitions before this check.
