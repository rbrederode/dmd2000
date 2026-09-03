from api import imu_app, protocol, sdp_dig, tm_dig, tm_dm, tm_sdp, tm_ws, ws_dm


def test_api_modules_share_standard_protocol_definitions():
    modules = (tm_ws, ws_dm, imu_app, tm_dig, tm_sdp, tm_dm, sdp_dig)

    for module in modules:
        assert module.MSG_TYPES is protocol.MSG_TYPES
        assert module.STATUS is protocol.STATUS
        assert module.ACTION_CODES[:len(protocol.ACTION_CODES)] == protocol.ACTION_CODES
        assert module.PROPERTIES[:len(protocol.PROPERTIES)] == protocol.PROPERTIES
        assert module.MSG_FIELDS["msg_type"]["enum"] is module.MSG_TYPES
        assert module.MSG_FIELDS["action_code"]["enum"] is module.ACTION_CODES
        for redundant_name in (
            "MSG_TYPE_REQ",
            "MSG_TYPE_ADV",
            "MSG_TYPE_RSP",
            "ACTION_CODE_GET",
            "ACTION_CODE_SET",
            "STATUS_SUCCESS",
            "STATUS_ERROR",
            "PROPERTY_TRACE",
            "PROPERTY_DEBUG",
            "PROPERTY_STATUS",
        ):
            assert redundant_name not in module.__dict__

    assert protocol.PROPERTY_STATUS in protocol.PROPERTIES


def test_api_modules_preserve_interface_specific_extensions():
    expected_property_extensions = {
        tm_ws: (),
        ws_dm: (ws_dm.PROPERTY_WEATHER,),
        imu_app: (imu_app.PROPERTY_IMU_DATA,),
        tm_dig: (
            tm_dig.PROPERTY_LOAD_ACTIVE,
            tm_dig.PROPERTY_CENTER_FREQ,
            tm_dig.PROPERTY_SAMPLE_RATE,
            tm_dig.PROPERTY_BANDWIDTH,
            tm_dig.PROPERTY_GAIN,
            tm_dig.PROPERTY_FREQ_CORRECTION,
            tm_dig.PROPERTY_SCANNING,
            tm_dig.PROPERTY_SDP_COMMS,
        ),
        tm_sdp: (
            tm_sdp.PROPERTY_SCAN_CONFIG,
            tm_sdp.PROPERTY_SCAN_COMPLETE,
            tm_sdp.PROPERTY_OBS_COMPLETE,
            tm_sdp.PROPERTY_OBS_RESET,
            tm_sdp.PROPERTY_SIGNAL_DISPLAY,
        ),
        tm_dm: (
            tm_dm.PROPERTY_TARGET,
            tm_dm.PROPERTY_CAPABILITY,
            tm_dm.PROPERTY_MODE,
        ),
        sdp_dig: (
            sdp_dig.PROPERTY_DIG_ID,
            sdp_dig.PROPERTY_LOAD,
            sdp_dig.PROPERTY_CENTER_FREQ,
            sdp_dig.PROPERTY_SAMPLE_RATE,
            sdp_dig.PROPERTY_BANDWIDTH,
            sdp_dig.PROPERTY_SDR_GAIN,
            sdp_dig.PROPERTY_CHANNELS,
            sdp_dig.PROPERTY_SCAN_DURATION,
            sdp_dig.PROPERTY_READ_COUNTER,
            sdp_dig.PROPERTY_READ_START,
            sdp_dig.PROPERTY_READ_END,
            sdp_dig.PROPERTY_SCANNING,
        ),
    }

    for module, extensions in expected_property_extensions.items():
        assert module.PROPERTIES == protocol.PROPERTIES + extensions
        if "property" in module.MSG_FIELDS:
            assert module.MSG_FIELDS["property"]["enum"] is module.PROPERTIES
        else:
            assert module.METADATA_FIELD["property"]["enum"] is module.PROPERTIES

    assert tm_dig.ACTION_CODES == protocol.ACTION_CODES + (tm_dig.ACTION_CODE_METHOD,)
    assert sdp_dig.ACTION_CODES == protocol.ACTION_CODES + (sdp_dig.ACTION_CODE_SAMPLES,)
