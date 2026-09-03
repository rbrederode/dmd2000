from util.xbase import XAPIValidationFailed
from typing import Any, Dict

import logging
logger = logging.getLogger(__name__)

class API():
    
    def __init__(self):
        pass

    def get_api_version(self) -> str:
        """
        Returns the API version string of the implementation.
            :return: API version string in the format "major.minor" e.g. "1.0"
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def get_legacy_supported_versions(self) -> [str]:
        """
        Returns a list of legacy supported API version strings that the implementation can translate to/from.
            :return: List of legacy supported API version strings in the format "major.minor" e.g. ["0.9", "0.8"]
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def validate(self, api_msg: Dict[str, Any]):
        """
        Validates that the api_msg dictionary contains the required fields and that the fields
        conform to the implementation's API_VERSION types and formats.
            :param api_msg: APIMessage dictionary containing the API call in API_VERSION format
            :raises XAPIValidationFailed: If the message fails validation
        """
        raise NotImplementedError("Subclasses should implement this method.")

    def translate(self, api_msg: Dict[str, Any], target_version: str) -> Dict[str, Any]:
        """
        Translates an api_msg dictionary to a target_version.
        Must support translation between an API implementation's API_VERSION and LEGACY_SUPPORTED_VERSIONS.
        Must support bi-directional translation i.e. to and from versions.
            :param api_msg: Dictionary containing an APIMessage dictionary
            :param target_version: Target version of the API
            :return: Translated api message dictionary
            :raises XAPIValidationFailed: If the api message fails validation
        """
        raise NotImplementedError("Subclasses should implement this method.")