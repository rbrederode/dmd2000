#!/usr/bin/env python3
import numpy as np
import select
import sys
import termios
import tty
from rtlsdr import RtlSdr
from pysigproc import SigprocFile

OUT = "rtlsdr_test.fil"

center_freq_hz = 1420.405751e6
sample_rate_hz = 2.4e6
gain = 40

nchans = 1024
spectra_per_write = 128
navg = 16                       # FFT frames averaged per output spectrum
tsamp = nchans * navg / sample_rate_hz

# SIGPROC wants MHz
fch1_mhz = (center_freq_hz + sample_rate_hz / 2) / 1e6
foff_mhz = -(sample_rate_hz / nchans) / 1e6

hdr = SigprocFile()
hdr.rawdatafile = OUT
hdr.source_name = "rtlsdr"
hdr.machine_id = 0
hdr.telescope_id = 0
hdr.data_type = 1              # filterbank
hdr.fch1 = fch1_mhz            # top channel frequency, MHz
hdr.foff = foff_mhz            # negative because channels descend
hdr.nchans = nchans
hdr.nbits = 32                 # float32 spectra
hdr.tstart = 60000.0           # dummy MJD; replace with real MJD
hdr.tsamp = tsamp              # seconds
hdr.nifs = 1

def key_pressed() -> bool:
    return bool(select.select([sys.stdin], [], [], 0)[0])


def main():
    sdr = RtlSdr()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd) if sys.stdin.isatty() else None

    try:
        sdr.sample_rate = sample_rate_hz
        sdr.center_freq = center_freq_hz
        sdr.gain = gain

        # Discard initial transient samples
        sdr.read_samples(2048)

        if old_settings is not None:
            tty.setcbreak(fd)

        print(f"Writing {OUT}. Press any key to stop and close the file.")

        with open(OUT, "wb") as fout:
            hdr.filterbank_header(fout)

            while True:
                if old_settings is not None and key_pressed():
                    sys.stdin.read(1)
                    print("Key press detected; closing filterbank file.")
                    break

                nsamp = nchans * navg * spectra_per_write
                iq = sdr.read_samples(nsamp)

                frames = iq.reshape(-1, nchans)

                # Windowing is optional but useful for leakage reduction
                win = np.hanning(nchans).astype(np.float32)
                spec = np.fft.fftshift(np.fft.fft(frames * win, axis=1), axes=1)

                power = np.abs(spec) ** 2

                # Average navg FFTs into one time sample
                power = power.reshape(spectra_per_write, navg, nchans).mean(axis=1)

                # Because foff < 0, first column should be highest frequency
                power = power[:, ::-1]

                fout.write(power.astype(np.float32).tobytes())
                fout.flush()
    except KeyboardInterrupt:
        print("Interrupted; closing filterbank file.")
    finally:
        if old_settings is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sdr.close()


if __name__ == "__main__":
    main()
