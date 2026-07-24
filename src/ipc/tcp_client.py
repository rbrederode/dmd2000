#!/usr/bin/env python3

import selectors
import select
import socket
import errno
import os
import sys
import threading
import time
import struct
import traceback
import json
import argparse
from queue import Queue
from datetime import datetime, timezone

from ipc import message
from env import events
from env.app_processor import AppProcessor
from util.timer import Timer, TimerManager
from util.util import resolve_default_host
from util.xbase import XSoftwareFailure

import logging
logger = logging.getLogger(__name__)

DEFAULT_DEST_IP = "127.0.0.1"
DEST_PORT = 50000

MAX_BLOCK_SIZE = 65535   # Define a maximum block size for sending data (65,535 bytes to fit in 64KB packet)
CONNECT_TIMEOUT_SECONDS = 2.0

def _iter_framed_blocks(data: bytes, max_block_size: int):
    """Yield (header, block) tuples using remaining_blocks = blocks after this block."""

    total_len = len(data)
    offset = 0

    while offset < total_len:
        block = data[offset:offset + max_block_size]
        block_size = len(block)
        bytes_remaining = total_len - (offset + block_size)
        remaining_blocks = (bytes_remaining + max_block_size - 1) // max_block_size
        header = struct.pack('>HH', block_size, remaining_blocks)

        yield header, block
        offset += block_size

