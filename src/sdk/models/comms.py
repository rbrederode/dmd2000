
"""This module defines an enumerated type for communication status."""

import enum

class InterfaceType(enum.IntEnum):
    """The type of interface from the perspective of an application.
       For example, the digitiser app is an ENTITY with respect to the TM.
       The TM is an ENTITY_DRIVER with respect to the digitiser app.
       The SDP is an APP_APP interface with respect to the TM.
    """

    UNKNOWN = -1
    """Unknown interface type."""

    ENTITY_DRIVER = 0
    """An entity driving interface will connect to multiple entities e.g. dishes or digitisers."""

    ENTITY = 1
    """An entity interface connects to an entity driving interface e.g. a dish or digitiser."""

    APP_APP = 2
    """An app_app interface connects to another application e.g. TM to SDP."""

class CommunicationStatus(enum.IntEnum):
    """The status of communication with the system under controk."""

    DISABLED = 0
    """
    Communication is disabled.

    The control system is not trying to establish/maintain a channel of
    communication with the system under control. For example:

    * if communication with the system under control is
      connection-oriented, then there is no connection, and the control
      system is not trying to establish a connection.
    * if communication is by event subscription, then the control system
      is unsubscribed from events.
    * if communication is by polling, then the control system is not
      performing that polling.
    """

    NOT_ESTABLISHED = 1
    """
    Communication is sought but not established.

    The control system is trying to establish/maintain a channel of
    communication with the system under control, but that channel is not
    currently established. For example:

    * if communication with the system under control is
      connection-oriented, then the control system has not yet succeeded
      in establishing the connection, or the connection has been broken.
    """

    ESTABLISHED = 2
    """
    The control system has established a channel of communication with
    the system under control. For example:

    * if communication with the system under control is
      connection-oriented, then the control system has connected to the
      system under control.
    * if communication is by polling, then the control system is polling
      the system under control, and the system under control is
      responsive.
    """
