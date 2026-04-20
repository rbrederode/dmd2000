from datetime import datetime, timezone

from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan


def test_find_equiv_scan_creates_missing_input_dir(tmp_path):
    missing_dir = tmp_path / "samples"

    scan = Scan(
        scan_model=ScanModel(
            obs_id="obs001",
            tgt_idx=0,
            freq_scan=0,
            scan_type=ScanType.SKY,
            dig_id="dig001",
            created=datetime.now(timezone.utc),
            start_idx=0,
            duration=60,
            sample_rate=2.4e6,
            channels=1024,
            center_freq=1420.4e6,
            gain=10.0,
            load=False,
            status=ScanState.COMPLETE,
            last_update=datetime.now(timezone.utc),
        )
    )

    result = scan.find_equiv_scan(input_dir=str(missing_dir), scan_type=ScanType.LOAD)

    assert result is None
    assert missing_dir.exists()
    assert missing_dir.is_dir()