class TCPClient:
    """TCP Client class to create connections and send data to/from a server using IPv4.
        It runs in non-blocking mode and processes events in its own daemon thread.
        Events (connected, disconnected, data received) are added to a queue
        for further processing by the calling process. """

    def __init__(self, description="TCP Client", queue=None, host=None, port=DEST_PORT, max_block_size=MAX_BLOCK_SIZE):
        """Initialize the TCP client with the given host and port.

            Parameters
                description: Description of the client
                queue: Queue to keep track of events
                host: Destination IP address
                port: Port number """
    
        self.description = description
        self.host = host if host is not None else resolve_default_host("TCP Client", fallback_host=DEFAULT_DEST_IP)
        self.port = port
        self.sel = selectors.DefaultSelector()

        self.started = True     # Flag to indicate if the client daemon thread is running
        self.connected = False  # Flag to indicate if the client is connected to a server

        self.recv_buffer = bytearray() # Buffer to store incoming data
        self.recv_msg = message.Message() # Message being received
 
        self.event_q = queue if queue else Queue() # Queue to keep track of events    
        self.max_block_size = max_block_size if max_block_size > 0 else MAX_BLOCK_SIZE
        self.last_result = -1  # Last result code from connect_ex()

        self._connect_lock = threading.RLock()  # Lock socket creation, connect, and disconnect as one state transition
        self._send_lock = threading.Lock()      # Lock to ensure thread-safe sending of messages

        # AF_INET: IPv4, SOCK_STREAM: TCP. The socket is registered with the
        # selector only after its non-blocking connection has completed.
        self.client_socket = None
        self._create_socket()

        # Start the event thread only after all state it accesses is initialized.
        self.event_handler = threading.Thread(target=self._process_events)
        self.event_handler.daemon = True
        self.event_handler.start()

    def _create_socket(self):
        """Create a new unconnected, non-blocking socket."""
        self._destroy_socket()  # Ensure any existing socket is destroyed before creating a new one

        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_socket.setblocking(False)
        self.connected = False  # Set the client to not connected

    def _destroy_socket(self):
        """Destroy the current socket and unregister it from the selector."""

        client_socket = self.client_socket
        self.client_socket = None
        self.connected = False

        if client_socket is not None:
            try:
                self.sel.unregister(client_socket)
            except (KeyError, ValueError):
                pass
            except Exception as e:
                logging.error(f"TCP Client {self.description} error unregistering socket: {e}")

            try:
                client_socket.close()
            except OSError as e:
                logging.error(f"TCP Client {self.description} error closing socket: {e}")

    def _process_connection(self):
        """Register a completed connection and publish its connect event."""

        self.sel.register(
            self.client_socket,
            selectors.EVENT_READ,
            data=message.Message(),
        )

        event = events.ConnectEvent(local_sap=self, remote_conn=self.client_socket, remote_addr=(self.host, self.port), timestamp=datetime.now())
        self.event_q.put(event)

        logging.info(f"TCP Client {self.description} connected to host {self.host} port {self.port}")

    def _schedule_retry(self):
        """Schedule one reconnect attempt unless one is already pending."""

        if not self.started or Timer.manager is None:
            return

        timer_name = f"TCPClient-{self.description}"
        if Timer.manager.get_timers_by_name(timer_name):
            return

        self.retry_timer = Timer(
            timer_name,
            self.event_q,
            5000,
            user_callback=lambda x: self.connect(),
        )

    def _process_disconnect(self, expected_socket=None):
        """Atomically close the current connection and schedule one retry.

        ``expected_socket`` prevents a stale selector event from disconnecting
        a newer socket created by a concurrent retry.
        """

        with self._connect_lock:
            if expected_socket is not None and expected_socket is not self.client_socket:
                return

            client_socket = self.client_socket
            was_connected = self.connected
            self._destroy_socket()
            self.recv_buffer = bytearray()
            self.recv_msg = message.Message()

            if was_connected or client_socket is not None:
                event = events.DisconnectEvent(
                    local_sap=self,
                    remote_conn=None,
                    remote_addr=(self.host, self.port),
                    timestamp=datetime.now(),
                )
                self.event_q.put(event)

            self._schedule_retry()

        logging.info(f"TCP Client {self.description} disconnected from host {self.host} port {self.port}")

    def _process_msg(self, client_socket, msg):

        """Process incoming msg events on the client socket in non-blocking mode."""

        if client_socket is not self.client_socket or client_socket.fileno() == -1:
            logging.error(f"TCP Client {self.description} socket is invalid. Cannot receive message.\n{msg}")
            return

        try:
            data = client_socket.recv(MAX_BLOCK_SIZE)  # non-blocking, might return 0..MAX_BLOCK_SIZE bytes
        except BlockingIOError:
            return  # no data ready
        except (ConnectionResetError, OSError) as e:
            logging.error(f"TCP Client {self.description} socket connection reset / OSError. Cannot receive message.\n{msg}")
            self._process_disconnect(expected_socket=client_socket)
            return

        # Check if the connection has been closed i.e. zero bytes received
        if not data:
            self._process_disconnect(expected_socket=client_socket)
            return

        # Append data to the receive buffer
        self.recv_buffer.extend(data)

        # Try to parse all complete blocks
        while True:
            # Need at least 4 bytes for header
            if len(self.recv_buffer) < 4:
                break

            block_size, remaining_blocks = struct.unpack('>HH', self.recv_buffer[:4])

            # Check if a full block has arrived
            if len(self.recv_buffer) < 4 + block_size:
                break  # wait for at least one block of data

            # Extract one block following the 4 byte header
            block = bytes(self.recv_buffer[4:4 + block_size])

            # Trim from buffer
            del self.recv_buffer[:4 + block_size]

            # Add block to message
            self.recv_msg.msg_data.extend(block)

            # If last block -> full message complete
            if remaining_blocks == 0:

                msg = message.Message()
                msg.from_data(self.recv_msg.msg_data)

                event = events.DataEvent(
                    local_sap=self, remote_conn=client_socket, remote_addr=(self.host, self.port), data=msg.msg_data, timestamp=datetime.now())
                self.event_q.put(event)
                self.recv_msg = message.Message()  # Reset for next message

                logger.debug(f"TCP Client {self.description} received message from {self.host} port {self.port} Message:\n{msg}")

    def _process_events(self):
        """ Process events in a loop until the client is stopped. """
        
        # While the client has started, keep processing events
        while self.started:
                events = self.sel.select(timeout=1) # Wait for events with a timeout specified in seconds
                
                if self.connected: # Only process events if connected to a server
                    for key, mask in events:

                        # key.data (state) should not be None as we associated a message instance with the socket   
                        if key.data is None:
                            raise XSoftwareFailure(f"TCP Client {self.description} no key data associated with the socket")
                        else:
                            try:
                                self._process_msg(key.fileobj, key.data)
                            except Exception as e:
                                logging.error(f"TCP Client {self.description} unhandled exception while processing events for {self.host} port {self.port} Data (hex): {key.data.msg_data.hex() if key.data.msg_data else ''} Exception: {e}")
                                self._process_disconnect(expected_socket=key.fileobj)
                                break

    def connect(self) -> int:
        """Establish a socket connection.
            Returns
                0 or EISCON if the connection was successful
                Error code if the connection failed"""

        with self._connect_lock:
            if not self.started:
                return errno.ECANCELED

            if self.connected:
                return self.last_result

            if self.client_socket is None or self.client_socket.fileno() == -1:
                logger.debug(f"TCP Client {self.description} socket is invalid, creating a new socket.")
                self._create_socket()

            logger.debug(f"TCP Client {self.description} attempting to connect to host {self.host} port {self.port}")

            self.last_result = self.client_socket.connect_ex((self.host, self.port)) # Attempt a connect to the server

            if self.last_result in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                _, writable, exceptional = select.select(
                    [],
                    [self.client_socket],
                    [self.client_socket],
                    CONNECT_TIMEOUT_SECONDS,
                )

                if writable or exceptional:
                    self.last_result = self.client_socket.getsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_ERROR,
                    )
                else:
                    self.last_result = errno.ETIMEDOUT

            if self.last_result in (0, errno.EISCONN):  # Success (0) or socket already connected (EISCONN)
                self.connected = True  
                self._process_connection()
            else:
                self.connected = False
                logging.error(
                    f"TCP Client {self.description} failed to connect to host {self.host} port {self.port} "
                    f"with error code {self.last_result}, {errno.errorcode.get(self.last_result)}, {os.strerror(self.last_result)}"
                )
                self._destroy_socket()
                self._schedule_retry()

            return self.last_result

    def send(self, msg: message.Message, client_socket=None):
        """Send a message to the server
            Parameters
                msg: Message to send
                client_socket: Dummy parameter for compatibility with server interface
        """

        with self._send_lock:  # Ensure that only one thread can send a message at a time

            time_enter = time.time()

            if not self.connected:
                logging.error(f"TCP Client {self.description} not connected to host {self.host} port {self.port}. Cannot send message.\n{msg}")
                return

            if not isinstance(msg, message.Message):
                logging.error(f"TCP Client {self.description} invalid message type. Expected message.Message, got {type(msg)}.\n{msg}")
                return

            if self.client_socket is None or self.client_socket.fileno() == -1:
                logging.error(f"TCP Client {self.description} socket is invalid. Cannot send message.\n{msg}")
                self.connected = False
                return

            data = None

            # Iterate over all connections and send the message
            for key in list(self.sel.get_map().values()):
                if key.data is not None:
                    try:
                        logger.debug(f"TCP Client {self.description} sending message to host {self.host} port {self.port}\n{msg}")

                        data = msg.to_data()  # Convert the message to bytes 

                        if data is None:
                            raise ValueError(f"TCP Client {self.description} Message to_data() returned None. Message not initialized correctly.\n{msg}")

                        total_len = len(data)
                        offset = 0

                        # If the message exceeds the maximum block size, set the socket to blocking mode temporarily
                        # This prevents "Resource temporarily unavailable" errors on large messages
                        if total_len > self.max_block_size:
                            key.fileobj.setblocking(True)

                        # Send the message in blocks with a header that tells the
                        # receiver how many blocks still follow this one.
                        for header, block in _iter_framed_blocks(data, self.max_block_size):
                            key.fileobj.sendall(header + block)

                        logger.debug(f"TCP Client {self.description} sent message to peer in {total_len // self.max_block_size + 1} blocks.\n{message.Message.__str__(msg)}")
                    except (OSError,  TimeoutError ) as e:
                        logger.error(f"TCP Client {self.description} OS error / timeout sending message to host {self.host} port {self.port}\n{e}")
                        self._process_disconnect(expected_socket=key.fileobj)
                    except (BrokenPipeError,ConnectionResetError) as e:
                        logger.error(f"TCP Client {self.description} connection reset / broken pipe error while sending message to host {self.host} port {self.port}\n{e}")
                        self._process_disconnect(expected_socket=key.fileobj)
                    except Exception as e:
                        logger.error(f"TCP Client {self.description} general exception sending message to host {self.host} port {self.port}\n{e}")
                        self._process_disconnect(expected_socket=key.fileobj)
                    finally:
                        # If the message exceeds the maximum block size i.e. we entered blocking mode, return the socket to non-blocking mode
                        if total_len > self.max_block_size:
                            try:
                                key.fileobj.setblocking(False)  # Ensure the socket is set back to non-blocking mode
                            except Exception as e:
                                logger.error(f"TCP Client {self.description} socket not valid anymore while setting non-blocking mode while sending message to host {self.host} port {self.port}\n{e}")

            time_exit = time.time()
            logger.debug(f"TCP Client {self.description} SEND {len(data) if data is not None else 'unknown'} bytes duration: {(time_exit - time_enter)*1000:.2f} ms")
    
    def nrConnections(self):
        """Return the number of connections to the server."""
        return len(self.sel.get_map()) - 1 # Subtract 1 for the client socket itself

    def disconnect(self):
        """Disconnect if currrently connected to the server."""
        for key in list(self.sel.get_map().values()):
            if key.data is not None:
                self._process_disconnect()

        logging.error(f"TCP Client {self.description} disconnected from {self.host} port {self.port}")

    def stop(self):
        """Stop the TCP client and close connections."""
        if not self.started:
            logging.warning(f"TCP Client {self.description} already stopped on host {self.host} port {self.port}")
            return

        self.started = False

        # Unregister all sockets
        for key in list(self.sel.get_map().values()):  # Create a copy of the selector values as it may change
            if key.data is not None:
                self._process_disconnect(expected_socket=key.fileobj)
            else:
                self.sel.unregister(key.fileobj)

        # A socket whose connection is still in progress is intentionally not
        # registered with the selector, so close that socket explicitly too.
        with self._connect_lock:
            self._destroy_socket()

        # Stop the event handler thread
        if self.event_handler.is_alive():
            self.event_handler.join()
        
        self.sel.close() # Close the selector
        logging.info(f"TCP Client {self.description} stopped connecting to host {self.host} port {self.port}")

    def recv_all(self, socket, n):
        """Receive exactly n bytes from the socket."""
        data = b''
        while len(data) < n:
            packet = socket.recv(n - len(data))
            if not packet:
                # Connection closed
                return data if data else None
            data += packet
        return data

