from astropy.coordinates import EarthLocation, AltAz, SkyCoord
from astropy.coordinates import get_body
from astropy.time import Time
import astropy.units as u

from datetime import datetime, timezone
import logging
import numpy as np
from typing import Tuple
import threading

from models.dsh import DishModel, DriverType, PointingState, Capability, DishMode, Feed, PECModel
from models.target import TargetModel, PointingType
from models.health import HealthState
from util.xbase import XInvalidTransition, XSoftwareFailure, XStreamUnableToExtract, XCommsFailure

logger = logging.getLogger(__name__)

class DishDriver:

    MAX_HISTORY = 1000  # Store last X AltAz, PEC and mode/state readings

    def __init__(self, dsh_model: DishModel=None):
        if dsh_model is None:
            raise ValueError("Driver DishModel cannot be None during initialisation.")

        # Lock for thread-safe allocation of shared resources
        self._rlock = threading.RLock()  

        self.dsh_model = dsh_model      
        self.location = EarthLocation(lat=self.dsh_model.latitude*u.deg, lon=self.dsh_model.longitude*u.deg, height=self.dsh_model.height*u.m)

        # History of pointing and desired AltAz for plotting
        self.pointing_altaz_hist = None
        self.desired_altaz_hist = None
        
         # Periodic Error Correction (PEC) history for altitude and azimuth
        self.pec_hist = None

    ##############################################################################
    # Public Interface Methods
    ##############################################################################
    
    def get_location(self) -> EarthLocation:
        return self.location

    def get_poll_interval_ms(self) -> int:
        """ Get the polling interval in milliseconds.
            Used by the dish manager to check the driver current altaz.
            :return: The polling interval in milliseconds.
        """
        return self.dsh_model.driver_poll_period

    def get_failure_count(self) -> int:
        """ Get the current consecutive driver failures to return its altaz.
            :return: The current failure count.
        """
        return self.dsh_model.driver_failures

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application.
        """
        threshold = 60000 / self.get_poll_interval_ms()
        failure_count = self.get_failure_count()

        old_health = self.dsh_model.health

        if failure_count > 0 and failure_count < 10:
            message = f"DishDriver health status set to DEGRADED: Detected sporadic failures {failure_count} communicating with Dish {self.dsh_model.dsh_id}." + \
                      f" Consider investigating dish driver."
            logger.error(self.dsh_model.set_last_err(message))
            self.dsh_model.health = HealthState.DEGRADED
            
        elif failure_count >= 10 and failure_count < threshold:
            message = f"DishDriver health status set to DEGRADED: Detected persistent failures {failure_count} communicating with Dish {self.dsh_model.dsh_id}." + \
                      f" Consider investigating dish driver."
            logger.error(self.dsh_model.set_last_err(message))
            self.dsh_model.health = HealthState.DEGRADED  

        elif failure_count >= threshold:
            message = f"DishDriver health status set to FAILED: Detected unacceptably high failures {failure_count} communicating with Dish {self.dsh_model.dsh_id}." + \
                      f" Consider investigating dish driver."
            logger.error(self.dsh_model.set_last_err(message))
            self.dsh_model.health = HealthState.FAILED

        elif failure_count == 0:
            self.dsh_model.health = HealthState.OK

        if old_health != self.dsh_model.health:
            self.dsh_model.last_update = datetime.now(timezone.utc)

        return self.dsh_model.health

    def get_rotation_speed(self) -> float:
        """ Get the rotation speed of the dish from the subclass implementation.
            :return: The rotation speed in degrees per second.
        """
        # Delegate to subclass implementation
        with self._rlock:
            return self._get_rotation_speed()

    def get_min_max_alt(self) -> Tuple[float, float]:
        """ Get the minimum and maximum altitude limits of the dish from the subclass implementation.
            :return: A tuple of (min_altitude, max_altitude) in degrees.
        """
        # Delegate to subclass implementation
        with self._rlock:
            return self._get_min_max_alt()

    def get_resolution(self) -> float:
        """ Get the resolution of the dish from the subclass implementation.
            :return: The resolution in degrees per step.
        """
        # Delegate to subclass implementation
        with self._rlock:
            return self._get_resolution()

    def get_target_tuple(self) -> (str, 'TargetModel'):
        """ Get the current target of the dish from the DishModel.
            :return: The current target id and target model.
        """
        return self.dsh_model.tgt_id, self.dsh_model.target

    def get_stow_altaz(self) -> Tuple[float, float]:
        """ Get the stow Alt Az position of the dish from the subclass implementation.
            :return: The stow Alt Az position as a tuple of (altitude, azimuth).
        """
        # Delegate to subclass implementation
        with self._rlock:
            return self._get_stow_altaz()

    def get_mode(self) -> DishMode:
        """ Get the current dish mode from the DishModel.
            :return: The current dish mode.
        """
        return self.dsh_model.mode

    def get_capability(self) -> Capability:
        """ Get the current dish capability from the DishModel.
            :return: The current capability.
        """
        return self.dsh_model.capability

    def get_pointing_state(self) -> PointingState:
        """ Get the current pointing state from the DishModel.
            :return: The current pointing state.
        """
        return self.dsh_model.pointing_state

    def get_previous_altaz(self) -> AltAz:
        """ Get the previous AltAz pointing of the dish from the DishModel.
            :return: The previous AltAz pointing of the dish.
        """
        if self.dsh_model.pointing_altaz is None:
            return None

        alt = self.dsh_model.pointing_altaz.get("alt", None)
        az = self.dsh_model.pointing_altaz.get("az", None)

        if alt is None or az is None:
            return None

        now = Time(datetime.now(timezone.utc))
        altaz = AltAz(obstime=now, location=self.location, alt=alt*u.deg, az=az*u.deg)
        return altaz

    def get_desired_altaz(self) -> AltAz:
        """ Get the desired AltAz pointing of the dish from the DishModel.
            :return: The desired AltAz pointing of the dish.
        """
        if self.dsh_model.desired_altaz is None:
            return None

        alt = self.dsh_model.desired_altaz.get("alt", None)
        az = self.dsh_model.desired_altaz.get("az", None)

        if alt is None or az is None:
            return None

        now = Time(datetime.now(timezone.utc))
        altaz = AltAz(obstime=now, location=self.location, alt=alt*u.deg, az=az*u.deg)
        return altaz

    def get_current_pec(self) -> Tuple[float, float]:
        """ Get the current periodic error correction (PEC) for the dish.
            Calculated as the difference between the current pointing AltAz and the desired AltAz.
            :return: The current PEC as a tuple of (altitude PEC, azimuth PEC) in degrees.
        """
        if self.dsh_model.pointing_altaz is None or self.dsh_model.desired_altaz is None:
            return None, None

        pointing_alt = self.dsh_model.pointing_altaz.get("alt", None)
        pointing_az = self.dsh_model.pointing_altaz.get("az", None)

        desired_alt = self.dsh_model.desired_altaz.get("alt", None)
        desired_az = self.dsh_model.desired_altaz.get("az", None)

        if pointing_alt is None or pointing_az is None:
            return None, None

        if desired_alt is None or desired_az is None:
            return None, None

        # PEC is the difference between where we are pointing and where we want to be pointing
        alt_pec = desired_alt - pointing_alt
        az_pec = desired_az - pointing_az

        return alt_pec, az_pec

    def get_rms_pec(self) -> Tuple[float, float]:
        """ Calculate the RMS of the PEC history for altitude and azimuth.
            :return: A tuple of (altitude PEC RMS, azimuth PEC RMS) in degrees.
        """
        with self._rlock:
            # ':' selects all rows ', 0' selects col1 (timestamp) ', 1' selects col2 (alt_pec) ', 2' selects col3 (az_pec)
            alt_pec = self.pec_hist[self.pec_hist[:, 0] > 0, 1] 
            az_pec = self.pec_hist[self.pec_hist[:, 0] > 0, 2]

        alt_pec_rms = np.sqrt(np.mean(np.square(alt_pec - np.mean(alt_pec)))) if alt_pec.size > 0 else 0
        az_pec_rms = np.sqrt(np.mean(np.square(az_pec - np.mean(az_pec)))) if az_pec.size > 0 else 0

        return alt_pec_rms, az_pec_rms

    def get_current_altaz(self) -> AltAz:
        """
            Get the current AltAz pointing of the dish.
            The Dish Manager polls this method every driver_poll_period ms to update the DishModel.
            :return: Current AltAz pointing of the dish.
        """
        # Delegate to subclass implementation
        try:
            with self._rlock:
                alt, az = self._get_current_altaz()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}"
                logger.error(self.dsh_model.set_last_err(message))
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to get current AltAz: {e}\n{self.dsh_model.to_dict()}"
                logger.exception(self.dsh_model.set_last_err(message))
            self.dsh_model.increment_failures()
            raise e

        if alt is None or az is None:
            self.dsh_model.increment_failures()
            message = f"DishDriver {self.dsh_model.dsh_id} returned invalid (None) AltAz data.\n{self.dsh_model.to_dict()}"
            raise ValueError(self.dsh_model.set_last_err(message))
        else:
            self.update_pointing_hist() # Update pointing history with the latest AltAz 
            if self.get_failure_count() > 0:
                logger.info(f"DishDriver {self.dsh_model.dsh_id} reset failure count {self.get_failure_count()} to 0 after successful AltAz read.")
                self.dsh_model.reset_failures()

        now = Time(datetime.now(timezone.utc))
        altaz = AltAz(obstime=now, location=self.location, alt=alt*u.deg, az=az*u.deg)

        previous_alt = self.dsh_model.pointing_altaz.get("alt", None) if self.dsh_model.pointing_altaz else None
        previous_az = self.dsh_model.pointing_altaz.get("az", None) if self.dsh_model.pointing_altaz else None

        # If first time reading AltAz, just update the dish model
        if previous_alt is None or previous_az is None:
            self.dsh_model.pointing_altaz = {"alt": altaz.alt.degree, "az": altaz.az.degree}
            self.dsh_model.velocity_altaz = {"alt": 0.0, "az": 0.0} # No velocity on first reading
            self.dsh_model.last_update = datetime.now(timezone.utc)

        # If current altaz does not match previous altaz i.e. dish has moved
        elif alt != previous_alt or az != previous_az:

            logger.info(f"DishDriver {self.dsh_model.dsh_id} is pointing at AltAz " + \
                f"(Alt: {alt}, Az: {az}) in pointing state: {self.dsh_model.pointing_state.name}, dish mode: {self.dsh_model.mode.name}.")

            # Update the dish model with the current pointing altaz 
            self.dsh_model.pointing_altaz = {"alt": altaz.alt.degree, "az": altaz.az.degree}
            self.dsh_model.velocity_altaz = {"alt": alt - previous_alt, "az": az - previous_az}
            self.dsh_model.last_update = datetime.now(timezone.utc)

            # If we were NOT expecting the dish to be moving, log an error
            if self.dsh_model.pointing_state not in [PointingState.SLEW, PointingState.TRACK, PointingState.SCAN, PointingState.UNKNOWN]:

                message = f"DishDriver {self.dsh_model.dsh_id} has moved from AltAz " + \
                    f"(Alt: {previous_alt}, Az: {previous_az}) to AltAz (Alt: {alt}, Az: {az}) " + \
                    f"while in {self.dsh_model.pointing_state.name} state. Dish is not expected to be moving !\n{self.dsh_model.to_dict()}"
                logger.error(self.dsh_model.set_last_err(message))

        # Else if Dish has not moved noticeably (TRACKING is slow and hard to notice)
        elif alt == previous_alt and az == previous_az: 

            self.dsh_model.velocity_altaz = {"alt": 0.0, "az": 0.0}

            # If we were expecting the dish to be stationary i.e. READY
            if self.dsh_model.pointing_state == PointingState.READY:
                pass # Dish seems stationary as expected

            # Else if we were expecting the dish to be tracking, scanning or slewing to a desired AltAz
            elif self.dsh_model.pointing_state in [PointingState.TRACK, PointingState.SLEW, PointingState.SCAN]:

                logger.info(f"DishDriver {self.dsh_model.dsh_id} is pointing at AltAz " + \
                f"(Alt: {alt}, Az: {az}) in pointing state: {self.dsh_model.pointing_state.name}, dish mode: {self.dsh_model.mode.name}.")

                desired_alt = self.dsh_model.desired_altaz.get("alt", None) if self.dsh_model.desired_altaz else None
                desired_az = self.dsh_model.desired_altaz.get("az", None) if self.dsh_model.desired_altaz else None

                if desired_alt is None or desired_az is None:
                    message = f"DishDriver {self.dsh_model.dsh_id} is in TRACK/SLEW/SCAN pointing state but no desired AltAz is set in DishModel.\n{self.dsh_model.to_dict()}"
                    raise XSoftwareFailure(self.dsh_model.set_last_err(message))

                # If the dish pointing AltAz is within resolution of the desired AltAz
                if abs(alt - desired_alt) <= self.get_resolution() and abs(az - desired_az) <= self.get_resolution():

                    # Transition from SLEW to READY or stay in original pointing state
                    self.dsh_model.pointing_state = PointingState.READY if self.dsh_model.pointing_state == PointingState.SLEW else self.dsh_model.pointing_state
                    self.dsh_model.last_update = datetime.now(timezone.utc)
                
                # If in TRACK, Dish has drifted off target, not a major issue as tracking can catch up on the next movement
                elif self.dsh_model.pointing_state == PointingState.TRACK:
                    
                    message = f"DishDriver {self.dsh_model.dsh_id} has drifted off target while TRACKING. " + \
                        f"Current AltAz (Alt: {alt}, Az: {az}) is not within resolution of desired AltAz (Alt: {desired_alt}, Az: {desired_az}).\n{self.dsh_model.to_dict()}"
                    logger.warning(self.dsh_model.set_last_err(message))
                
                # If in SLEW, major issue, dish should have reached target AltAz, but stopped prematurely, set pointing state to UNKNOWN
                elif self.dsh_model.pointing_state == PointingState.SLEW:
                
                    message = f"DishDriver {self.dsh_model.dsh_id} has prematurely stopped moving while SLEWing. " + \
                        f"Transition to UNKNOWN pointing state.\n{self.dsh_model.to_dict()}"
                    logger.warning(self.dsh_model.set_last_err(message))

                    self.dsh_model.pointing_state = PointingState.UNKNOWN
                    self.dsh_model.last_update = datetime.now(timezone.utc)

        return altaz

    def set_dish_mode(self, mode: DishMode):
        """ Set the current dish mode in the DishModel.
            :raises NotImplementedError: If a required method is not implemented by a subclass
        """
        if not isinstance(mode, DishMode):
            message = f"DishDriver set_mode requires a valid DishMode enumeration value, got {mode} of type {type(mode)}."
            raise ValueError(self.dsh_model.set_last_err(message))

        if self.dsh_model.capability in [Capability.UNAVAILABLE, Capability.UNKNOWN]:
            message = f"DishDriver {self.dsh_model.dsh_id} cannot set mode when capability unavailable or unknown.\n{self.dsh_model.to_dict()}"
            raise XInvalidTransition(self.dsh_model.set_last_err(message))
        
        logger.info(f"DishDriver {self.dsh_model.dsh_id} changing mode from {self.dsh_model.mode.name} to {mode.name}.")

        if mode == DishMode.STARTUP:
            self.set_startup_mode()
        elif mode == DishMode.STANDBY_FP:
            self.set_standby_fp_mode()
        elif mode == DishMode.SHUTDOWN:
            self.set_shutdown_mode()
        elif mode == DishMode.STOW:
            self.set_stow_mode()
        elif mode == DishMode.MAINTENANCE:
            self.set_maintenance_mode()
        elif mode == DishMode.CONFIG:

            # Ensure we have the capability to enter CONFIG mode
            if self.dsh_model.capability not in [Capability.CONFIGURING, Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL]:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot set CONFIG mode when capability not available.\n{self.dsh_model.to_dict()}"
                raise XInvalidTransition(self.dsh_model.set_last_err(message))
            self.set_config_mode()

        elif mode == DishMode.OPERATE:

            # Ensure we have the capability to enter OPERATE mode
            if self.dsh_model.capability not in [Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL]:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot set OPERATE mode when capability not operational.\n{self.dsh_model.to_dict()}"
                raise XInvalidTransition(self.dsh_model.set_last_err(message))
            self.set_operate_mode()

    def get_weather_alarm(self) -> bool:
        """ Get the current weather alarm status from the DishModel.
            :return: True if the weather alarm is on, False if it is off.
        """
        return self.dsh_model.weather_alarm
    
    def set_weather_alarm(self, alarm_on: bool):
        """ Set the weather alarm status in the DishModel.
            :param alarm_on: True if the weather alarm is on, False if it is off.
        """
        self.dsh_model.weather_alarm = alarm_on
        message = f"Weather alarm triggered in mode {self.get_mode().name}." if alarm_on else "Weather alarm cleared."
        self.dsh_model.set_last_err(message)

    def clear_target_tuple(self):
        """ Clear the target of the dish in the DishModel.
        """
        self.reset_pec_hist() # Reset PEC history when clearing target

        self.dsh_model.tgt_id = None
        self.dsh_model.target = None
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_target_tuple(self, tgt_id: str, target: 'TargetModel'):
        """ Set the target of the dish in the DishModel. 
            Stop the dish if necessary, calculate the desired AltAz and slew to it.
            :param tgt_id: The unique target identifier.
            :param target: The target model.
        """
        if self.dsh_model.mode != DishMode.CONFIG:
            message = f"DishDriver set_target_tuple requires Dish to be in CONFIG mode.\n{self.dsh_model.to_dict()}"
            raise XInvalidTransition(self.dsh_model.set_last_err(message))

        if tgt_id is None or target is None or not isinstance(target, TargetModel):
            message = f"DishDriver set_target_tuple requires a valid TargetModel and tgt_id to be provided.\n{self.dsh_model.to_dict()}"
            raise ValueError(self.dsh_model.set_last_err(message))

        # Check if the obs_id has changed i.e. we are starting a new observation
        new_obs_id = tgt_id.split("_")[0] if "_" in tgt_id else None
        cur_obs_id = self.dsh_model.tgt_id.split("_")[0] if self.dsh_model.tgt_id and "_" in self.dsh_model.tgt_id else None
            
        # Clear PEC rms associated with targets in the old observation
        self.dsh_model.tgt_pec = [] if new_obs_id != cur_obs_id else self.dsh_model.tgt_pec
        self.reset_pec_hist() # Reset PEC history for new target

        self.dsh_model.tgt_id = tgt_id
        self.dsh_model.target = target
        self.dsh_model.last_update = datetime.now(timezone.utc)

        if self.dsh_model.pointing_state != PointingState.READY:
            self.stop()

    def set_desired_altaz(self, altaz: AltAz):
        """ Set the desired AltAz pointing of the dish in the DishModel.
            :param altaz: The desired AltAz pointing of the dish.
        """
        self.dsh_model.desired_altaz = None if altaz is None else {"alt": altaz.alt.degree,"az": altaz.az.degree}
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_dish_capability(self, capability: Capability):
        """ Set the current capability in the DishModel.
            :param capability: The new capability.
        """
        if not isinstance(capability, Capability):
            message = "DishDriver set_dish_capability requires a valid Capability enumeration value."
            raise ValueError(self.dsh_model.set_last_err(message))

        # If we are currently operational 
        if self.dsh_model.capability in [Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL]:

            # And we are transitioning to a non-operational mode, stow the dish first
            if capability not in [Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL]:

                # Set new capability in dish model before stowing to ensure it is set
                self.dsh_model.capability = capability
                self.dsh_model.last_update = datetime.now(timezone.utc)
                self.set_stow_mode()
                return 
        
        self.dsh_model.capability = capability
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_startup_mode(self):
        """
            Sets the dish to startup mode and tests the connection to the dish controller.
            If successful, the dish mode is set to STANDBY_FP.
            Failures during startup will result in exceptions being raised.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        self.dsh_model.mode = DishMode.STARTUP
        self.dsh_model.pointing_state = PointingState.UNKNOWN
        self.dsh_model.last_update = datetime.now(timezone.utc)

        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._set_startup_mode()
                
            self.stop() # Ensures dish controller is responsive
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}"
                logger.error(self.dsh_model.set_last_err(message))
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set startup mode: {e}\n{self.dsh_model.to_dict()}"
                logger.exception(self.dsh_model.set_last_err(message))
                
            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            raise e

        self.dsh_model.mode = DishMode.STANDBY_FP
        return

    def set_standby_fp_mode(self):
        """
            Sets the dish to standby full power mode. 
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        try:
            self.stop() # Ensure dish is not moving
            with self._rlock:
                self._set_standby_fp_mode()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}"
                logger.error(self.dsh_model.set_last_err(message))
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set standby full power mode: {e}\n{self.dsh_model.to_dict()}"
                logger.exception(self.dsh_model.set_last_err(message))

            self.dsh_model.increment_failures() 
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.READY
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to STANDBY_FP
        # We stopped the dish, so pointing state is READY
        self.dsh_model.mode = DishMode.STANDBY_FP
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_shutdown_mode(self):
        """
            Sets the dish to shutdown mode for planned power loss or by UPS trigger. 
            After successful shutdown, the dish mode is set to SHUTDOWN.
            Failures during shutdown will result in exceptions being raised.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        # First instruct the dish to stow
        self.set_stow_mode()

        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._set_shutdown_mode()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set shutdown mode: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to SHUTDOWN
        self.dsh_model.mode = DishMode.SHUTDOWN
        self.dsh_model.pointing_state = PointingState.UNKNOWN
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_stow_mode(self):
        """
            Sets the dish to stow mode. 
            Failures during stow will result in exceptions being raised.
            Transitions to UNKNOWN mode on failure.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        # Clear any existing target model and unique target identifier from the dish
        self.clear_target_tuple()

        # Ensure dish stops moving before stowing
        self.stop()

        # Get the stow AltAz from the subclass implementation
        stow_alt, stow_az = self._get_stow_altaz()

        # Get current pointing AltAz from dish model
        az =  self.dsh_model.pointing_altaz.get("az", None) if self.dsh_model.pointing_altaz else None
        alt = self.dsh_model.pointing_altaz.get("alt", None) if self.dsh_model.pointing_altaz else None

        if self.get_weather_alarm():
            message = f"DishDriver {self.dsh_model.dsh_id} weather alarm is ON. Stowing from {alt}, {az} in altitude only to avoid potential damage from high winds."
            logger.warning(self.dsh_model.set_last_err(message))
            stow_az = az if az is not None else stow_az

        stow_altaz = AltAz(
            obstime=Time(datetime.now(timezone.utc)), 
            location=self.location, alt=stow_alt*u.deg, az=stow_az*u.deg)   
        
        # Update the desired AltAz in the dish model
        self.set_desired_altaz(stow_altaz)

        # If the dish is already at the stow position, set mode to STOW and return
        if alt is None or az is None:
            message = f"DishDriver {self.dsh_model.dsh_id} has no pointing AltAz data."
            logger.warning(self.dsh_model.set_last_err(message))
        elif abs(alt - stow_alt) <= self.get_resolution() and abs(az - stow_az) <= self.get_resolution():
            self.dsh_model.mode = DishMode.STOW
            self.dsh_model.pointing_state = PointingState.READY
            self.dsh_model.last_update = datetime.now(timezone.utc)
            return

        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._set_stow_mode(alt=stow_alt, az=stow_az)
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set stow mode: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to STOW
        self.dsh_model.mode = DishMode.STOW
        self.dsh_model.pointing_state = PointingState.SLEW
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_maintenance_mode(self):
        """
            Sets the dish to maintenance mode. 
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        # First instruct the dish to stow
        self.set_stow_mode()

        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._set_maintenance_mode()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set maintenance mode: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to MAINTENANCE
        self.dsh_model.mode = DishMode.MAINTENANCE
        self.dsh_model.pointing_state = PointingState.UNKNOWN
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_config_mode(self):
        """
            Sets the dish to config mode. 
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        try:
            with self._rlock:
                    self._set_config_mode()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set config mode: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to CONFIG
        self.dsh_model.mode = DishMode.CONFIG
        self.dsh_model.pointing_state = PointingState.UNKNOWN
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def set_operate_mode(self):
        """
            Sets the dish to operate mode. 
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        try:
            with self._rlock:
                self._set_operate_mode()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to set operate mode: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish mode to OPERATE, pointing state to READY
        self.dsh_model.mode = DishMode.OPERATE
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

        # Get the current target from the dish model
        target_id, target = self.get_target_tuple()

        if target is not None:
            # Calculate the desired AltAz for the new target and slew the dish to it
            altaz = self.get_desired_altaz(target=target)
            self.slew(altaz=altaz)

    def track(self):
        """
            Track the drivers current target if states and modes permit. Delegates to subclass implementation.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        def _is_track_cmd_allowed(self) -> bool:
            
            if self.dsh_model.mode != DishMode.OPERATE:
                return False
            if self.dsh_model.pointing_state not in [PointingState.TRACK, PointingState.READY]:
                return False
            return True
       
        # Check if track command is allowed
        if not _is_track_cmd_allowed(self):
            message = f"DishDriver {self.dsh_model.dsh_id} track command not allowed in dish mode {self.dsh_model.mode.name} or pointing state {self.dsh_model.pointing_state.name}."
            raise XInvalidTransition(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        # Calculate the desired AltAz for the current target
        target_id, target = self.get_target_tuple()
        if target is None:
            message = f"DishDriver {self.dsh_model.dsh_id} track command requires a valid target to be set."
            raise XInvalidTransition(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        if target.pointing not in [PointingType.SIDEREAL_TRACK, PointingType.NON_SIDEREAL_TRACK]:
            message = f"DishDriver {self.dsh_model.dsh_id} track command ignored for target {target.id} with pointing type {target.pointing.name}."
            logger.warning(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            return

        altaz = self.get_desired_altaz(target=target)
       
        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._track(altaz.alt.degree, altaz.az.degree)
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to track to AltAz (Alt: {altaz.alt.degree}, Az: {altaz.az.degree}): {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e
        finally:
            self.update_pec_hist(target_id) # Update AltAz and PEC history after each scan attempt, regardless of success or failure

        # Set the dish pointing state to TRACKING
        self.dsh_model.pointing_state = PointingState.TRACK
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def scan(self):    
        """
            Scan the drivers current target if states and modes permit. Delegates to subclass implementation.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        def _is_scan_cmd_allowed(self) -> bool:
            
            if self.dsh_model.mode != DishMode.OPERATE:
                return False
            if self.dsh_model.pointing_state not in [PointingState.SCAN, PointingState.READY]:
                return False
            return True
       
        # Check if scan command is allowed
        if not _is_scan_cmd_allowed(self):
            message = f"DishDriver {self.dsh_model.dsh_id} scan command not allowed in dish mode or pointing state."
            raise XInvalidTransition(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        # Calculate the desired AltAz for the current target
        target_id, target = self.get_target_tuple()
        if target is None:
            message = f"DishDriver {self.dsh_model.dsh_id} scan command requires a valid target to be set."
            raise XInvalidTransition(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        if target.pointing not in [PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN]:
            message = f"DishDriver {self.dsh_model.dsh_id} scan command ignored for target {target.id} with pointing type {target.pointing.name}."
            logger.warning(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            return

        altaz = self.get_desired_altaz(target=target)
       
        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._scan(altaz.alt.degree, altaz.az.degree)
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to scan to AltAz (Alt: {altaz.alt.degree}, Az: {altaz.az.degree}): {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e
        finally:
            self.update_pec_hist(target_id) # Update AltAz and PEC history after each scan attempt, regardless of success or failure

        # Set the dish pointing state to SCAN
        self.dsh_model.pointing_state = PointingState.SCAN
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def stop(self):
        """
            Stop any movement of the dish. Delegates to subclass implementation.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._stop()
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to stop dish movement: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish pointing state to READY
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def slew(self, altaz: AltAz):
        """ Slew to the target AltAz position if states and modes permit. Delegates to subclass implementation.
            :param altaz: Target AltAz position
        """
        def _is_slew_cmd_allowed(self) -> bool:

            if self.dsh_model.mode != DishMode.OPERATE:
                return False
            if self.dsh_model.pointing_state != PointingState.READY:
                return False
            return True

        # Check if slew command is allowed
        if not _is_slew_cmd_allowed(self):
            message = f"DishDriver {self.dsh_model.dsh_id} slew command not allowed in dish mode {self.dsh_model.mode.name} or pointing state {self.dsh_model.pointing_state.name}."
            raise XInvalidTransition(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        # Update the desired AltAz in the dish model
        self.set_desired_altaz(altaz)

        # If the dish is already at the desired AltAz within resolution, avoid entering SLEW.
        current_alt = self.dsh_model.pointing_altaz.get("alt", None) if self.dsh_model.pointing_altaz else None
        current_az = self.dsh_model.pointing_altaz.get("az", None) if self.dsh_model.pointing_altaz else None
        if current_alt is not None and current_az is not None:
            if abs(current_alt - altaz.alt.degree) <= self.get_resolution() and abs(current_az - altaz.az.degree) <= self.get_resolution():
                logger.info(
                    f"DishDriver {self.dsh_model.dsh_id} already on target at AltAz "
                    f"(Alt: {current_alt}, Az: {current_az}); slew command not required."
                )
                self.dsh_model.pointing_state = PointingState.READY
                self.dsh_model.last_update = datetime.now(timezone.utc)
                return
  
        # Delegate to subclass implementation
        try:
            with self._rlock:
                self._slew(altaz.alt.degree, altaz.az.degree)
        except Exception as e:
            if isinstance(e, XCommsFailure):
                message = f"DishDriver {self.dsh_model.dsh_id} failed to communicate with dish controller: {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} failed to slew to AltAz (Alt: {altaz.alt.degree}, Az: {altaz.az.degree}): {e}" + \
                          f" Transitioning to UNKNOWN mode."
                logger.exception(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

            self.dsh_model.increment_failures()
            self.dsh_model.mode = DishMode.UNKNOWN
            self.dsh_model.pointing_state = PointingState.UNKNOWN
            self.dsh_model.last_update = datetime.now(timezone.utc)
            raise e

        # Set the dish pointing state to SLEWing
        self.dsh_model.pointing_state = PointingState.SLEW
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def get_desired_altaz(self, target: TargetModel) -> AltAz:
        """ Calculates the desired AltAz for the given target at the current time.
        """  
        time = Time(datetime.now(timezone.utc))
        frame = AltAz(obstime=time, location=self.location)
          
        if target.pointing == PointingType.SIDEREAL_TRACK:
            # Sidereal target
            sky_coord = SkyCoord(ra=target.sky_coord.ra, dec=target.sky_coord.dec, unit='deg', frame=target.sky_coord.frame)
            desired_altaz = sky_coord.transform_to(frame)
            
        elif target.pointing == PointingType.NON_SIDEREAL_TRACK:
            # Non-sidereal target (solar system body)
            body_coord = get_body(body=target.id, time=time, location=self.location)
            desired_altaz = body_coord.transform_to(frame)

        elif target.pointing == PointingType.DRIFT_SCAN:
            # Drift scan target
            alt = target.altaz.get("alt")
            az = target.altaz.get("az")
            alt_q = alt if hasattr(alt, 'unit') else float(alt) * u.deg
            az_q = az if hasattr(az, 'unit') else float(az) * u.deg
            desired_altaz = AltAz(obstime=time, location=self.location, alt=alt_q, az=az_q)

        elif target.pointing == PointingType.OFFSET_SCAN:

            if target.scan is None or target.scan.offset is None or target.scan.rate is None or target.scan.angle is None:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot calculate desired AltAz for OFFSET_SCAN target without valid scan parameters.\n{self.dsh_model.to_dict()}"
                raise XStreamUnableToExtract(self.dsh_model.set_last_err(message))

            # Step 1: Compute true target position in AltAz at current time
            if target.sky_coord is not None:
                sky_coord = SkyCoord(ra=target.sky_coord.ra, dec=target.sky_coord.dec, unit='deg', frame=target.sky_coord.frame)
                true_altaz = sky_coord.transform_to(frame)
            elif target.id is not None:
                body_coord = get_body(body=target.id, time=time, location=self.location)
                true_altaz = body_coord.transform_to(frame)
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot calculate desired AltAz for OFFSET_SCAN target without valid sky_coord, altaz or target id.\n{self.dsh_model.to_dict()}"
                raise XStreamUnableToExtract(self.dsh_model.set_last_err(message))
            
            # Step 2: Compute elapsed time
            now = datetime.now(timezone.utc)
            elapsed = (now - target.scan.start).total_seconds() * u.s if target.scan.start is not None else 0 * u.s

            # Step 3: Compute angular offset
            start_offset = target.scan.offset * u.deg
            rate = target.scan.rate * u.deg / u.s
            offset = start_offset + rate * elapsed

            # Step 4: Apply offset in tangent plane
            position_angle = target.scan.angle * u.deg
            desired_altaz = true_altaz.directional_offset_by(position_angle, offset)
            logger.info(f"DishDriver {self.dsh_model.dsh_id} performing OFFSET_SCAN: true AltAz (Alt: {true_altaz.alt.degree}°, Az: {true_altaz.az.degree}°), start offset: {start_offset}°, current offset: {offset}°, elapsed time: {elapsed}s, position angle: {position_angle}°, resulting in desired AltAz (Alt: {desired_altaz.alt.degree}°, Az: {desired_altaz.az.degree}°).") 

        elif target.pointing == PointingType.FIVE_POINT_SCAN:
            
            if target.scan is None or target.scan.offset is None or target.scan.direction is None:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot calculate desired AltAz for FIVE_POINT_SCAN target without valid scan parameters.\n{self.dsh_model.to_dict()}"
                raise XStreamUnableToExtract(self.dsh_model.set_last_err(message))

            # Step 1: Compute true target position in AltAz at current time
            if target.sky_coord is not None:
                sky_coord = SkyCoord(ra=target.sky_coord.ra, dec=target.sky_coord.dec, unit='deg', frame=target.sky_coord.frame)
                true_altaz = sky_coord.transform_to(frame)
            elif target.id is not None:
                body_coord = get_body(body=target.id, time=time, location=self.location)
                true_altaz = body_coord.transform_to(frame)
            else:
                message = f"DishDriver {self.dsh_model.dsh_id} cannot calculate desired AltAz for FIVE_POINT_SCAN target without valid sky_coord, altaz or target id.\n{self.dsh_model.to_dict()}"
                raise XStreamUnableToExtract(self.dsh_model.set_last_err(message))
                
            # Step 2: Compute angular offset
            offset = target.scan.offset * u.deg if target.scan.direction != "C" else 0 * u.deg
            direction_angles = {"C": 0, "N": 0, "S": 180, "E": 90, "W": 270}
            position_angle = direction_angles[target.scan.direction] * u.deg

            # Step 3: Apply directional (C, N, S, E, W) offset to true AltAz
            desired_altaz = true_altaz.directional_offset_by(position_angle, offset) if target.scan.direction != "C" else true_altaz

            logger.info(f"DishDriver {self.dsh_model.dsh_id} performing FIVE_POINT_SCAN: true AltAz (Alt: {true_altaz.alt.degree}°, Az: {true_altaz.az.degree}°), offset: {offset}°, position angle: {position_angle}°, resulting in desired AltAz (Alt: {desired_altaz.alt.degree}°, Az: {desired_altaz.az.degree}°).") 

        self.set_desired_altaz(desired_altaz)
        return desired_altaz

    def reset_pec_hist(self):
        """ Reset PEC and AltAz history to all zeros.
        """
        with self._rlock:
            self.pec_hist = np.zeros((self.MAX_HISTORY, 3)) # Reset PEC history to all zeros

    def reset_pointing_hist(self):
        """ Reset pointing AltAz history to all zeros.
        """
        with self._rlock:
            self.pointing_altaz_hist = np.zeros((self.MAX_HISTORY, 3)) # Reset pointing AltAz history to all zeros
            self.desired_altaz_hist = np.zeros((self.MAX_HISTORY, 3)) # Reset desired AltAz history to all zeros

    def update_pec_hist(self, tgt_id: str = None):
        """ Update the PEC history with the latest values.
            Maintains a history of the last N PEC values where N is defined by MAX_HISTORY.
        """
        alt_pec, az_pec = self.get_current_pec()

        # PEC will be None if either a pointing or desired AltAz value is not available
        if alt_pec is None or az_pec is None:
            return # If we cannot calculate PEC, do not update history

        now = Time(datetime.now(timezone.utc)) # Current datetime in UTC
        now.format = 'unix'

        # Update history by obtaining the current thread lock first
        # Numpy arrays are not inherently thread-safe
        with self._rlock:

            if self.pec_hist is None:
                self.reset_pec_hist() 

            self.pec_hist = np.roll(self.pec_hist, shift=-1, axis=0)
            self.pec_hist[-1] = (now.value, alt_pec, az_pec)

            tgt_pec = self.dsh_model.get_pec_by_tgt_id(tgt_id) if tgt_id is not None else None
            
            if tgt_pec is None:
                tgt_pec = PECModel(tgt_id=tgt_id)
                self.dsh_model.tgt_pec.append(tgt_pec)
            
            tgt_pec.alt_rms, tgt_pec.az_rms = self.get_rms_pec()
            
            now = datetime.now(timezone.utc)
            tgt_pec.last_update = now
            self.dsh_model.last_update = now

    def update_pointing_hist(self):
        """ Update the PEC and pointing AltAz history with the latest values.
            Maintains a history of the last N PEC and pointing AltAz values where N is defined by MAX_HISTORY.
        """
        pointing_alt = self.dsh_model.pointing_altaz.get("alt", None) if self.dsh_model.pointing_altaz else None
        pointing_az = self.dsh_model.pointing_altaz.get("az", None) if self.dsh_model.pointing_altaz else None

        desired_alt = self.dsh_model.desired_altaz.get("alt", None) if self.dsh_model.desired_altaz else None
        desired_az = self.dsh_model.desired_altaz.get("az", None) if self.dsh_model.desired_altaz else None

        # PEC will be None if either a pointing or desired AltAz value is not available
        if pointing_alt is None or pointing_az is None:
            return # If we cannot get current pointing AltAz, do not update history

        now = Time(datetime.now(timezone.utc)) # Current datetime in UTC
        now.format = 'unix'

        # Update history by obtaining the current thread lock first
        # Numpy arrays are not inherently thread-safe
        with self._rlock:

            if self.pointing_altaz_hist is None or self.desired_altaz_hist is None:
                self.reset_pointing_hist() 

            self.pointing_altaz_hist = np.roll(self.pointing_altaz_hist, shift=-1, axis=0)
            self.pointing_altaz_hist[-1] = (now.value, pointing_alt, pointing_az)

            self.desired_altaz_hist = np.roll(self.desired_altaz_hist, shift=-1, axis=0)
            self.desired_altaz_hist[-1] = (now.value, desired_alt, desired_az)

    def update_mode_hist(self):
        """ Update the mode history with the latest values.
            Maintains a history of the last N mode values where N is defined by MAX_HISTORY.
        """
        now = Time(datetime.now(timezone.utc)) # Current datetime in UTC
        now.format = 'unix'

        mode_value = self.dsh_model.mode.value if self.dsh_model.mode is not None else None

        # Update history by obtaining the current thread lock first
        # Numpy arrays are not inherently thread-safe
        with self._rlock:

            if self.mode_hist is None:
                self.reset_mode_hist() 

            self.mode_hist = np.roll(self.mode_hist, shift=-1, axis=0)
            self.mode_hist[-1] = (now.value, mode_value)

    ##############################################################################
    # Callback Methods Available to be called by Subclasses
    ##############################################################################

    def notify_imminent_power_loss(self):
        """ Notify the base class that imminent power loss has been detected on the dish e.g. UPS event.
        """
        logger.info(f"DishDriver {self.dsh_model.dsh_id} notified of imminent power loss event. Transitioning to SHUTDOWN mode.\n{self.dsh_model.to_dict()}")

        try:
            self.set_dish_mode(DishMode.SHUTDOWN)
        except Exception as e:
            message = f"DishDriver {self.dsh_model.dsh_id} failed to shutdown cleanly during imminent power loss notification: {e}" + \
                      f" Transitioning to SHUTDOWN mode."
            logger.error(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")

        self.dsh_model.mode = DishMode.SHUTDOWN
        self.dsh_model.last_update = datetime.now(timezone.utc)
    
    def notify_low_power(self):
        """ Notify the base class that low power has been triggered on the dish e.g. UPS event.
        """
        logger.info(f"DishDriver {self.dsh_model.dsh_id} notified of low power event.\n{self.dsh_model.to_dict()}")

        if self.dsh_model.mode in [DishMode.STANDBY_LP, DishMode.SHUTDOWN]:
            return  # Already in low power or shutdown mode
        elif self.dsh_model.mode == DishMode.MAINTENANCE:
            message = f"DishDriver {self.dsh_model.dsh_id} is in MAINTENANCE mode during low power event. Cannot transition to STANDBY_LP mode."
            logger.warning(self.dsh_model.set_last_err(message) + f"\n{self.dsh_model.to_dict()}")
        else:
            self.set_dish_mode(DishMode.STANDBY_LP)

    def notify_full_power(self):
        """ Notify the base class that full power is available on the dish e.g. UPS event cleared.
        """
        logger.info(f"DishDriver {self.dsh_model.dsh_id} notified of full power availability.\n{self.dsh_model.to_dict()}")

        if self.dsh_model.mode in [DishMode.STANDBY_LP]:
            self.set_dish_mode(DishMode.STANDBY_FP)
        else:
            self.set_dish_mode(DishMode.STARTUP)
    
    ##############################################################################
    # Private Methods Expected to be Implemented by Subclasses
    ##############################################################################

    def _get_rotation_speed(self) -> float:
        """ Get the rotation speed of the dish from the subclass implementation.
            :return: The rotation speed in degrees per second.
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _get_min_max_alt(self) -> Tuple[float, float]:
        """ Get the minimum and maximum altitude limits of the dish from the subclass implementation.
            :return: A tuple of (min_altitude, max_altitude) in degrees.
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _get_resolution(self) -> float:
        """ Get the resolution of the dish from the subclass implementation.
            :return: The resolution in degrees per step.
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _get_stow_altaz(self) -> Tuple[float, float]:
        """ Get the stow Alt Az position of the dish from the subclass implementation.
            :return: The stow Alt Az position as a tuple of (altitude, azimuth).
        """
        raise NotImplementedError("Subclasses should implement this method.")
 
    def _get_current_altaz(self) -> (float, float):
        """
            Get the current AltAz pointing of the dish from the subclass implementation.
            :return: The current AltAz pointing of the dish as a tuple of (altitude, azimuth).
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_startup_mode(self):
        """
            Sets the dish to startup mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_standby_fp_mode(self):
        """
            Sets the dish to standby full power mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_standby_lp_mode(self):
        """
            Sets the dish to standby low power mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_shutdown_mode(self):
        """
            Sets the dish to shutdown mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_operate_mode(self):
        """
            Sets the dish to operate mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_maintenance_mode(self):
        """
            Sets the dish to maintenance mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_config_mode(self):
        """
            Sets the dish to config mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_stow_mode(self, alt: float, az: float):
        """
            Sets the dish to stow mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _set_operate_mode(self):
        """
            Sets the dish to operate mode.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _track(self, alt: float, az: float):
        """
            Track the current target.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _stop(self):
        """
            Stop any movement of the dish.
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def _slew(self, alt: float, az: float):
        """
            Slew the dish to the specified Alt Az position.
            :param alt: Target altitude in degrees
            :param az: Target azimuth in degrees
            :raises NotImplementedError: If the method is not implemented by a subclass
        """
        raise NotImplementedError("Subclasses should implement this method.")

if __name__ == "__main__":
    # Example usage 

    dish001 = DishModel(
        dsh_id="dish001",
        short_desc="70cm Discovery Dish",
        diameter=0.7,
        fd_ratio=0.37,
        latitude=53.187052, longitude=-2.256079, height=94.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.H3T_1420,
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        last_update=datetime.now(timezone.utc)
    )
