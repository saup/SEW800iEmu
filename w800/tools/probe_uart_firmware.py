"""Observe actual UART traffic and native UI with host UARTs enabled."""
import json
import socket
import struct
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import trace_calls

def main():
    out=ROOT/'reports/uart-firmware'
    out.mkdir(exist_ok=True)
    with OriginalUISession(default_theme=True,start_phone_standby=True,uart=True) as s:
        press(s,'select')
        results=[]
        trace_calls(s,{0x44a98058,0x44a980f8,0x44a98198,0x44a97c94,0x447f4768,0x44a97e2c,0x44a98234,0x449ba698,0x449ba5d8,0x449ba640,0x447f4adc,0x44a3f738,0x44911bc4},'uart_native_driver')
        def native_state():
            return {'handles':s.d.read(0x4c049e28,7*0x3c).hex(), 'routing':s.d.read(0x4c049fd8,32).hex(), 'llrs232':s.d.read(0x4c06b668,160).hex(), 'rs232':s.d.read(0x4c048954,128).hex(), 'timer2':s.d.read(0x4c087e5c,28).hex(), 'timer_control':hex(s.word(0xf9010000)), 'timer_compare2':hex(s.word(0xf9010008)), 'irqmask1':hex(s.word(0xfb020204)), 'registers':s.d.read(0xfb060000+4,0x2c).hex()}
        initial_state=native_state()
        for port in (0,):
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
                peer.connect(str(s.q.uart_paths[port]));peer.setblocking(False)
                # Standard attention request only; no state-changing commands.
                peer.send(b'AT\r')
                data=b''
                for cycle in range(12):
                    if cycle in (3,7):peer.sendall(b'AT\r')
                    s.advance(500)
                    try:data+=peer.recv(4096)
                    except BlockingIOError:pass
                results.append({'port':port,'response_hex':data.hex(), 'registers': {hex(off): int.from_bytes(s.d.read({0:0xfb060000,1:0xfc040000,4:0xfb040000}[port]+off,2),'little') for off in (0x0e,0x12,0x14,0x2c,0x2e)}})
        press(s,'soft_right')
        s.frame().save(str(out/'menu-after-uart.png'))
        (out/'firmware-debug.log').write_text((s.q.directory/'firmware-debug.log').read_text())
        (out/'report.json').write_text(json.dumps({'uart':results,'report':s.report(),'native_state_initial':initial_state,'native_state_after':native_state()},indent=2))
        print(results,flush=True)
if __name__=='__main__':main()
