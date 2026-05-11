from astropy.io import fits
import numpy as np
import datetime

hdul = fits.open('~/samples/ODT-2026-03-11T2100Z-dish002-obs.fits')

# --- Print full header of primary HDU ---
print("--- PRIMARY HDU HEADER ---")
print(repr(hdul[0].header))

# --- Inspect TARGET table ---
print("\n--- TARGETS COLUMNS ---")
print(hdul['TARGETS'].columns)
print("\nData sample:")
print(hdul['TARGETS'].data)

# --- Inspect TARGET_CONFIG table ---
print("\n--- TARGET_CONFIG COLUMNS ---")
print(hdul['TARGET_CONFIG'].columns)
print("\nData sample:")
print(hdul['TARGET_CONFIG'].data)

# --- Inspect TARGET_SCANS table ---
print("\n--- TARGET_SCANS COLUMNS ---")
print(hdul['TARGET_SCANS'].columns)
print("\nData sample:")
print(hdul['TARGET_SCANS'].data)

hdul.close()
exit(1)

from astropy.io import fits
import numpy as np

# --- Sample Observation JSON (simplified for clarity) ---
obs_id = 'ODT-2026-03-11T21:00Z-dish001'
title = 'Test Dish 001'
dish_id = 'dish001'
diameter = 0.7
f_d_ratio = 0.26
latitude = 53.187052
longitude = -2.256079
user_email = 'ray.brederode@skao.int'

# --- Primary HDU ---
primary_hdr = fits.Header()
primary_hdr['OBS_ID'] = obs_id
primary_hdr['TITLE'] = title
primary_hdr['DISH_ID'] = dish_id
primary_hdr['DIAMETER'] = diameter
primary_hdr['HIERARCH F_D_RATIO'] = f_d_ratio
primary_hdr['LATITUDE'] = latitude
primary_hdr['HIERARCH LONGITUDE'] = longitude
primary_hdr['HIERARCH USER_EMAIL'] = user_email

primary_hdu = fits.PrimaryHDU(header=primary_hdr)

# --- TARGET_CONFIG Table ---
# Example data from your JSON
tgt_idx = np.array([0, 1], dtype=np.int64)
feed_type = np.array(['LOAD', 'H3T_1420'], dtype='S20')
gain = np.array([23, 23], dtype=np.int64)
center_freq = np.array([1420400000, 1420400000], dtype=np.float64)
bandwidth = np.array([2000000, 2000000], dtype=np.float64)
sample_rate = np.array([2048000, 2048000], dtype=np.float64)
integration_time = np.array([60, 60], dtype=np.int64)
spectral_resolution = np.array([2048, 2048], dtype=np.int64)

cols_config = fits.ColDefs([
    fits.Column(name='tgt_idx', format='K', array=tgt_idx),
    fits.Column(name='feed_type', format='20A', array=feed_type),
    fits.Column(name='gain', format='K', array=gain),
    fits.Column(name='center_freq', format='D', array=center_freq),
    fits.Column(name='bandwidth', format='D', array=bandwidth),
    fits.Column(name='sample_rate', format='D', array=sample_rate),
    fits.Column(name='integration_time', format='K', array=integration_time),
    fits.Column(name='spectral_resolution', format='K', array=spectral_resolution),
])

tgt_config_hdu = fits.BinTableHDU.from_columns(cols_config, name='TARGET_CONFIG')

# --- TARGET_SCANS Table ---
# Example: 4 scans (2 targets × 2 frequency scans)
tgt_idx_scans = np.array([0,0,1,1], dtype=np.int64)
freq_scan = np.array([0,1,0,1], dtype=np.int64)
scan_iter = np.array([0,0,0,0], dtype=np.int64)
start_freq = np.array([1419041600, 1419710400, 1419041600, 1419710400], dtype=np.float64)
center_freq_scan = np.array([1420065600, 1420734400, 1420065600, 1420734400], dtype=np.float64)
end_freq = np.array([1421089600, 1421758400, 1421089600, 1421758400], dtype=np.float64)
gain_scan = np.array([23,23,23,23], dtype=np.int64)
status = np.array(['EMPTY','EMPTY','EMPTY','EMPTY'], dtype='S10')

cols_scans = fits.ColDefs([
    fits.Column(name='tgt_idx', format='K', array=tgt_idx_scans),
    fits.Column(name='freq_scan', format='K', array=freq_scan),
    fits.Column(name='scan_iter', format='K', array=scan_iter),
    fits.Column(name='start_freq', format='D', array=start_freq),
    fits.Column(name='center_freq', format='D', array=center_freq_scan),
    fits.Column(name='end_freq', format='D', array=end_freq),
    fits.Column(name='gain', format='K', array=gain_scan),
    fits.Column(name='status', format='10A', array=status),
])

tgt_scans_hdu = fits.BinTableHDU.from_columns(cols_scans, name='TARGET_SCANS')

# --- Write to FITS ---
hdulist = fits.HDUList([primary_hdu, tgt_config_hdu, tgt_scans_hdu])
hdulist.writeto('observation_full.fits', overwrite=True)

print("FITS file 'observation_full.fits' created with labeled tables!")
