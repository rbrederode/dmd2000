from datetime import datetime, timezone
import pprint
import json
import logging
import os
import socket
import time

# Import google api tools
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

from models.app import AppModel
from models.dig import DigitiserModel
from models.health import HealthState
from models.comms import CommunicationStatus
from models.ui import UIDriverType
from ui.drivers.driver import UIDriver
from ui.drivers.gsheets.gsheets_model import GSheetConfig
from util.xbase import XSoftwareFailure
from util.util import dict_flatten, dict_unflatten

logger = logging.getLogger(__name__)

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Retry decorator for handling transient network errors
def retry_on_timeout(max_retries=3, delay=5):
    """
    Decorator to retry a function call on timeout errors
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except TimeoutError as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Google Sheets Driver timeout error on attempt {attempt + 1}/{max_retries}. Retrying in {delay} seconds...")
                        time.sleep(delay)
                    else:
                        logger.error(f"Google Sheets Driver failed after {max_retries} attempts due to timeout")
                        raise
                except socket.timeout as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Google Sheets Driver socket timeout on attempt {attempt + 1}/{max_retries}. Retrying in {delay} seconds...")
                        time.sleep(delay)
                    else:
                        logger.error(f"Google Sheets Driver: Failed after {max_retries} attempts due to socket timeout")
                        raise
                except Exception as e:
                    # Don't retry on other types of errors
                    raise
            return None
        return wrapper
    return decorator

# Helper function to execute Google Sheets API requests with retry
@retry_on_timeout(max_retries=3, delay=5)
def execute_sheets_request(request):
    """
    Execute a Google Sheets API request with timeout handling
    """
    return request.execute()

class GoogleSheetsDriver(UIDriver):

    def __init__(self, config: GSheetConfig):
        """Uses the Google Sheets API to authenticate with Google """

        self.config = config
                
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                credentials_file = "credentials.json"
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                except FileNotFoundError as e:
                    cwd = os.getcwd()
                    raise XSoftwareFailure(
                        "Google Sheets Driver could not start because OAuth client credentials were not found. "
                        f"Expected '{credentials_file}' in the current working directory: {cwd}. "
                        "Place the Google API OAuth client secrets file there as 'credentials.json', "
                        "or run Telescope Manager with '--headless true' to disable UI driver startup."
                    ) from e
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(creds.to_json())

        try:
            # Build service 
            service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        except HttpError as err:
            logger.error(f"Google Sheets Driver: HTTP Error: {err}")

        self.sheet = service.spreadsheets()

    def _map_to_range(self, model_type: str) -> str:

        if model_type == "DigitiserList" or model_type == "DigitiserModel":
            return self.config.dig_range
        elif model_type == "TelescopeManagerModel":
            return self.config.tm_range
        elif model_type == "DishManagerModel":
            return self.config.dsh_range
        elif model_type == "ScienceDataProcessorModel":
            return self.config.sdp_range
        elif model_type == "ODAModel":
            return self.config.oda_range
        elif model_type == "ObsList":
            return self.config.odt_range
        elif model_type == "WeatherStationModel":
            return self.config.ws_range
        else:
            logger.warning(f"Google Sheets Driver: Unknown model type: {model_type}")
            return None

    def publish(self, model_dict: dict) -> None:
        flat = dict_flatten(model_dict)

        # Need to determine which range to write to based on the model type
        _type = model_dict.get("_type", "")
        range = self._map_to_range(_type)

        if range is None:
            logger.warning(f"Google Sheets Driver could not map model type {_type} to a Google Sheets range")
            return

        self._write_to_sheet(flat, sheet_range=range)

    def read_config(self, model_type: str) -> dict:
        return self._read_from_sheet(model_type)

    def _write_to_sheet(self, flat_dict, sheet_range=None):
        # Build rows of [key, value] pairs, sorted by key name
        rows = [[key, flat_dict[key]] for key in sorted(flat_dict.keys())]

        if sheet_range is None:
             logger.error("Google Sheets Driver no sheet range provided while attempting to write {flat_dict} to sheet, aborting write operation")
             return

        try:    
            # Clear existing data in the sheet range before writing new data
            execute_sheets_request(
                self.sheet.values().clear(
                    spreadsheetId=self.config.sheet_id,
                    range=sheet_range
                )
            )
            # Write new data to the sheet
            execute_sheets_request(
                self.sheet.values().update(
                    spreadsheetId=self.config.sheet_id,
                    range=sheet_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": rows}
                )
            )
        except Exception as err:
            logger.error(f"Google Sheets Driver error updating model in Google Sheets: {err}")

    def _read_from_sheet(self, model_type: str):
        
        sheet_range = self._map_to_range(model_type)

        if sheet_range is None:
            logger.warning(f"Google Sheets Driver could not map model type {model_type} to a Google Sheets range")
            return None
        
        try:
            result = execute_sheets_request(
                self.sheet.values().get(
                    spreadsheetId=self.config.sheet_id,
                    range=sheet_range))

            if 'error' in result:
                error = result['error']['details'][0]
                error_msg = error.get('errorMessage', 'Unknown error')
                logger.error(f"Google Sheets Driver - error getting {model_type} configuration: {error_msg}")
            else:
                values = result.get("values", [])
                # Convert list of lists to a dict
                flat_dict = {row[0]: row[1] for row in values if len(row) >= 2}
                unflattened_dict = dict_unflatten(flat_dict)

        except Exception as err:
            logger.error(f"Google Sheets Driver error reading model from Google Sheets: {err}")
            return None

        return unflattened_dict

if __name__ == "__main__":

    config = GSheetConfig(
        sheet_id="1OJ2wobPrwsQgeRW9gHgiMZ4fu8NKcpYYLGnwjv8Gt5M",
        tm_range="TM_UI_API++!A2:B",
        dig_range="TM_UI_API++!D2:E",
        dsh_range="TM_UI_API++!G2:H",
        sdp_range="TM_UI_API++!J2:K",
        odt_range="TM_UI_API++!M2:N",
        oda_range="TM_UI_API++!P2:Q",
        ws_range="TM_UI_API++!S2:T",
        last_update=datetime.now(timezone.utc)
    )
    print("GSheetConfig created successfully:", config.to_dict())


    driver = GoogleSheetsDriver(config=config)

    dig001 = DigitiserModel(
        dig_id="dig001",
        app=AppModel(
            app_name="dig",
            app_running=False,
            num_processors=4,
            queue_size=0,
            interfaces=[],
            processors=[],
            health=HealthState.UNKNOWN,
            last_update=datetime.now()
        ),
        load=False,
        gain=0.0,
        sample_rate=0.0,
        bandwidth=0.0,
        center_freq=0.0,
        freq_correction=0,
        channels=0,
        scan_duration=0,
        scanning={"obs_id": "obs001", "tgt_index": 1, "freq_scan": 5},
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdp_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdr_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdr_eeprom={},
        last_update=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    )

    driver.publish(dig001.to_dict())
