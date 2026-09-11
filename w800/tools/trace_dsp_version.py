"""Observe original ARM receipt of the DSP version HLE reply via local QEMU."""
import json
import socket
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger

POINTS = {0x688:'DSP IRQ', 0x44736540:'DSP DMA event',
          0x44736520:'DMA allocation release', 0x447362a8:'Mailbox RX dispatch',
          0x447b78e4:'Version OS message', 0x447b7c06:'Original version reader',
          0x44858c38:'DMA configuration', 0x4490d028:'DSP RX block start',
          0x448d52a2:'Original stream open response',
          0x447367a8:'DSP queued request'}

def main():
    rows=[]
    with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
        d=Debugger(q);d.sock.settimeout(5)
        for pc in POINTS:d.breakpoint(pc,thumb=True)
        try:
            for _ in range(60):
                d.run();r=d.registers();pc=r[15]
                row={'pc':hex(pc),'event':POINTS.get(pc,'Unexpected stop'),
                     'regs':[hex(v) for v in r]}
                if pc in (0x447b78e4,0x447362a8):
                    pointer=r[1] if pc==0x447b78e4 else r[2]
                    row['payload']=d.read(pointer,224).hex()
                rows.append(row);print(row['event'],row['regs'][:4],flush=True)
                if pc==0x447367a8:
                    row['payload']=d.read(r[1],32).hex()
                if pc==0x448d52a2:break
                if pc not in POINTS:break
                d.step_over_breakpoint(pc,thumb=True)
        except socket.timeout:
            print('No new event within five seconds.',flush=True)
        finally:
            q.command('stop')
            report={'events':rows,'version_reader_reached':any(r['pc']=='0x447b7c06' for r in rows),
                    'diagnostics':q.diagnostics(),
                    'registers':q.command('human-monitor-command',{'command-line':'info registers'})}
            d.close()
    (ROOT/'reports/dsp-version-hle.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
