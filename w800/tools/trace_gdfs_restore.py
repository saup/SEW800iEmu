"""Audit the original filesystem-to-GDFS restore without changing guest state.

Payloads are represented by hashes; the report records unit numbers and original
return codes so storage failures can be distinguished from rejected records.
"""
import hashlib
import json
import time
from w800.backend import QemuBackend,ROOT
from w800.tools.debug import Debugger


def main():
    points={0x44c8e27c:'File restore entry',0x44c8e2c0:'Write unit',
            0x44c8e2c4:'Write result',0x44c8e344:'Delete unit',
            0x44c8e348:'Delete result',0x44c8e3ba:'Underlying write result',
            0x44c8e43c:'Underlying delete call',0x44c8e43e:'Underlying delete result',
            0x44c8e33e:'File payload short read',0x44c8e370:'File restore return'}
    events=[]
    with QemuBackend(exploratory=True,debug=True,mmio_limit=200000) as q:
        d=Debugger(q);d.sock.settimeout(3)
        d.breakpoint(0x44c8e27c,thumb=True)
        start=time.monotonic()
        try:
            for _ in range(3000):
                stop=d.run();r=d.registers();pc=r[15]
                event={'pc':hex(pc),'label':points.get(pc),'r0':hex(r[0])}
                if pc==0x44c8e27c:
                    for address in points:
                        if address!=pc:d.breakpoint(address,thumb=True)
                    for name,address in [('directory',r[0]),('file',r[1])]:
                        raw=d.read(address,256)
                        end=next((i for i in range(0,256,2) if raw[i:i+2]==b'\0\0'),256)
                        event[name]=raw[:end].decode('utf-16le',errors='replace')
                if pc==0x44c8e2c0:
                    event.update(unit=r[0],size=r[2],sha256=hashlib.sha256(d.read(r[1],r[2])).hexdigest())
                if pc==0x44c8e344:event['unit']=r[0]
                if pc==0x44c8e43c:event.update(target=hex(r[2]),unit=r[1])
                events.append(event)
                print(json.dumps(event),flush=True)
                if pc not in points or pc==0x44c8e370 or time.monotonic()-start>30:break
                d.step_over_breakpoint(pc,thumb=True)
        except TimeoutError:
            events.append({'stop':'No restore breakpoint within three seconds'})
        finally:q.command('stop');d.close()
        report={'events':events,'snapshot':q.snapshot()}
    (ROOT/'reports/gdfs-restore-audit.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
