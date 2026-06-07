# Filterbank Processing

This note describes the DMD2000 filterbank path used for live SDR observations.
The relevant code is split across two stages:

- `src/obs/scan.py` converts incoming IQ samples into unnormalised scan-level
  filterbank files.
- `src/obs/opt.py` combines completed scan files into SIGPROC `.fil` files,
  applying observation-level normalisation during export.

The live observation path does not retain raw IQ samples. Raw IQ is reduced as it
arrives, then discarded.

## Example Configuration

The numbers below use a typical Airspy/UHF pulsar setup:

- `sample_rate = 6,000,000` samples/sec
- `spectral_resolution = 100` channels
- `filter_bank.temporal_resolution = 1.0` ms
- `filter_bank.sub_bandwidth = 1,800,000` Hz
- `filter_bank.dtype = uint8`
- scan duration is typically `60` sec

`filter_bank.dtype` is the final `.fil` data type. Intermediate `*-fb.dat`
files are written as unnormalised `float32` values.

## Per-Second IQ Processing

The digitiser supplies IQ samples one second at a time. For each second,
`Scan.load_samples()` keeps a flat one-second IQ view for filterbank processing.

### Samples Per Filterbank Row

`Scan._fb_samples_per_row()` calculates:

```text
samples_per_row = round(sample_rate * temporal_resolution_seconds)
```

For a 6 MS/s sample rate and 1 ms temporal resolution:

```text
samples_per_row = round(6,000,000 * 0.001) = 6000
```

Each filterbank row therefore represents 6000 complex IQ samples, or 1 ms of
data.

### Rows Per Second

`Scan._fb_rows_per_sec()` calculates:

```text
rows_per_sec = int(sample_rate) // samples_per_row
```

For the example:

```text
rows_per_sec = 6,000,000 // 6000 = 1000
```

Each second of IQ produces 1000 filterbank rows.

### FFT And Power

For each temporal row, the code computes:

```text
power = abs(fftshift(fft(row))) ** 2
```

For the example, each one-second block becomes:

```text
(1000 rows, 6000 FFT bins)
```

### Sub-Band Selection

The number of FFT bins retained is:

```text
selected_bins = round(samples_per_row * sub_bandwidth / sample_rate)
```

The value is clamped to at least `spectral_resolution` and at most the FFT size.
It must be divisible by `spectral_resolution`.

For the example:

```text
selected_bins = round(6000 * 1,800,000 / 6,000,000) = 1800
```

Because this is the normal live scan path, the selected slice is centred on the
scan centre frequency:

```text
bin_start = (6000 - 1800) // 2 = 2100
kept bins = 2100:3900
```

If `sub_bandwidth` is unset, zero, or larger than the sample rate, the full
sample-rate bandwidth is used.

### Averaging Into Channels

The selected bins are averaged into `spectral_resolution` output channels:

```text
nave = selected_bins // spectral_resolution
```

For the example:

```text
nave = 1800 // 100 = 18
```

Each output channel is the mean of 18 selected FFT bins, giving a per-second
filterbank block of:

```text
(1000 rows, 100 channels)
```

Rows with non-finite mean values are set to zero.

## Per-Scan Intermediate Files

`Scan._fb_store_rows()` stores each one-second filterbank block in a scan-wide
buffer. For a 60 second scan with 1 ms rows and 100 channels:

```text
rows = 60 * 1000 = 60,000
shape = (60,000, 100)
```

When all seconds in the scan are loaded, `Scan._fb_finalize_writer()` writes the
buffer directly to:

```text
<files_prefix>-fb.dat
```

Important details:

- The file contains unnormalised power values.
- The file dtype is fixed to `float32`.
- No trimmed standard deviation is computed in `scan.py`.
- No quantisation to `uint8` happens in `scan.py`.
- `filter_bank.dtype` is preserved for the final `.fil` export stage.

The intermediate size is:

```text
rows * channels * 4 bytes
```

