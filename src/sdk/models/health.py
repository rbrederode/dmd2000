# -*- coding: utf-8 -*-
#
# Distributed under the terms of the BSD 3-clause new license.
# See LICENSE.txt for more info.
"""This module defines an enumerated type for health state."""

import enum


class HealthState(enum.IntEnum):
    """Python enumerated type for health state."""

    OK = 0
    """A device reports this state when there are no failures that are
    assessed as affecting the ability of the device to perform its
    function."""

    DEGRADED = 1
    """
    The device reports this state when only part of its functionality is
    available. This value is optional and shall be implemented only
    where it is useful.

    For example, a subarray may report its health state as ``DEGRADED``
    if one of the dishes that belongs to a subarray is unresponsive (or
    may report health state as ``FAILED``).

    Difference between ``DEGRADED`` and ``FAILED`` health state shall be
    clearly identified (quantified) and documented. For example, the
    difference between a ``DEGRADED`` and ``FAILED`` subarray might be
    defined as:

    * the number or percent of the dishes available;
    * the number or percent of the baselines available;
    * sensitivity

    or some other criterion. More than one criterion may be defined for
    a device.
    """

    FAILED = 2
    """
    The device reports this state when unable to perform core
    functionality and produce valid output.
    """

    UNKNOWN = 3
    """
    The device reports this state when unable to determine its health.

    This is also an initial state, indicating that health state has not
    yet been determined.
    """

if __name__ == "__main__":
    for state in HealthState:
        print(f"{state.name} = {state.value}")

    hs = HealthState['UNKNOWN']
    print(f"hs: {hs} ({hs.value}: {hs.name})")