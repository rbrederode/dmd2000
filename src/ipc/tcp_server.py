#!/usr/bin/env python3

import selectors
import socket
import sys
import threading
import time
import struct
import traceback
from queue import Queue
from datetime import datetime

from ipc import message
from env import events
from util.timer import TimerManager, Timer
from util.util import resolve_default_host

import logging
logger = logging.getLogger(__name__)

DEFAULT_HOST_IP = "127.0.0.1"
HOST_PORT = 60000

MAX_BLOCK_SIZE = 65535   # Define a maximum block size for sending data (65,535 bytes to fit in 64KB packet)

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

class ConnectionState:
    """Class to hold the state of a connection including the receive buffer and message being constructed.
        The TCPServer associates an instance of this class with each client connection. """

    def __init__(self):
        self.recv_buffer = bytearray()
        self.recv_msg = message.Message()

class TCPServer:
    """TCP Server class to handle connections and data from/to clients using IPv4.
        It runs in non-blocking mode and processes events in its own daemon thread.
        Events (connected, disconnected, data received) are added to a queue
        for further processing by the calling process. """

    def __init__(self, description="TCP Server", queue=None, host=None, port=HOST_PORT, max_block_size=MAX_BLOCK_SIZE):
        """Initialize the TCP server with the given host and port.

            Parameters
                description: Description of the server
                queue: Queue to keep track of events
                host: Host IP address
                port: Port number """
    
        self.description = description
        self.host = host if host is not None else resolve_default_host("TCP Server", fallback_host=DEFAULT_HOST_IP)
        self.port = port
        self.sel = selectors.DefaultSelector()

        self.server_socket = None
        self._create_socket()

        self.event_handler = None # Thread to handle server socket events
        self.event_q = queue if queue else None # Queue to keep track of events   
        self.started = False # Flag to indicate if the server has been started or stopped

        self.max_block_size = max_block_size if max_block_size > 0 else MAX_BLOCK_SIZE

        self._send_lock = threading.Lock() # Lock to ensure thread-safe sending of messages

    def _create_socket(self):
        """Create a new socket and register it with the selector."""
        # AF_INET: IPv4, SOCK_STREAM: TCP
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Avoid bind() exception: OSError: [Errno 48] Address already in use
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.setblocking(False)  # Set the socket to non-blocking mode

    def _destroy_socket(self):
        """Destroy the server socket."""
        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None

    def _process_connection(self, listening_socket):
        """Accept incoming connection events an a listening socket (fileobj).
            Store the resulting client socket and address in a new ConnectEvent
        """

        # Accept the connection
        client_socket, addr = listening_socket.accept()
        client_socket.setblocking(False)

        # Create a new connection state containing an(empty) message & recv_buffer instance
        state = ConnectionState()

        # Register the connection with the selector for read events and associate the state with it
        # This allows the selector to monitor this particular connection for incoming data
        self.sel.register(client_socket, selectors.EVENT_READ, data=state)
        event = events.ConnectEvent(self, client_socket, addr, datetime.now())
        # Add the event to the queue for further processing
        self.event_q.put(event)

        logger.debug(f"{event}")

    def _process_disconnect(self, client_socket, peername=None):
        """Process a disconnect from a client and deregister the connection from the selector."""

        try:
            try:
                self.sel.unregister(client_socket)
            except (KeyError, ValueError):
                pass

            try:
                client_socket.close()
            except (OSError, ValueError):
                pass

        finally:
            # Create a disconnect event and add it to the queue
            event = events.DisconnectEvent(self, client_socket, peername if peername else "", datetime.now())
            self.event_q.put(event)

        logger.debug(f"{event}")

    def _process_msg(self, client_socket, state: ConnectionState):

        """Process incoming msg events from the client in non-blocking mode."""

        valid_client_socket, peername = self.validate_client_socket(client_socket)

        if not valid_client_socket:
            logger.error(f"TCP Server {self.description} invalid client socket provided. Cannot process message.")
            self._process_disconnect(client_socket, peername if peername else None)
            return

        try:
            data = client_socket.recv(MAX_BLOCK_SIZE)  # non-blocking, might return 0..MAX_BLOCK_SIZE bytes
        except BlockingIOError:
            return  # no data ready
        except (ConnectionResetError, OSError) as e:
            logging.exception(f"TCP Server {self.description} socket connection reset / OSError. Cannot process message.")
            self._process_disconnect(client_socket, peername)
            return

        # Check if the connection has been closed i.e. zero bytes received
        if not data:
            self._process_disconnect(client_socket, peername)
            return

        # Append data to the receive buffer
        state.recv_buffer.extend(data)

        # Try to parse all complete blocks
        while True:
            # Need at least 4 bytes for header
            if len(state.recv_buffer) < 4:
                break

            block_size, remaining_blocks = struct.unpack('>HH', state.recv_buffer[:4])

            # Check if a full block has arrived
            if len(state.recv_buffer) < 4 + block_size:
                break  # wait for at least one block of data

            # Extract one block following the 4 byte header
            block = bytes(state.recv_buffer[4:4 + block_size])
            # Trim from buffer
            del state.recv_buffer[:4 + block_size]

            # Add block to message
            state.recv_msg.msg_data.extend(block)

            # If last block -> full message complete
            if remaining_blocks == 0:

                msg = message.Message()
                msg.from_data(state.recv_msg.msg_data)

                event = events.DataEvent(
                    self, client_socket, peername,
                    msg.msg_data, datetime.now()
                )

                self.event_q.put(event)
                state.recv_msg = message.Message()  # Reset for next message

                logger.debug(f"TCP Server {self.description} received message on {self.host} port {self.port} from {peername} Message:\n{msg}")

    def _process_events(self):
        """ Process events in a loop until the server is stopped. """
        
        # While the server has started, keep processing events
        while self.started:

            # Wait for events with a timeout specified in seconds
            events = self.sel.select(timeout=1) 
            for key, mask in events:

                # key.data is None for the listening socket (register with data=None)
                # key.data is the per-connection state instance associated with this client socket
                if key.data is None:
                    self._process_connection(key.fileobj)
                else:
                    try:
                        if mask & selectors.EVENT_READ:
                            self._process_msg(key.fileobj, key.data)
                        elif mask & selectors.EVENT_WRITE:
                            # Handle write events if needed
                            pass
                    except Exception as e:
                        logger.error(f"TCP Server {self.description} unhandled exception error on {self.host} port {self.port} from {key.fileobj.getpeername()} Data (hex): {key.data} Exception: {e}")

    def start(self):
        """Start the TCP server i.e. listen for incoming connections
            and start the event handler thread."""
        
        # Check if the server is already started
        if self.started:
            logger.warning(f"TCP Server {self.description} already started on host {self.host} port {self.port}")
            return
        
        self.started = True
        self.server_socket.listen()

        # Register the listening socket with the selector for read events where data is None
        # An EVENT.READ on this socket means an incoming connection request, and accept should be called

        self.sel.register(self.server_socket, selectors.EVENT_READ, data=None)

        logger.debug(f"TCP Server {self.description} started listening on host {self.host} port {self.port}")

        # Create & start a thread to handle events, set it as a daemon thread (killed when the main thread exits)
        self.event_handler = threading.Thread(target=self._process_events)
        self.event_handler.daemon = True 
        self.event_handler.start()

    def validate_client_socket(self, client_socket) -> (bool, str):
        """Check if the provided client socket is valid and connected to the server."""

        if client_socket is None or client_socket.fileno() == -1:
            logger.warning(f"TCP Server {self.description} invalid client socket detected on host {self.host} port {self.port}")
            return False, None

        if client_socket not in [key.fileobj for key in self.sel.get_map().values() if key.data is not None]:
            logger.warning(f"TCP Server {self.description} client socket not connected to server on host {self.host} port {self.port}")
            return False, None

        try:
            peername = client_socket.getpeername()
        except OSError:
            logger.warning(f"TCP Server {self.description} cannot get peername from client socket. Socket may be disconnected on host {self.host} port {self.port}")
            return False, None

        return True, peername

    def send(self, msg, client_socket=None):
        """Send a message to a specific connected client."""

        # Ensure that only one thread can send a message at a time to prevent interleaving of messages
        with self._send_lock:  

            # If no client socket is provided, send to the first connected client
            if client_socket is None:
                client_socket = next((key.fileobj for key in self.sel.get_map().values() if key.data is not None), None)

            valid_client_socket, peername = self.validate_client_socket(client_socket)
            
            if not valid_client_socket:
                logger.error(f"TCP Server {self.description} invalid client socket provided. Cannot send message.\n{msg}")
                return

            if not isinstance(msg, message.Message):
                logging.error(f"TCP Server {self.description} invalid message type provided. Expected 'message.Message', got {type(msg)}.")
                return

            try:
                data = msg.to_data()  # Convert the message to bytes 
                
                # If the message exceeds the maximum block size, set the socket to blocking mode temporarily
                # This prevents "Resource temporarily unavailable" errors on large messages
                total_len = len(data)
                if total_len > self.max_block_size:
                    client_socket.setblocking(True)

                # Send the message in blocks with a header that tells the
                # receiver how many blocks still follow this one.
                for header, block in _iter_framed_blocks(data, self.max_block_size):
                    client_socket.sendall(header + block)

                if total_len > self.max_block_size:
                    client_socket.setblocking(False)

                logger.debug(f"TCP Server {self.description} sent message to {peername} in {total_len // self.max_block_size + 1} blocks.\n{message.Message.__str__(msg)}")
            except (OSError, BrokenPipeError, TimeoutError, ConnectionResetError) as e:
                logger.error(f"TCP Server {self.description} error sending message to {peername}: {e}")
            except Exception as e:
                logger.error(f"TCP Server {self.description} error sending message to {peername}: {e}")
                self._process_disconnect(client_socket, peername)

    def broadcast(self, msg):
        """Send a message to all connected clients."""
        # Iterate over all connections and send the message
        for key in list(self.sel.get_map().values()):
            if key.data is not None:
                self.send(msg, key.fileobj)
    
    def nrConnections(self):
        """Return the number of connections to the server."""
        return len(self.sel.get_map()) - 1 # Subtract 1 for the server socket itself

    def disconnectAll(self):
        """Disconnect all clients currrently connected to the server."""
        for key in list(self.sel.get_map().values()):
            if key.data is not None:
                self._process_disconnect(key.fileobj)

        logger.error(f"TCP Server {self.description}: All clients disconnected from {self.host} port {self.port}")
        
    def stop(self):
        """Stop the TCP server and close all connections."""
        if not self.started:
            logger.warning(f"TCP Server {self.description} already stopped on host {self.host} port {self.port}")
            return

        # Unregister all sockets
        for key in list(self.sel.get_map().values()):  # Create a copy of the selector values as it may change
            if key.data is not None:
                self._process_disconnect(key.fileobj)
            else:
                self.sel.unregister(key.fileobj)

        self.started = False # Set the server to not started

        # Stop the event handler thread
        if self.event_handler.is_alive():
            self.event_handler.join()
        
        self.sel.close() # Close the selector
        logger.debug(f"TCP Server {self.description} stopped listening on host {self.host} port {self.port}")

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
    level=logging.INFO,  # Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    format="%(asctime)s - %(levelname)s - %(message)s",  # Log format
    handlers=[
        logging.StreamHandler(),                     # Log to console
        logging.FileHandler("server.log", mode="a")  # Log to a file
        ]
    )

    logging.getLogger().setLevel(logging.DEBUG)

    # Example usage of the TCPServer class    
    queue = Queue()

    Timer.manager = TimerManager()
    Timer.manager.start()

    msg = message.Message()
    msg.msg_data = b'Test message from TCP Server'

    server = TCPServer(queue=queue, host='192.168.0.2', port=65000)
    server.start()

    while True:

        while queue.empty():
            time.sleep(0.1)

        event = queue.get()
        print("Received event from queue:", event)
        server.send(msg, event.remote_conn)

    server.stop()    

    Timer.manager.stop()
