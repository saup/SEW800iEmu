"""Connect a terminal to a running W800 VM's private UART socket."""
import argparse
import os
from pathlib import Path
import selectors
import socket
import sys
import tempfile
import termios
import tty


def sessions():
    return sorted({p.parent for p in Path(tempfile.gettempdir()).glob('w800-*/uart*.sock')},key=lambda p:p.stat().st_mtime,reverse=True)


def terminal(endpoint):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.connect(str(endpoint))
        sock.setblocking(False)
        selector=selectors.DefaultSelector()
        selector.register(sock,selectors.EVENT_READ)
        selector.register(sys.stdin,selectors.EVENT_READ)
        original=termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
        pending=bytearray()
        input_closed=False
        print('Connected. Ctrl-] exits. Output is raw firmware UART data.',file=sys.stderr)
        try:
            if original: tty.setraw(sys.stdin.fileno())
            while True:
                for key,mask in selector.select():
                    if key.fileobj is sock:
                        if mask & selectors.EVENT_READ:
                            data=sock.recv(4096)
                            if not data:return
                            sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
                        if mask & selectors.EVENT_WRITE:
                            sent=sock.send(pending)
                            del pending[:sent]
                            if not pending:
                                selector.modify(sock,selectors.EVENT_READ)
                                if input_closed:sock.shutdown(socket.SHUT_WR)
                    else:
                        data=os.read(sys.stdin.fileno(),4096)
                        if b'\x1d' in data:return
                        if not data:
                            selector.unregister(sys.stdin)
                            input_closed=True
                            if not pending:sock.shutdown(socket.SHUT_WR)
                            continue
                        if len(pending)+len(data)>65536:
                            raise RuntimeError('UART output queue is full')
                        pending.extend(data)
                        selector.modify(sock,selectors.EVENT_READ|selectors.EVENT_WRITE)
        finally:
            if original:termios.tcsetattr(sys.stdin,termios.TCSADRAIN,original)
            selector.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list',action='store_true')
    p.add_argument('--session',type=Path,help='VM directory printed by --list')
    p.add_argument('--uart',type=int,choices=(0,1,4),default=0,help="UART0 is the native accessory/AT port (default)")
    args=p.parse_args()
    available=sessions()
    if args.list:
        for path in available:
            print(path)
            print('  UART sockets: '+', '.join(p.name for p in sorted(path.glob('uart*.sock'))))
            print('  Debugger-captured log: '+str(path/'firmware-debug.log'))
        if not available:print('No UART-enabled VM. Start or restart the phone GUI.')
        return
    if args.session is None:
        if len(available)!=1:p.error('Use --list and --session to select a running phone')
        args.session=available[0]
    try:terminal(args.session/f'uart{args.uart}.sock')
    except (OSError,RuntimeError) as exc:p.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
