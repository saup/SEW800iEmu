"""Bridge a running W800 VM's service UART socket to a host serial PTY.

Flash tools (XS++, SETool2lite, FAR/JDFlasher) treat the DCU-60/flash cable
as a plain serial port in front of the phone's service UART. In the emulator
that UART is uart0.sock; this script exposes it as a PTY so host tools can
open it like a real cable.
"""
import argparse
import os
import pty
import selectors
import socket
import sys
import termios


def bridge(endpoint, uart=0):
    name = 'uartsif.sock' if uart == 'sif' else f'uart{uart}.sock'
    endpoint = endpoint / name if endpoint.is_dir() else endpoint
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(endpoint))
        master, slave = pty.openpty()
        name = os.ttyname(slave)
        tty_attr = termios.tcgetattr(slave)
        tty_attr[0] = 0; tty_attr[1] = 0; tty_attr[3] = 0  # raw: no iflag/oflag/lflag processing
        termios.tcsetattr(slave, termios.TCSANOW, tty_attr)
        os.close(slave)
        print(f'{name}', flush=True)
        print('PTTY above is the cable. Point the flash tool at it; Ctrl-C exits.',
              file=sys.stderr)
        selector = selectors.DefaultSelector()
        selector.register(sock, selectors.EVENT_READ)
        selector.register(master, selectors.EVENT_READ)
        sock.setblocking(False); os.set_blocking(master, False)
        while True:
            for key, _ in selector.select():
                try:
                    data = os.read(key.fileobj if key.fileobj != sock else sock.fileno(), 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                target = master if key.fileobj == sock else sock.fileno()
                try:
                    os.write(target, data)
                except (BlockingIOError, OSError):
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('endpoint', type=__import__('pathlib').Path,
                        help='session directory or uart0.sock path')
    parser.add_argument('--uart', default=0,
                        help='0, 1, 4, or sif (EROM flash/service UART)')
    args = parser.parse_args()
    bridge(args.endpoint, args.uart)


if __name__ == '__main__':
    main()
