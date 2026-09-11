"""Observe original startup decisions using breakpoints and physical key inputs."""
import argparse
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hold',choices=['walkman','power'])
    parser.add_argument('--customization',action='store_true')
    args=parser.parse_args()
    points={0x44f77384:'Startup source call',0x44f77386:'Startup source result',
            0x44f77630:'Startup mode setter',0x44c8e604:'Radio activation wrapper',
            0x44c8e624:'Radio interface query result',0x44c8e640:'Radio activation result',
            0x44d22c64:'MMI process entry',0x44e91cc0:'InitBook constructor'}
    if args.customization:
        points.update({0x44c80862:'Customization upgrade marker lookup',
                       0x44c80866:'Upgrade marker result',
                       0x44c80874:'Customization status marker lookup',
                       0x44c80878:'Status marker result',
                       0x44c8089e:'Status marker open result',
                       0x44c808b6:'Status marker read result',
                       0x44c80988:'Customization GDFS marker read',
                       0x44c8098c:'Customization GDFS marker result',
                       0x44c82fee:'Customization mode result'})
    events=[]
    with QemuBackend(exploratory=True,debug=True,mmio_limit=200000) as q:
        d=Debugger(q);d.sock.settimeout(3)
        for pc in points:d.breakpoint(pc,thumb=True)
        first_stop=None
        if args.hold:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending=executor.submit(d.run)
                deadline=time.monotonic()+2
                while q.command('query-status')['status']!='running':
                    if pending.done() or time.monotonic()>deadline:
                        raise RuntimeError('Firmware stopped before the key could be pressed')
                    time.sleep(.001)
                q.send_key(args.hold,True)
                first_stop=pending.result(timeout=4)
        try:
            for _ in range(60):
                stop=first_stop if first_stop is not None else d.run()
                first_stop=None
                r=d.registers();pc=r[15]
                event={'pc':hex(pc),'label':points.get(pc),'stop':stop,
                       'registers':[hex(value) for value in r]}
                if pc in (0x44c80862,0x44c80874):
                    for name,pointer in [('directory',r[0]),('file',r[1])]:
                        raw=d.read(pointer,256)
                        end=next((i for i in range(0,len(raw),2) if raw[i:i+2]==b'\0\0'),len(raw))
                        event[name]=raw[:end].decode('utf-16le',errors='replace')
                if pc==0x44c808b6:event['status_bytes']=d.read(r[7],2).hex()
                if pc==0x44c8098c:event['gdfs_marker']=d.read(r[13]+4,1).hex()
                if pc==0x44f77384:
                    target=r[1]&~1
                    event['target_code']=d.read(target,128).hex()
                    points[target]='Startup source implementation'
                    d.breakpoint(target,thumb=bool(r[1]&1))
                    event['object_words']=[hex(x) for x in struct.unpack('<16I',d.read(r[0],64))]
                events.append(event)
                print(json.dumps(event),flush=True)
                if pc not in points:break
                d.step_over_breakpoint(pc,thumb=True)
        except TimeoutError:
            events.append({'stop':'No startup checkpoint within three seconds'})
        finally:
            q.command('stop');d.close()
        report={'held_key':args.hold,'events':events,'snapshot':q.snapshot()}
    suffix='customization' if args.customization else (args.hold or 'default')
    (ROOT/f'reports/boot-mode-{suffix}.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
