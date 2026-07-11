import math

def vector_or_none(value, length, source="IMU"):
    """Convert a list of values to floats, or return None if the list is None or all values are None."""
    if value is None:
        return None
    if len(value) != length:
        raise ValueError(f"{source} error: Expected vector length {length}, got {len(value)}")
    if all(v is None for v in value):
        return None
    return [float(v) for v in value]

def adafruit_quaternion_to_wxyz(value):
    """Convert a quaternion from Adafruit format (i, j, k, real) to w, x, y, z format."""
    if value is None:
        return None
    if len(value) != 4:
        raise ValueError(f"BNO085Driver error: Expected quaternion length 4, got {len(value)}")

    quat_i, quat_j, quat_k, quat_real = value
    if all(v is None for v in value):
        return None

    return [float(quat_real), float(quat_i), float(quat_j), float(quat_k)]

def quaternion_to_euler(quaternion):
    """Convert a quaternion (w, x, y, z) to Euler angles (roll, pitch, yaw) in degrees."""
    if quaternion is None:
        return None

    w, x, y, z = quaternion

    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.degrees(math.copysign(math.pi / 2.0, sinp))
    else:
        pitch = math.degrees(math.asin(sinp))

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    return [roll, pitch, yaw]

def radians_to_degrees(value):
    """Convert a list of values from radians to degrees, or return None if the list is None."""
    if value is None:
        return None
    return [math.degrees(v) for v in value]


def float_or_none(value):
    """Convert a value to float, or return None if the value is None."""
    return None if value is None else float(value)


def yaw_to_azimuth(yaw, az_offset=0.0, flip_az=False):
    """Convert yaw angle to azimuth angle."""
    if yaw is None:
        return None

    az_offset = 0.0 if az_offset is None else az_offset % 360.0

    if yaw > 180.0 or yaw < -180.0:
        yaw = (yaw % 180.0) - 180.0

    azimuth = 360.0 - yaw if yaw > 0.0 else -yaw
    azimuth += az_offset

    return (azimuth + 180.0) % 360.0 if flip_az else azimuth % 360.0


def angle_to_altitude(angle, alt_offset=0.0):
    """Convert pitch or roll angle to altitude angle."""
    if angle is None:
        return None

    if alt_offset is None:
        alt_offset = 0.0

    alt_offset = float(alt_offset)
    if not -90.0 <= alt_offset <= 90.0:
        raise ValueError(f"Altitude offset must be between -90 and 90 degrees. Offset provided: {alt_offset}")

    angle += alt_offset

    if angle > 180.0 or angle < -180.0:
        angle = (angle % 180.0) - 180.0

    flip_az = False

    if angle > 90.0:
        angle = 180.0 - angle
        flip_az = True
    elif angle < -90.0:
        angle = -180.0 - angle
        flip_az = True

    return max(-90.0, min(90.0, angle)), flip_az
