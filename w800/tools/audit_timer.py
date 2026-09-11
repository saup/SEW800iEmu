"""Read-only original guest OS timer observations over private QEMU sockets."""
import json, struct, time
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger

def main():
    rows=[]
    with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
        d=Debugger(q); d.breakpoint(0x447b8238,thumb=True);d.run()
        pc=d.registers()[15]
        if pc != 0x447b8238: raise RuntimeError(f'Post-loader stage not reached: {pc:#x}')
        d.breakpoint(0x447b8238,False,thumb=True)
        for elapsed in range(5):
            q.command('cont');time.sleep(1);q.command('stop')
            r=d.registers()
            row={'host_interval':elapsed+1,'pc':hex(r[15]),'registers':{}}
            for a in [0xf9010000,0xf9010004,0xf9010008,0xf901001c,0xfb020100,0xfb020104,0xfb020200,0xfb020204,0xfb020300,0xfb020304,0x4c07eb3c,0x4200f264,0x4c07d938,0x4c07d93c,0xf9050000]:
                row['registers'][hex(a)]=hex(struct.unpack('<I',d.read(a,4))[0])
            row['qmp_status']=q.command('query-status')
            rows.append(row)
        d.close()
    print(json.dumps(rows,indent=2));(ROOT/'reports/timer-runtime.json').write_text(json.dumps(rows,indent=2)+'\n')
if __name__=='__main__':main()
