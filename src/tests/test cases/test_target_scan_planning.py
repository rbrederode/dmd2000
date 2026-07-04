from models.dsh import Feed
from models.target import TargetConfig, TargetScanSet


def test_single_frequency_scan_remains_centered_on_target_config():
    cfg = TargetConfig(
        obs_id="obs001",
        tgt_idx=0,
        feed_type=Feed.LF_400,
        gain=45,
        center_freq=412_000_000,
        bandwidth=1_000_000,
        sample_rate=6_000_000,
        integration_time=60,
        spectral_resolution=100,
    )
    scans = TargetScanSet()

    scans.determine_scans("obs001", cfg)

    assert scans.freq_scans == 1
    assert len(scans.scans) == 1
    assert scans.scans[0].center_freq == 412_000_000
    assert scans.scans[0].start_freq == 409_000_000
    assert scans.scans[0].end_freq == 415_000_000


def test_multi_frequency_scan_keeps_expanded_range_tiling():
    cfg = TargetConfig(
        obs_id="obs001",
        tgt_idx=0,
        feed_type=Feed.LF_400,
        gain=45,
        center_freq=412_000_000,
        bandwidth=10_000_000,
        sample_rate=6_000_000,
        integration_time=60,
        spectral_resolution=100,
    )
    scans = TargetScanSet()

    scans.determine_scans("obs001", cfg)

    assert scans.freq_scans > 1
    assert scans.scans[0].center_freq != cfg.center_freq
