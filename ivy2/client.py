import serial
import queue
import threading
import time

# the amount of time without sending any messages before disconnecting (in seconds)
AUTO_DISCONNECT_TIMEOUT = 30


class ClientThread(threading.Thread):
    def __init__(self, receive_size=4096):
        super().__init__()

        self.receive_size = receive_size

        self.sock = None
        self.alive = threading.Event()

        self.outbound_q = queue.Queue()
        self.inbound_q = queue.Queue()

        self.disconnect_timer = threading.Timer(
            AUTO_DISCONNECT_TIMEOUT,
            self.disconnect
        )

    def connect(self, com_port):
        # Open the Windows virtual Bluetooth serial port.
        # read_timeout=0.1 makes read() return quickly when no data is ready.
        self.sock = serial.Serial(com_port, timeout=0.1)
        # Flush any stale bytes left in the buffer from previous connection attempts.
        self.sock.reset_input_buffer()
        self.alive.set()
        self.daemon = True
        self.start()

    def run(self):
        # Serial is a byte stream, not message-framed like RFCOMM sockets.
        # Buffer incoming bytes and emit one complete frame at a time so the
        # parser always receives exactly one message rather than a concatenated blob.
        FRAME_SIZE = 34
        recv_buffer = bytearray()

        while self.alive.is_set():
            # check that the port is still open
            if not self.sock or not self.sock.is_open:
                self.disconnect()
                break

            # send any outbound messages
            try:
                message = self.outbound_q.get(True, 0.1)

                self.sock.write(message)

                time.sleep(0.02)

                # reset the auto-disconnect timer
                self.disconnect_timer.cancel()
                self.disconnect_timer = threading.Timer(
                    AUTO_DISCONNECT_TIMEOUT,
                    self.disconnect
                )
            except queue.Empty:
                pass

            # receive incoming bytes into the buffer
            try:
                data = self.sock.read(self.receive_size)

                if data:
                    recv_buffer.extend(data)

                    # Emit one complete frame at a time into the inbound queue.
                    while len(recv_buffer) >= FRAME_SIZE:
                        self.inbound_q.put(bytes(recv_buffer[:FRAME_SIZE]))
                        recv_buffer = recv_buffer[FRAME_SIZE:]
            except (OSError, serial.SerialException):
                pass

    def disconnect(self, timeout=None):
        if self.sock and self.sock.is_open:
            self.sock.close()

        self.disconnect_timer.cancel()

        # unset the alive event so run() will not continue
        self.alive.clear()

        try:
            # block the calling thread until this thread completes
            threading.Thread.join(self, timeout)
        except RuntimeError:
            pass
