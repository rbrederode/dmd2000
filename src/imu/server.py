#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Implements a TCP/IP Server that provides IMU data to clients. The server listens for incoming connections and responds to requests for IMU data.
"""

from datetime import datetime, timezone
import logging
import socket
import sys
import threading

from ipc.message import APIMessage
from imu.imu import IMU, IMUProvider, load_imu_device_list

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class IMUServer:
    """ Sends IMU data to clients over TCP/IP. 
        This is a simple server that listens for incoming connections and responds with simulated IMU data.
        The protocol conforms to the imu_app API, where clients send a request message and the server responds with the current IMU data.
    """
    
    def __init__(self, imu_provider: IMUProvider, host='127.0.0.1', port=52500):
        """ Initialize the IMUServer with the given IMU provider, host, and port. 
            :param imu_provider: An object that provides IMU data.
            :param host: The host address to bind the server to (default is '127.0.0.1').
            :param port: The port number to bind the server to (default is 52500).
        """
        if imu_provider is not None and not isinstance(imu_provider, IMUProvider):
            raise TypeError(f"IMUServer requires an IMUProvider, got {type(imu_provider).__name__}")

        self.imu_provider = imu_provider
        self.host = host
        self.port = port
        self.running = False       
    
    def _process_get_imu_data(self, api_req: APIMessage) -> APIMessage:
        """
        Process a request for imu data and return appropriate response.
        Parameters:
        
            param api_req: Request message

        return: Response message as APIMessage object
        """
        imu_data = self.imu_provider.get_imu_data() if self.imu_provider is not None else None
        status = "success" if imu_data is not None else "error"
        
        # Create response message
        api_rsp = APIMessage()
        api_rsp.set_json_api_header(
            api_version=api_req.get_api_version(),
            dt=datetime.now(timezone.utc),
            from_system=api_req.get_to_system(),
            to_system=api_req.get_from_system(),
            entity=api_req.get_entity(),
            api_call={
                "msg_type": "rsp",
                "action_code": "get",
                "property": "imu_data",
                "status": status,
                "value": imu_data.to_dict() if imu_data is not None else None,
                "message": "IMU data retrieved successfully." if imu_data is not None else "IMU data not available.",
            },
            echo=api_req.get_echo_data(),
        )
        
        return api_rsp
    
    def _handle_client(self, client_socket, address):
        """Handle a single client connection."""
        
        logger.info(f"IMUServer connection from {address}")
        
        try:
            # Receive imu request
            req_data = client_socket.recv(1024)

            if req_data:

                api_req = APIMessage()
                api_req.from_data(req_data)

                logger.info(f"IMUServer received: {api_req}")

                api_call = api_req.get_api_call()
                if api_call.get('msg_type') == 'req' and api_call.get('action_code') == 'get' and api_call.get('property') == "imu_data":

                    # Process request and generate response
                    api_rsp = self._process_get_imu_data(api_req)
                    rsp_data = api_rsp.to_data()
                
                    # Send response
                    client_socket.send(rsp_data)
                    logger.info(f"IMUServer sent: {api_rsp}")
                else:
                    logger.warning(f"IMUServer received unrecognised request: {api_call}")

        except Exception as e:
            logger.error(f"IMUServer error handling client {address}: {e}")
        
        finally:
            client_socket.close()
            logger.debug(f"IMUServer connection closed: {address}")
    
    def start(self):
        """Start the IMUServer."""
        self.running = True
                
        # Create server socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_socket.bind((self.host, self.port))
            server_socket.listen(5)
            logger.info(f"IMUServer listening on {self.host}:{self.port}")
            
            while self.running:
                try:
                    # Accept connection
                    client_socket, address = server_socket.accept()
                    
                    # Handle client in separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, address),
                        daemon=True
                    )
                    client_thread.start()
                
                except KeyboardInterrupt:
                    logger.info("Shutting down IMU Server...")
                    break
                except Exception as e:
                    logger.error(f"IMUServer error: {e}")
        
        finally:
            server_socket.close()
            self.running = False
            logger.info("IMUServer stopped")


def main():
    """Run the IMU TCP/IP server using an IMU selected from a config profile."""
    import argparse
    
    parser = argparse.ArgumentParser(description='IMU TCP/IP Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host address to bind to')
    parser.add_argument('--port', type=int, default=52500, help='Port to listen on')
    parser.add_argument("--profile", type=str, default="default", help="Configuration profile under src/config, e.g. default or jodrell.")
    parser.add_argument('--imu-id', type=str, default='imu001', help='Unique IMU model identifier.')
     
    args = parser.parse_args()

    imu_device_list = load_imu_device_list(profile=args.profile)
    imu_device = imu_device_list.get_imu_by_id(args.imu_id)

    if imu_device is None:
        logger.error("IMUServer could not find IMU %s in profile %s IMUDeviceList.json.", args.imu_id, args.profile)
        return 1

    imu = IMU(imu_device=imu_device)
    if not imu.connect():
        logger.error("IMUServer could not connect to IMU %s in profile %s.", args.imu_id, args.profile)
        return 1

    try:
        imu_server = IMUServer(imu_provider=imu, host=args.host, port=args.port)
        imu_server.start()
    except KeyboardInterrupt:
        logger.info("\nIMUServer interrupted by user")
    finally:
        imu.disconnect()

    return 0

if __name__ == "__main__":
    sys.exit(main())
