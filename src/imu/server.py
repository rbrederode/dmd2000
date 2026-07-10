#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Implements a TCP/IP Server that provides IMU data to clients. The server listens for incoming connections and responds to requests for IMU data.
"""

from datetime import datetime, timezone, timedelta
import logging
import socket
import sys
import threading
import time

from ipc.message import APIMessage
from models.imu import IMUData

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
    
    def __init__(self, host='127.0.0.1', port=52500):
        self.host = host
        self.port = port
        self.running = False       
    
    def _process_get_imu_data(self, api_req: APIMessage) -> APIMessage:
        """
        Process a request for imu data and return appropriate response.
        
        :param api_req: Request message
        :return: Response message as APIMessage object
        """
        # Simulate IMU data for demonstration purposes
        imu_data = IMUData(
            imu_id="imu001",
            acceleration=[0.0, 0.0, 9.81],
            angle=[3.0, 4.0, 5.0],
            angular_vel=[0.0, 0.0, 0.0],
            magnetic_vector=[30.0, 5.0, -40.0],
            temp_celsius=25.0,
            quaternion=[1.0, 0.0, 0.0, 0.0],
            last_update=datetime.now(timezone.utc)
        )
        
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
                "status": "success",
                "value": imu_data.to_dict(),
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


if __name__ == "__main__":
    # Parse command-line arguments
    import argparse
    
    parser = argparse.ArgumentParser(description='IMU TCP/IP Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host address to bind to')
    parser.add_argument('--port', type=int, default=52500, help='Port to listen on')
     
    args = parser.parse_args()
    
    # Create and start IMU server
    imu_server = IMUServer(host=args.host, port=args.port)
    
    try:
        imu_server.start()
    except KeyboardInterrupt:
        logger.info("\nIMUServer interrupted by user")
        sys.exit(0)
