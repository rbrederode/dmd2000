from astropy.io import fits
import numpy as np
import json

def observation_to_fits_hdulist(observation):
	"""
	Convert an Observation instance to a FITS HDUList object.
	Table extensions: TARGETS, TARGET_CONFIG, TARGET_SCANS
	"""
	# --- Primary HDU Header ---
	hdr = fits.Header()
	hdr['OBS_ID'] = getattr(observation, 'obs_id', '')
	hdr['TITLE'] = getattr(observation, 'title', '')
	hdr['DISH_ID'] = getattr(observation, 'dsh_id', '')
	hdr['DIAMETER'] = getattr(observation, 'diameter', 0.0)
	hdr['FD_RATIO'] = getattr(observation, 'f/d_ratio', 0.0)
	hdr['LATITUDE'] = getattr(observation, 'latitude', 0.0)
	hdr['LONGITUD'] = getattr(observation, 'longitude', 0.0)
	hdr['EMAIL'] = getattr(observation, 'user_email', '')
	hdr['DESC'] = getattr(observation, 'description', '')
	hdr['CAPABILI'] = getattr(observation, 'capabilities', '')
	hdr['INT_TIME'] = getattr(observation, 'total_integration_time', 0.0)
	hdr['SLEWTIME'] = getattr(observation, 'estimated_slewing_time', 0.0)
	hdr['DURATION'] = getattr(observation, 'estimated_observation_duration', '')
	hdr['CREATED'] = str(getattr(observation, 'created', ''))
	hdr['START_DT'] = str(getattr(observation, 'start_dt', ''))
	hdr['END_DT'] = str(getattr(observation, 'end_dt', ''))
	hdr['LASTUPDT'] = str(getattr(observation, 'last_update', ''))

	primary_hdu = fits.PrimaryHDU(header=hdr)

	# --- TARGETS Table ---
	targets = observation.targets
	tgt_idx = np.array([getattr(t, 'tgt_idx', -1) for t in targets], dtype=np.int64)
	tgt_id = np.array([str(getattr(t, 'id', '')) for t in targets], dtype='S32')
	pointing = np.array([getattr(t, 'pointing').name if hasattr(getattr(t, 'pointing', None), 'name') else str(getattr(t, 'pointing', '')) for t in targets], dtype='S32')
	sky_coord = np.array([str(getattr(t, 'sky_coord', '')) for t in targets], dtype='S64')
	altaz = np.array([str(getattr(t, 'altaz', '')) for t in targets], dtype='S64')
	scan_type = np.array([type(getattr(t, 'scan', None)).__name__ if getattr(t, 'scan', None) else '' for t in targets], dtype='S16')
	scan_params = np.array([
		json.dumps(getattr(t, 'scan').to_dict()) if getattr(t, 'scan', None) and hasattr(getattr(t, 'scan'), 'to_dict') else str(getattr(t, 'scan', ''))
		for t in targets
	], dtype='S256')

	cols_targets = fits.ColDefs([
		fits.Column(name='tgt_idx', format='K', array=tgt_idx),
		fits.Column(name='id', format='32A', array=tgt_id),
		fits.Column(name='pointing', format='32A', array=pointing),
		fits.Column(name='sky_coord', format='64A', array=sky_coord),
		fits.Column(name='altaz', format='64A', array=altaz),
		fits.Column(name='scan_type', format='16A', array=scan_type),
		fits.Column(name='scan_params', format='256A', array=scan_params),
	])
	tgt_hdu = fits.BinTableHDU.from_columns(cols_targets, name='TARGETS')

	# --- TARGET_CONFIG Table ---
	configs = observation.target_configs
	tgt_idx_cfg = np.array([getattr(c, 'tgt_idx', -1) for c in configs], dtype=np.int64)
	feed = np.array([
		getattr(c, 'feed').name if hasattr(getattr(c, 'feed', None), 'name') else str(getattr(c, 'feed', ''))
		for c in configs
	], dtype='S20')
	gain = np.array([0.0 if isinstance(getattr(c, 'gain', 0.0), str) else getattr(c, 'gain', 0.0) for c in configs], dtype=np.float64)
	center_freq = np.array([getattr(c, 'center_freq', 0.0) for c in configs], dtype=np.float64)
	bandwidth = np.array([getattr(c, 'bandwidth', 0.0) for c in configs], dtype=np.float64)
	sample_rate = np.array([getattr(c, 'sample_rate', 0.0) for c in configs], dtype=np.float64)
	integration_time = np.array([getattr(c, 'integration_time', 0.0) for c in configs], dtype=np.float64)
	spectral_resolution = np.array([getattr(c, 'spectral_resolution', 0) for c in configs], dtype=np.int64)

	cols_config = fits.ColDefs([
		fits.Column(name='tgt_idx', format='K', array=tgt_idx_cfg),
		fits.Column(name='feed', format='20A', array=feed),
		fits.Column(name='gain', format='D', array=gain),
		fits.Column(name='center_freq', format='D', array=center_freq),
		fits.Column(name='bandwidth', format='D', array=bandwidth),
		fits.Column(name='sample_rate', format='D', array=sample_rate),
		fits.Column(name='integration_time', format='D', array=integration_time),
		fits.Column(name='spectral_resolution', format='K', array=spectral_resolution),
	])
	tgt_config_hdu = fits.BinTableHDU.from_columns(cols_config, name='TARGET_CONFIG')

	# --- TARGET_SCANS Table ---
	scans = []
	for scan_set in observation.target_scans:
		scans.extend(getattr(scan_set, 'scans', []))
	tgt_idx_scans = np.array([getattr(s, 'tgt_idx', -1) for s in scans], dtype=np.int64)
	freq_scan = np.array([getattr(s, 'freq_scan', -1) for s in scans], dtype=np.int64)
	scan_iter = np.array([getattr(s, 'scan_iter', -1) for s in scans], dtype=np.int64)
	start_freq = np.array([getattr(s, 'start_freq', 0.0) for s in scans], dtype=np.float64)
	center_freq_scan = np.array([getattr(s, 'center_freq', 0.0) for s in scans], dtype=np.float64)
	end_freq = np.array([getattr(s, 'end_freq', 0.0) for s in scans], dtype=np.float64)
	gain_scan = np.array([getattr(s, 'gain', 0.0) for s in scans], dtype=np.float64)
	status = np.array([
		getattr(s, 'status').name if hasattr(getattr(s, 'status', None), 'name') else str(getattr(s, 'status', ''))
		for s in scans
	], dtype='S16')

	cols_scans = fits.ColDefs([
		fits.Column(name='tgt_idx', format='K', array=tgt_idx_scans),
		fits.Column(name='freq_scan', format='K', array=freq_scan),
		fits.Column(name='scan_iter', format='K', array=scan_iter),
		fits.Column(name='start_freq', format='D', array=start_freq),
		fits.Column(name='center_freq', format='D', array=center_freq_scan),
		fits.Column(name='end_freq', format='D', array=end_freq),
		fits.Column(name='gain', format='D', array=gain_scan),
		fits.Column(name='status', format='16A', array=status),
	])
	tgt_scans_hdu = fits.BinTableHDU.from_columns(cols_scans, name='TARGET_SCANS')

	# --- Compose HDUList ---
	hdulist = fits.HDUList([primary_hdu, tgt_hdu, tgt_config_hdu, tgt_scans_hdu])
	return hdulist
