from datetime import datetime, timezone

from models.scan import ScanModel


def test_scan_model_serialises_short_target_pec_attribute_names():
    pec_update = datetime(2026, 8, 24, 10, 30, tzinfo=timezone.utc)
    scan = ScanModel(
        tgt_alt_pec_rms=0.12,
        tgt_az_pec_rms=0.34,
        tgt_pec_last_update=pec_update,
    )

    persisted = scan.to_dict()

    assert persisted["tgt_alt_pec_rms"] == 0.12
    assert persisted["tgt_az_pec_rms"] == 0.34
    assert persisted["tgt_pec_last_update"]["value"] == pec_update.isoformat()
    assert "target_alt_pec_rms" not in persisted
    assert "target_az_pec_rms" not in persisted
    assert "target_pec_last_update" not in persisted
