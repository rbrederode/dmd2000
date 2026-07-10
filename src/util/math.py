import math


def vector_or_none(value, length, source="IMU"):
    if value is None:
        return None
    if len(value) != length:
        raise ValueError(f"{source} error: Expected vector length {length}, got {len(value)}")
    if all(v is None for v in value):
        return None
    return [float(v) for v in value]


def adafruit_quaternion_to_wxyz(value):
    if value is None:
        return None
    if len(value) != 4:
        raise ValueError(f"BNO085Driver error: Expected quaternion length 4, got {len(value)}")

    quat_i, quat_j, quat_k, quat_real = value
    if all(v is None for v in value):
        return None

    return [float(quat_real), float(quat_i), float(quat_j), float(quat_k)]


def quaternion_to_euler(quaternion):
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
    if value is None:
        return None
    return [math.degrees(v) for v in value]


def float_or_none(value):
    return None if value is None else float(value)
