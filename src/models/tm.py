# -*- coding: utf-8 -*-

import enum
import logging
from pathlib import Path
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.comms import CommunicationStatus
from models.base import BaseModel
from models.health import HealthState
from models.proc import ProcessorModel
from models.ui import UIDriver, UIDriverType
from util import log, util
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

logger = logging.getLogger(__name__)

class AllocationState(enum.IntEnum):
    REQUESTED = 1
    ACTIVE = 2
    RELEASED = 3

class ResourceType(enum.Enum):
    DISH = "dish"
    DIGITISER = "digitiser"
    OBS = "observation"

class Allocation(BaseModel):
    """A class representing a single resource allocation."""

    schema = Schema({
        "_type": And(str, lambda v: v == "Allocation"),
        "resource_type": And(str, lambda v: isinstance(v, str)),                    # Type of resource (e.g., "dish", "digitiser")
        "resource_id": And(str, lambda v: isinstance(v, str)),                      # ID of the resource being allocated
        "allocated_type": And(str, lambda v: isinstance(v, str)),                   # Type of entity to which the resource is allocated (e.g., "observation")
        "allocated_id": And(str, lambda v: isinstance(v, str)),                     # ID of the entity to which the resource is allocated
        "state": And(AllocationState, lambda v: isinstance(v, AllocationState)),    # State of the allocation
        "expires": Or(None, And(datetime, lambda v: isinstance(v, datetime))),      # Expiration time of the allocation, None implies no expiration
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "Allocation",
            "resource_type": "<undefined>",
            "resource_id": "<undefined>",
            "allocated_type": "<undefined>",
            "allocated_id": "<undefined>",
            "state": AllocationState.REQUESTED,
            "expires": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class ResourceAllocations(BaseModel):
    """A class representing the resource allocation model.
        Contains a list of allocated resources (dishes, digitisers, etc.)

        Each allocation takes the form of a dictionary with keys:
            - resource_type: str
            - resource_id: str
            - allocated_type: str
            - allocated_id: str
    """
    schema = Schema({
        "_type": And(str, lambda v: v == "ResourceAllocations"),
        "alloc_list": And(list, lambda v: isinstance(v, list)),          # List of resource allocations
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ResourceAllocations",
            "alloc_list": [],
            "last_update": datetime.now(timezone.utc),
        }
        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_allocations(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        allocated_type: str | None = None,
        allocated_id: str | None = None,
        state: AllocationState | None = None,
        include_expired: bool = False,
    ):
        """Get a list of allocations filtered by the provided criteria.
            Args:
                resource_type (str | None): Type of resource (e.g., "dish", "digitiser")
                resource_id (str | None): ID of the resource being allocated
                allocated_type (str | None): Type of entity to which the resource is allocated (e.g., "observation")
                allocated_id (str | None): ID of the entity to which the resource is allocated
                state (AllocationState | None): State of the allocation
            Returns:
                list[Allocation]: List of allocations matching the criteria.
        """
        result = []

        now = datetime.now(timezone.utc)

        for a in self.alloc_list:

            # Check expired allocations
            if a.expires is not None and a.expires <= now and a.state != AllocationState.RELEASED:
                a.state = AllocationState.RELEASED
                a.last_update = now

            if resource_type and a.resource_type != resource_type:
                continue
            if resource_id and a.resource_id != resource_id:
                continue
            if allocated_type and a.allocated_type != allocated_type:
                continue
            if allocated_id and a.allocated_id != allocated_id:
                continue
            if state and a.state != state:
                continue

            # Consider expiration
            if include_expired:
                result.append(a)
            elif a.expires is None or a.expires > now:
                result.append(a)

        return result

    def get_active_allocation(self, resource_type: str, resource_id: str) -> Allocation | None:
        """Get the active allocation for a specific resource.
            Args:
                resource_type (str): Type of resource (e.g., "dish", "digitiser")
                resource_id (str): ID of the resource being allocated
            Returns:
                Allocation | None: The active allocation object, or None if not found.
        """
        allocations = self.get_allocations(resource_type=resource_type, resource_id=resource_id, state=AllocationState.ACTIVE, include_expired=False)
        if len(allocations) > 1:
            raise XSoftwareFailure(f"Resource Allocations found multiple active allocations for {resource_type}:{resource_id}. There should be at most one active allocation per resource.")
        return allocations[0] if len(allocations)==1 else None
 
    def is_active_allocation(self, resource_type: str, resource_id: str) -> bool:
        """Check if a specific resource is currently allocated.
            Args:
                resource_type (str): Type of resource (e.g., "dish", "digitiser")
                resource_id (str): ID of the resource being allocated
            Returns:
                bool: True if the resource is allocated, False otherwise.
        """
        return self.get_active_allocation(resource_type, resource_id) is not None

    def request_allocation(
        self,
        resource_type: str,
        resource_id: str,
        allocated_type: str,
        allocated_id: str,
        expires: datetime | None = None,
    ) -> Allocation:
        """Request a new resource allocation.
            Args:
                resource_type (str): Type of resource (e.g., "dish", "digitiser")
                resource_id (str): ID of the resource being allocated
                allocated_type (str): Type of entity to which the resource is allocated (e.g., "observation")
                allocated_id (str): ID of the entity to which the resource is allocated
                expires (datetime | None): Expiration time of the allocation, None implies no expiration
            Returns:
                Allocation: The created allocation object.
        """
        now = datetime.now(timezone.utc)

        if expires is not None and expires <= now:
            logger.warning(
                f"Resource Allocation for {resource_type}:{resource_id} cannot be requested by "
                f"{allocated_type} {allocated_id} because it expired at {expires}"
            )
            return None

        # Check if there are existing allocations for this resource type and id
        existing_allocs = self.get_allocations(resource_type=resource_type, resource_id=resource_id, include_expired=False)
        
        # Enforce exclusivity: no other active allocations allowed, however allow re-requesting for the same allocated resource
        active_allocs = [alloc for alloc in existing_allocs if alloc.state == AllocationState.ACTIVE and not (alloc.allocated_id == allocated_id and alloc.allocated_type == allocated_type)]
        if len(active_allocs) > 0:
            logger.warning(f"Resource Allocation for {resource_type}:{resource_id} cannot be requested as this resource is already actively allocated to another entity")
            return None
        
        # Return previous allocation when re-requesting the same allocated resource
        previous_alloc = [alloc for alloc in existing_allocs if alloc.state in [AllocationState.REQUESTED, AllocationState.ACTIVE] and (alloc.allocated_id == allocated_id and alloc.allocated_type == allocated_type)]
        if len(previous_alloc) > 0:
            return previous_alloc[0]

        # Create a new allocation
        alloc = Allocation(
            resource_type=resource_type,
            resource_id=resource_id,
            allocated_type=allocated_type,
            allocated_id=allocated_id,
            state=AllocationState.REQUESTED,
            expires=expires,
            last_update=datetime.now(timezone.utc),
        )

        self.alloc_list.append(alloc)
        self.last_update = datetime.now(timezone.utc)
        return alloc

    def grant_allocation(self, allocation: Allocation):
        """Grant a requested resource allocation.
            Args:
                allocation (Allocation): The allocation object to grant.
            Raises:
                XInvalidTransition: If the allocation is not in REQUESTED state or resource is already allocated.
        """
        if allocation.state != AllocationState.REQUESTED:
            raise XInvalidTransition(
                f"Resource Allocation cannot grant allocation in state {allocation.state.name} for resource {allocation.resource_type}:{allocation.resource_id}"
            )

        # Enforce exclusivity
        if self.is_active_allocation(allocation.resource_type, allocation.resource_id):
            raise XInvalidTransition(
                f"Resource Allocation {allocation.resource_type}:{allocation.resource_id} failed because it's already actively allocated"
            )

        allocation.state = AllocationState.ACTIVE
        allocation.last_update = datetime.now(timezone.utc)
        self.last_update = datetime.now(timezone.utc)

    def release_allocation(self, allocation: Allocation):
        """Release an active resource allocation.
            Args:
                allocation (Allocation): The allocation object to release.
            Raises:
                XInvalidTransition: If the allocation is not in ACTIVE state.
        """
        allocation.state = AllocationState.RELEASED
        allocation.last_update = datetime.now(timezone.utc)
        self.last_update = datetime.now(timezone.utc)

    def handle_resource_allocation(self, resource_type, resource_id, resource_req, resource_alloc) -> bool:
        """
        Generic resource allocation handler.
        Attempts to grant the resource allocation request if the resource is available.
        Logs the result of the allocation attempt.
        Returns True if the resource is granted, False otherwise.
        """

        if resource_req is None:
            logger.error(f"Resource Allocation request is None for {resource_type} {resource_id}, and cannot be allocated")
            return False

        # Resource is available
        if resource_alloc is None:
            try:
                self.grant_allocation(resource_req)
                logger.info(
                    f"Resource Allocation successfully granted "
                    f"{resource_type} {resource_id} to {resource_req.allocated_type} {resource_req.allocated_id}, "
                    f"expiring at {resource_req.expires}"
                )
                return True
            except XInvalidTransition as e:
                logger.error(
                    f"Resource Allocation failed to grant "
                    f"{resource_type} {resource_id} to {resource_req.allocated_type} {resource_req.allocated_id}, "
                    f"due to exception {e}"
                )

        # Already allocated 
        elif resource_alloc.allocated_id == resource_req.allocated_id:
            logger.info(
                f"Resource Allocation already granted "
                f"{resource_type} {resource_id} to {resource_alloc.allocated_type} {resource_alloc.allocated_id}, "
                f"expiring at {resource_alloc.expires}"
            )
            return True

        # Allocated to another observation
        else:
            logger.info(
                f"Resource Allocation failed to grant "
                f"{resource_type} {resource_id} to {resource_req.allocated_type} {resource_req.allocated_id}, "
                f"because it is already allocated to {resource_alloc.allocated_type} {resource_alloc.allocated_id},"
                f"expiring at {resource_alloc.expires}"
            )
        return False

class TelescopeManagerModel(BaseModel):
    """A class representing the telescope manager model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "TelescopeManagerModel"),
        "id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "allocations": And(ResourceAllocations, lambda v: isinstance(v, ResourceAllocations)),
        "sdp_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "dm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "ws_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "ui_drivers": Or(None, And(list, lambda v: isinstance(v, list) and all(isinstance(item, UIDriver) for item in v))),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "TelescopeManagerModel",
            "app": AppModel(
                app_name="tm",
                app_running=False,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "id": "<undefined>",
            "allocations": ResourceAllocations(),
            "sdp_connected": CommunicationStatus.NOT_ESTABLISHED,
            "dm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "ws_connected": CommunicationStatus.NOT_ESTABLISHED,
            "ui_drivers": [],
            "last_update": datetime.now(timezone.utc)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":

    import pprint

    resource_allocs = ResourceAllocations()

    print("="*40)
    print("Request allocation of dish001 to obs001")
    print("="*40)

    alloc001 = resource_allocs.request_allocation(
        resource_type="dish",
        resource_id="dish001",
        allocated_type="observation",
        allocated_id="obs001",
    )
    pprint.pprint(resource_allocs.to_dict())

    print("="*40)
    print("Grant allocation of dish001 to obs001 ")
    print("="*40)
    resource_allocs.grant_allocation(alloc001)
    pprint.pprint(resource_allocs.to_dict())

    print("="*40)
    print("Request allocation of dig001 to obs002")
    print("="*40)
    alloc002 = resource_allocs.request_allocation(
        resource_type="digitiser",
        resource_id="dig001",
        allocated_type="observation",
        allocated_id="obs002",
    )
    print("="*40)
    print("Grant allocation of dig001 to obs002 ")
    print("="*40)
    resource_allocs.grant_allocation(alloc002)
    pprint.pprint(resource_allocs.to_dict())

    print("="*40)
    print("Attempt to allocation of dig001 to obs003, expecting failure")
    print("="*40)

    try:
        alloc003 = resource_allocs.request_allocation(
            resource_type="digitiser",
            resource_id="dig001",
            allocated_type="observation",
            allocated_id="obs003",
        )
    except XSoftwareFailure as e:
        print(f"Caught expected exception: {e}")

    print("="*40)
    print("Release allocation of dig001 to obs002, request to allocate to obs003 again")
    print("="*40)
    
    resource_allocs.release_allocation(alloc002)


    alloc003 = resource_allocs.request_allocation(
            resource_type="digitiser",
            resource_id="dig001",
            allocated_type="observation",
            allocated_id="obs003",
        )
    pprint.pprint(resource_allocs.to_dict())

    print("="*40)
    print("Grant allocation of dig001 to obs003")
    print("="*40)

    resource_allocs.grant_allocation(alloc003)
    pprint.pprint(resource_allocs.to_dict())
    
    tm001 = TelescopeManagerModel(
        id="tm001",
        app=AppModel(
            app_name="dig",
            app_running=True,
            num_processors=2,
            queue_size=0,
            interfaces=["tm", "sdp"],
            processors=[ProcessorModel(
                name="Thread-1",
                current_event="Idle",
                processing_time_ms=0.0,
                last_update=datetime.now()
            )],
            health=HealthState.UNKNOWN,
            last_update=datetime.now(timezone.utc)
        ),
        sdp_connected=CommunicationStatus.NOT_ESTABLISHED,
        dm_connected=CommunicationStatus.NOT_ESTABLISHED,
        last_update=datetime.now(timezone.utc)
    )

    tm002 = TelescopeManagerModel(id="tm002")

    tm001.app.app_name = "tm"

    print("="*40)
    print("tm001 Model Initialized")
    print("="*40)
    pprint.pprint(tm001.to_dict())

    print("="*40)
    print("tm002 Model with Defaults Initialized")
    print("="*40)
    pprint.pprint(tm002.to_dict())

    gsheets_driver = UIDriver(
        driver_type=UIDriverType.GSHEETS,
        driver_config={
            "sheet_id": "1234567890",
            "tm_range": "TM_UI_API++!A2",
            "dig_range": "TM_UI_API++!D2",
            "dsh_range": "TM_UI_API++!G2",
            "sdp_range": "TM_UI_API++!J2",
            "odt_range": "TM_UI_API++!M2",
            "oda_range": "TM_UI_API++!P2",
            "ws_range": "TM_UI_API++!S2",
        },  
        poll_period=30,
        last_update=datetime.now(timezone.utc)
    )

    tm002.ui_drivers = [gsheets_driver]
    tm002.save_to_disk("./config/test", filename=tm002._type + ".json")