For one 60 second example scan:

```text
60,000 * 100 * 4 = 24,000,000 bytes
```

That is about 24 MB per scan, or about 1.44 GB per hour for this configuration.

## Observation-Level `.fil` Export

`src/obs/opt.py` exports completed SKY filterbank scans with:

```bash
python -m obs.opt --make-fil --dir <scan_directory> --obs <observation_id>
```

`export_filterbank_observation_to_fil()` groups scans by:

```text
(target index, frequency scan index)
```

Each group becomes one `.fil` file.

### Global Julia-Style Normalisation

Before writing a `.fil`, OPT reads all `*-fb.dat` files in the group as
`float32` and computes one robust scale factor over the whole group:

1. Flatten all unnormalised scan values.
2. Sort values low to high.
3. Trim 20% from the low end and 20% from the high end.
4. Compute the sample standard deviation of the remaining values.

This matches the normalisation strategy used by the Julia GQRX processing
script, but applies it to DMD2000 scan groups.

Each scan is then normalised with the same group scale factor:

```text
normalised = spectra / group_trimmed_std
```

Cleanup then follows the Julia-style rules:

- non-finite values become `0`
- values greater than `255` become `0`

Finally, spectra are rounded/clipped/cast to `filter_bank.dtype`, usually
`uint8`.

### Channel Order

The intermediate `*-fb.dat` rows are stored in increasing FFT-bin order after
`fftshift()`. During `.fil` export, OPT reverses the channel axis:

```text
spectra[:, ::-1]
```

This makes the first SIGPROC channel correspond to the highest frequency. The
SIGPROC header uses:

```text
foff = -channel_width_mhz
fch1 = highest_channel_centre_mhz
```

### Frequency Metadata

The output sub-bandwidth is:

```text
sub_bandwidth if configured else sample_rate
```

The channel width is:

```text
channel_width = sub_bandwidth / spectral_resolution
```

For the example:

```text
channel_width = 1,800,000 / 100 = 18,000 Hz
```

The `.fil` header stores frequency values in MHz.

### Gap Filling

The scan metadata records read start/end timestamps for each second. During
export, OPT checks for positive time gaps between adjacent chunks. If a gap is
found, it inserts synthetic spectra.

The synthetic row is based on the mean of edge rows:

- the last rows before the gap
- the first rows after the gap

The number of rows used on each side is controlled by:

```text
filter_bank.gap_mean_duration
```

The generated gap-fill spectra are coerced to the final output dtype before
being written.

## Relationship To The Older Julia/Airspy Flow

The older GQRX flow is:

```text
raw GQRX IQ -> Julia HDF5 -> AirspyMini_B0329p54_psrfits_v0.py -> .fil
```

With full-bandwidth settings, the DMD2000 row-level FFT and channel averaging
are intended to match the Julia reducer closely:

- 1 ms chunks
- `fftshift(fft())`
- power as `abs(...) ** 2`
- average FFT bins into 100 channels
- one global trimmed standard deviation before final `uint8` output

There are still implementation-level differences:

- DMD2000 acquires one second at a time and groups seconds into scans.
- DMD2000 writes unnormalised `float32` scan intermediates before `.fil` export.
- OPT can fill timestamp gaps between chunks or scans.
- The GQRX sidecar module can optionally select a frequency-offset sub-band from
  a stored raw file; the normal live scan path selects the central sub-band.

## Plain English Summary

Every 1 ms of live IQ data becomes one filterbank spectrum. Each spectrum is
made by FFT'ing the IQ row, converting to power, optionally selecting a central
sub-band, and averaging the selected FFT bins into the configured number of
channels.

Each completed scan writes unnormalised `float32` `*-fb.dat` data. Later, OPT
normalises all scans in a target/frequency group together using one Julia-style
trimmed standard deviation, flips channels into SIGPROC order, fills any timing
gaps, converts to the configured final dtype, and writes the `.fil` file.
