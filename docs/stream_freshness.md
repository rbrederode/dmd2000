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
or twice the duration of a consumer sample block. Age measures how long the oldest contributing producer read has waited
since completion, using a monotonic clock. Normal read duration and accumulated
differences between nominal sample time and elapsed wall time do not count as
buffer age.
Exceeding this limit logs a WARNING and still returns the samples with their
original host read timestamps and `sample_age_sec`. Age alone does not disconnect
the SDR, reset the stream, discard the block or abort the observation. This is a
warning threshold rather than the ring capacity. Actual producer/read failures,
timeouts, missing timing records and live stream reset failures still propagate
hardware errors through the existing digitiser error response/advice to TM.

Each producer read records wall-clock start/end times around the low-level read,
as the non-streaming drivers do. The ring retains these intervals alongside the
corresponding absolute sample ranges. A normal one-second producer/consumer
block therefore carries its actual measured read interval to SDP, rather than
an interval extrapolated from a phase anchor and sample count. Sixty nominal
seconds of samples can legitimately span more than sixty wall-clock seconds.
SDP's existing scan `read_start`, `read_end`, and per-block `sample_times`/`gap`
fields preserve these measured times without a scan-model change.

When a producer chunk is split, its measured interval is divided proportionally
by sample count; individual sample acquisition times are not known. When chunks
are combined, the result spans the first contributing start through the last
end, including any internal gaps. Buffered chunks retain their own timestamps
even when newer reads arrive. Overwritten/consumed IQ removes the matching timing
records, and a phase reset clears both. Freshness uses the oldest contributing
producer read's completion so a new tail cannot hide an old buffered prefix.

These are **host read intervals**, not hardware acquisition timestamps: neither
streaming nor non-streaming host read timing can independently detect buffering
inside a device. FIFO restart continues to establish freshness at that layer.

Sample advice includes `timestamp_source: "host_read"`, `stream_phase`,
`sample_age_sec`, `sample_index_start`, `sample_index_end`, `dropped_samples` and
`stream_backend`. Logs report hardware restart, settling discard, ring discard,
first-sample timing and sample-age warnings. The phase number is local to the
SDR instance; dropped-sample totals include unread samples discarded on reset.
Diagnostic fields travel in sample advice and traces; they are not added to the
saved scan-model schema.

The digitiser invalidates the timer generation before a blocking phase reset.
After each read it checks generation and configuration again, and constructs
sample advice from the captured configuration. A read completing during a
transition cannot be relabelled as the next target.

Fake-backend tests cover FIFO restart, settling, auto-gain isolation, pauses,
dropped-sample timing, stale buffered delivery, slow reads without false age
failures, split/merged chunks, metadata persistence, failed resets and
phase-transition reads.
For hardware validation, compare LOAD/SKY transition times with first-sample
timestamps and the power timeline, then repeat multiple integrations of one
target to confirm continuity. Restart both DIG and SDP with the updated API
metadata definitions before this check.

With DEBUG enabled, `Stream SDR timing` records report measured block intervals,
buffer age, producer read timing, effective delivery rate, and ring occupancy.
`producer_timeline_lag_sec` remains a diagnostic comparison against nominal sample
time; it does not trigger the sample-age warning.


## SDP scan identity at target boundaries

SDP checks each ODT sample block against the active digitiser configuration before
loading it. Pending scans must also match the observation ID, target index and
frequency-scan index; identical radio settings or overlapping read-counter ranges
are insufficient to identify a scan.

Scan configuration changes and sample handling share the per-digitiser lock.
When the active identity changes, SDP marks unfinished scans from the previous
identity as aborted and removes them from the pending queue. This cleanup sends
no scan-complete advice to TM. Approved sample handling also applies the same
cleanup defensively before matching a pending scan. A late ODT packet is
acknowledged and discarded before cleanup, so it cannot retire or modify scans
for the new target. Manual scans retain their existing metadata compatibility
and match against the active SDP identity.

Completed scans and LOAD calibration data are retained under the existing
calibration policy. Other digitisers' pending scans are unaffected, and pending
scan counts include all digitisers. This identity check does not change overload
sample dropping, the completion requirement or the number of retry attempts.
