"""Local QEMU GDB inspection. Breakpoints are QEMU traps, not flash patches."""
import socket
import struct
import select
import time


class Debugger:
    def __init__(self, backend):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(backend.directory / 'gdb.sock'))
        self.packet('qSupported')

    def packet(self, command):
        self.send_packet(command)
        return self.receive_packet()

    def send_packet(self, command):
        """Begin an RSP request; receive_packet must consume its reply next."""
        data = command.encode('ascii')
        self.sock.sendall(b'$' + data + b'#' + f'{sum(data) & 255:02x}'.encode())

    def receive_packet(self, *, timeout=None, cancelled=None, on_wait=None,
                       poll_interval=.1):
        deadline = None if timeout is None else time.monotonic() + timeout

        def receive(size):
            if deadline is None:
                return self.sock.recv(size)
            while True:
                if cancelled is not None and cancelled():
                    raise InterruptedError('UI session stopped')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Firmware did not reach its debugger checkpoint within the observation limit')
                if select.select([self.sock], [], [], min(poll_interval, remaining))[0]:
                    return self.sock.recv(size)
                if on_wait is not None:
                    # The RSP reply is still pending. Callbacks may use the
                    # separate QMP display connection, never this GDB stream.
                    on_wait()

        while True:
            byte = receive(1)
            if not byte:
                raise RuntimeError('GDB connection closed')
            if byte == b'$':
                break
        result = bytearray()
        while True:
            byte = receive(1)
            if not byte:
                raise RuntimeError('GDB connection closed')
            if byte == b'#':
                break
            result.extend(byte)
        checksum = bytearray()
        while len(checksum) < 2:
            byte = receive(2 - len(checksum))
            if not byte:
                raise RuntimeError('GDB connection closed')
            checksum.extend(byte)
        if int(checksum, 16) != sum(result) & 255:
            raise RuntimeError('GDB checksum mismatch')
        self.sock.sendall(b'+')
        # RSP run-length encoding is legal in memory/register responses.
        decoded = bytearray()
        i = 0
        while i < len(result):
            if result[i] == ord('*'):
                decoded.extend(bytes([decoded[-1]]) * (result[i + 1] - 29))
                i += 2
            else:
                decoded.append(result[i]); i += 1
        return decoded.decode('ascii')

    def breakpoint(self, address, enabled=True, thumb=False):
        response = self.packet(f'{"Z" if enabled else "z"}0,{address:x},{2 if thumb else 4}')
        if response != 'OK':
            raise RuntimeError(response)

    def run(self):
        return self.packet('c')

    def step_over_breakpoint(self, address, thumb=False):
        self.breakpoint(address, enabled=False, thumb=thumb)
        self.packet('s')
        self.breakpoint(address, thumb=thumb)

    def registers(self):
        return struct.unpack('<16I', bytes.fromhex(self.packet('g'))[:64])

    def read(self, address, size):
        result = bytearray()
        for offset in range(0, size, 1024):
            reply = self.packet(f'm{address + offset:x},{min(1024, size - offset):x}')
            if reply.startswith('E'):
                raise RuntimeError(reply)
            result.extend(bytes.fromhex(reply))
        return bytes(result)

    def close(self):
        self.sock.close()
