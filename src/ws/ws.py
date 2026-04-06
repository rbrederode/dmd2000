import sys
import time
import random
import logging
import threading
from datetime import datetime, timezone

from api import ws_dm, tm_ws
from models.comms import CommunicationStatus, InterfaceType
from models.ws import WeatherData, WeatherStationModel
from env.app import App
from env.events import ConnectEvent, DisconnectEvent, DataEvent, ConfigEvent, ObsEvent
from ipc.message import AppMessage, APIMessage
from ipc.action import Action
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.app import AppModel, HealthState

logger = logging.getLogger(__name__)

class WeatherStation(App):
    """ Weather Station application class.

    This application collects weather data (or simulates it) and peridocally sends updates to the Dish Manager and Telescope Manager.
    Weather data is needed for safety reasons to perform a wind stow of the dish when wind speeds are too high.
    """
    ws_model = WeatherStationModel(id="ws001")

    def __init__(self, app_name: str = "ws"):

        super().__init__(app_name=app_name, app_model=self.ws_model.app)

        # Register interface between Weather Station App and Dish Manager
        self.dm_system = "dm"
        self.dm_api = ws_dm.WS_DM()
        # Dish Manager TCP Client
        self.dm_endpoint = TCPClient(description=self.dm_system, queue=self.get_queue(), host=self.get_args().dm_host, port=self.get_args().dm_port)
        # Register Dish Manager interface with the App
        self.register_interface(self.dm_system, self.dm_api, self.dm_endpoint, InterfaceType.APP_APP)
        # Set initial Telescope Manager connection status
        self.ws_model.dm_connected = CommunicationStatus.NOT_ESTABLISHED

        # Register interface between Weather Station App and Telescope Manager
        self.tm_system = "tm"
        self.tm_api = tm_ws.TM_WS()
        # Telescope Manager TCP Server
        self.tm_endpoint = TCPServer(description=self.tm_system, queue=self.get_queue(), host=self.get_args().tm_host, port=self.get_args().tm_port)
        # Register Telescope Manager interface with the App
        self.register_interface(self.tm_system, self.tm_api, self.tm_endpoint, InterfaceType.APP_APP)
        # Set initial Telescope Manager connection status
        self.ws_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED

        self.ws_model.sim_mode = self.get_args().sim

    def add_args(self, arg_parser): 
        """ Specifies the weather station's command line arguments.
        """
        super().add_args(arg_parser)

        arg_parser.add_argument("--dm_host", type=str, required=False, help="TCP client host to connect to the Dish Manager", default="localhost")
        arg_parser.add_argument("--dm_port", type=int, required=False, help="TCP client port to connect to the Dish Manager", default=51000)
        
        arg_parser.add_argument("--tm_host", type=str, required=False, help="TCP host to listen on for Telescope Manager commands", default="localhost")
        arg_parser.add_argument("--tm_port", type=int, required=False, help="TCP port for Telescope Manager commands", default=50003)

        arg_parser.add_argument("--sim", type=str, required=False, help="Simulator mode (off, calm, windy, stormy)", default="calm")
        
    def process_init(self) -> Action:
        """Initialisation process for the Weather Station application.
        """
        logging.info("WeatherStation initialising with mode: %s", self.ws_model.sim_mode)

        action = Action()

        # Start the polling timer to update wind speed at 1Hz intervals
        action.set_timer_action(Action.Timer(
            name=f"weather_polling_timer", 
            timer_action=1000)) 

        # Start server endpoints and connect client endpoints to interfaces
        self.dm_endpoint.connect()
        self.tm_endpoint.start()

        return action

    def process_dm_connected(self, event) -> Action:
        """ Processes Dish Manager connected events.
        """
        logger.info(f"WeatherStation connected to Dish Manager: {event.remote_addr}")

        self.ws_model.dm_connected = CommunicationStatus.ESTABLISHED

        action = Action()
        return action

    def process_dm_disconnected(self, event) -> Action:
        """ Processes Dish Manager disconnected events.
        """
        logger.info(f"WeatherStation disconnected from Dish Manager: {event.remote_addr}")

        self.ws_model.dm_connected = CommunicationStatus.NOT_ESTABLISHED

        action = Action()
        return action

    def process_dm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Dish Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"WeatherStation received Dish Manager {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")
        
        action = Action()
        return action

    def process_tm_connected(self, event) -> Action:
        """ Processes Telescope Manager connected events.
        """
        logger.info(f"WeatherStation connected to Telescope Manager: {event.remote_addr}")

        self.ws_model.tm_connected = CommunicationStatus.ESTABLISHED

        action = Action()

        # Send initial status advice message to Telescope Manager
        # Informs TM of current WS status
        tm_adv = self._construct_status_adv_to_tm()
        action.set_msg_to_remote(tm_adv)
        return action

    def process_tm_disconnected(self, event) -> Action:
        """ Processes Telescope Manager disconnected events.
        """
        logger.info(f"WeatherStation disconnected from Telescope Manager: {event.remote_addr}")

        self.ws_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED

        action = Action()
        return action

    def process_tm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Telescope Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"WeatherStation received Telescope Manager {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")
        
        action = Action()
        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"WeatherStation timer event: {event}")

        action = Action()

        if self.ws_model.sim_mode != "off" and self.ws_model.dm_connected == CommunicationStatus.ESTABLISHED:
            weather_data = self._generate_weather()
            dm_msg = self._construct_dm_advice_message(weather_data)
            action.set_msg_to_remote(dm_msg)

        action.set_timer_action(Action.Timer(
            name=f"weather_polling_timer", 
            timer_action=1000)) 

        return action

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application.
        """

        health_state = HealthState.UNKNOWN

        if self.ws_model.tm_connected == CommunicationStatus.ESTABLISHED and self.ws_model.dm_connected == CommunicationStatus.ESTABLISHED:
            health_state = HealthState.OK
        elif self.ws_model.tm_connected != CommunicationStatus.ESTABLISHED and self.ws_model.dm_connected == CommunicationStatus.ESTABLISHED:
            health_state = HealthState.DEGRADED
        elif self.ws_model.tm_connected == CommunicationStatus.ESTABLISHED and self.ws_model.dm_connected != CommunicationStatus.ESTABLISHED:
            health_state = HealthState.FAILED
        
        return health_state

    def process_status_event(self, event) -> Action:
        """ Processes status update events.
        """
        self.get_app_processor_state()

        action = Action()

        if self.ws_model.tm_connected == CommunicationStatus.ESTABLISHED:
            action.set_msg_to_remote(self._construct_status_adv_to_tm())
        return action
    
    def _construct_status_adv_to_tm(self) -> APIMessage:
        """ Constructs a status advice message for the Telescope Manager.
        """
        tm_adv = APIMessage(api_version=self.tm_api.get_api_version())

        tm_adv.set_json_api_header(
            api_version=self.tm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.ws_model.app.app_name, 
            to_system="tm", 
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": tm_ws.PROPERTY_STATUS, 
                "value": self.ws_model.to_dict(), 
                "message": "WS status update"
            })
        return tm_adv

    def _construct_dm_advice_message(self, weather: WeatherData) -> APIMessage:

        dm_adv = APIMessage(api_version=self.dm_api.get_api_version())
        
        dm_adv.set_json_api_header(
            api_version=self.dm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.ws_model.app.app_name, 
            to_system="dm",
            entity="ws001",
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": ws_dm.PROPERTY_WEATHER,
                "value": weather.to_dict()
        })
        return dm_adv

    def _generate_weather(self) -> WeatherData:

        if self.ws_model.sim_mode not in ["off", "calm", "windy", "stormy"]:
            logger.error(f"WeatherStation sim mode '{self.ws_model.sim_mode}' is not recognised. Expecting 'off', 'calm', 'windy', or 'stormy'. Defaulting to 'off'.")
            self.ws_model.sim_mode = "off"

        if self.ws_model.sim_mode == "off":
            return None

        weather = WeatherData(
            obs_time=datetime.now(timezone.utc),
            last_update=datetime.now(timezone.utc),
            ws_id="ws001")

        if self.ws_model.sim_mode == "calm":
            weather.wind_speed = random.uniform(0, 15)
            weather.temperature = random.uniform(15, 25)
            weather.humidity = random.uniform(30, 70)
            weather.pressure = random.uniform(1000, 1025)
            weather.precipitation = random.uniform(0, 1)
            weather.dew_point = random.uniform(10, 20)
            weather.air_quality = random.uniform(0, 50)
            weather.uv_index = random.uniform(0, 5)
            weather.cloud_cover = random.uniform(0, 30)
        elif self.ws_model.sim_mode == "windy":
            weather.wind_speed = random.uniform(16, 25)
            weather.temperature = random.uniform(10, 20)
            weather.humidity = random.uniform(40, 80)
            weather.pressure = random.uniform(990, 1015)
            weather.precipitation = random.uniform(0, 5)
            weather.dew_point = random.uniform(5, 15)
            weather.air_quality = random.uniform(10, 100)
            weather.uv_index = random.uniform(0, 7)
            weather.cloud_cover = random.uniform(20, 70)
        elif self.ws_model.sim_mode == "stormy":
            weather.wind_speed = random.uniform(26, 40)
            weather.temperature = random.uniform(5, 15)
            weather.humidity = random.uniform(60, 100)
            weather.pressure = random.uniform(970, 1000)
            weather.precipitation = random.uniform(5, 20)
            weather.dew_point = random.uniform(0, 10)
            weather.air_quality = random.uniform(50, 200)
            weather.uv_index = random.uniform(0, 10)
            weather.cloud_cover = random.uniform(50, 100)
        else:
            weather.wind_speed = random.uniform(0, 40)
            weather.temperature = random.uniform(0, 35)
            weather.humidity = random.uniform(30, 99)
            weather.pressure = random.uniform(1000, 1025)
            weather.precipitation = random.uniform(0, 20)
            weather.dew_point = random.uniform(10, 20)
            weather.air_quality = random.uniform(0, 50)
            weather.uv_index = random.uniform(0, 5)     
            weather.cloud_cover = random.uniform(0, 100)
        return weather

def user_input_thread(ws):
    while True:
        new_mode = input("Enter new sim mode (off, calm, windy, stormy) or press Enter to keep current:\n\n").strip()
        if new_mode in ["off", "calm", "windy", "stormy"]:
            ws.ws_model.sim_mode = new_mode
            print(f"Sim mode changed to: {new_mode}")
        elif new_mode:
            print("Invalid mode. Valid options: off, calm, windy, stormy.")

def main():
    ws = WeatherStation()
    ws.start()

    # Start user input thread
    threading.Thread(target=user_input_thread, args=(ws,), daemon=True).start()

    while True:
        time.sleep(1)

    ws.stop()

if __name__ == "__main__":
    main()