if __name__ == "__main__":

    # Setup logging configuration
    logging.basicConfig(
        level=logging.DEBUG,  # Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format="%(asctime)s - %(levelname)s - %(message)s",  # Log format
        handlers=[
            logging.StreamHandler(),                     # Log to console
            logging.FileHandler("client.log", mode="a")  # Log to a file
            ]
    )

    arg_parser = argparse.ArgumentParser(description="set_debug")
    arg_parser.add_argument("--host", type=str, required=False, help="TCP server host",default="localhost")
    arg_parser.add_argument("--port", type=int, required=False, help="TCP server port", default=50000)
    arg_parser.add_argument("--from_id", type=str, required=False, help="From System ID", default="tm")
    arg_parser.add_argument("--to_id", type=str, required=False, help="To System ID", default="dig")
    arg_parser.add_argument("--entity", type=str, required=False, help="Entity ID", default=None)
 
    set_sample_rate_apicall = {}
    set_sample_rate_apicall["msg_type"] = "req"
    set_sample_rate_apicall["action_code"] = "set"
    set_sample_rate_apicall["property"] = "sample_rate"
    set_sample_rate_apicall["value"] = 2.4e6

    get_sample_rate_apicall = {}
    get_sample_rate_apicall["msg_type"] = "req"
    get_sample_rate_apicall["action_code"] = "get"
    get_sample_rate_apicall["property"] = "sample_rate"

    set_center_freq_apicall = {}
    set_center_freq_apicall["msg_type"] = "req"
    set_center_freq_apicall["action_code"] = "set"
    set_center_freq_apicall["property"] = "center_freq"
    set_center_freq_apicall["value"] = 1420.40e6

    get_auto_gain_apicall = {}
    get_auto_gain_apicall["msg_type"] = "req"
    get_auto_gain_apicall["action_code"] = "method"
    get_auto_gain_apicall["method"] = "get_auto_gain"
    get_auto_gain_apicall["params"] = {"sample_rate": 2.4e6, "time_in_secs": 1}

    set_gain_apicall = {}
    set_gain_apicall["msg_type"] = "req"
    set_gain_apicall["action_code"] = "set"
    set_gain_apicall["property"] = "gain"
    set_gain_apicall["value"] = 25

    read_samples_apicall = {}
    read_samples_apicall["msg_type"] = "req"
    read_samples_apicall["action_code"] = "method"
    read_samples_apicall["method"] = "read_samples"
    read_samples_apicall["params"] = {} # No parameters required for this method

    api_msg = message.APIMessage()

    queue = Queue()

    Timer.manager = TimerManager()
    Timer.manager.start()

    class Driver:
        def __init__(self):
            self.app_name = "tm"
            pass

        def get_interface(self, system_name):

            from api.tm_dig import TM_DIG

            if system_name in ["tm", "dig"]:
                return (TM_DIG(), None, False)  # No entity driver associated with this interface
            else:
                raise XSoftwareFailure(f"Driver has no interface for system {system_name}")

    test1 = AppProcessor(name="Test1", event_q=queue, driver=Driver())
    test1.start()

    # Start the TCP client and connect to the server

    client = TCPClient(queue=queue, host=arg_parser.parse_args().host, port=arg_parser.parse_args().port)
    client.connect()
    
    time.sleep(1)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=set_sample_rate_apicall
    )

    client.send(api_msg)

    time.sleep(1)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=get_sample_rate_apicall
    )

    client.send(api_msg)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=set_center_freq_apicall
    )

    client.send(api_msg)

    time.sleep(1)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=get_auto_gain_apicall
    )

    client.send(api_msg)

    time.sleep(60)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=read_samples_apicall
    )

    client.send(api_msg)

    time.sleep(10)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=arg_parser.parse_args().from_id,
        to_system=arg_parser.parse_args().to_id,
        entity=arg_parser.parse_args().entity,
        api_call=set_gain_apicall
    )

    client.send(api_msg)

    time.sleep(100)
    client.stop()    
    
    AppProcessor.stop_all()
    Timer.manager.stop()
