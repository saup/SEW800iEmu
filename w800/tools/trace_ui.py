"""Observe original InitBook callbacks and real key/frame changes; no guest patches."""
import argparse, json, time, hashlib, struct
from pathlib import Path
from PySide6.QtGui import QImage
from w800.backend import QemuBackend,ROOT
from w800.tools.debug import Debugger

PAGES={
    0x44d9f9ce:'KGEN mask-ROM API call',
    **{pc:f'Radio activation {pc:08x}' for pc in [0x44c8e604,0x44c8e624,0x44c8e63e,0x44c8e640,0x4475fdbc,0x4475fe08,0x4496e4c4,0x4498de8a,0x4498de98,0x4498dc2c,0x4498dc40,0x449477f4]},
    **{pc:f'MemStick {pc:08x}' for pc in [0x4487f268,0x4487f27c,0x4487f2a4,0x4487f2ac,0x4487f2b0,0x4487f2c6,0x4487f2d0,0x4487f3c8,0x4487f3e8,0x4487f3ec,0x4487f3f4,0x4487f3fc,0x4487f402]},
    **{pc:f'FSU {pc:08x}' for pc in [0x44a9954c,0x44a99560,0x44a99574,0x44a995c0,0x44a995c8,0x44a995da,0x44a99682,0x44a996be,0x44a9973c,0x44a996b8,0x4498fbe6,0x4498fbec,0x4498fbf0]},
    **{pc:f'Pre-MMI init {pc:08x}' for pc in [0x450db1d8,0x450db1f8,0x450db206,0x450db20e,0x450db216,0x450db024,0x450db050,0x450db06e,0x450db070,0x450db07e,0x450db080,0x450db096,0x450db098,0x450db0c2,0x450db0d4,0x450db0de,0x450db0e0,0x450db0ea,0x450db0f2,0x450db14e,0x450dae70,0x450dae84,0x450daecc,0x450daece,0x450daef0,0x450daefa,0x450daf04,0x450daf26,0x450daf28,0x450daf7c,0x44c159b8,0x44c159ba,0x44735052,0x44735066,0x447350ae,0x447350b2,0x44735160,0x44ad81d4,0x44ad81d8,0x44ad81e4,0x44ad81e8]},
    **{pc:f'Startup manager {pc:08x}' for pc in [0x44f772b4,0x44f772c2,0x44f772c8,0x44f772d6,0x44f772e4,0x44f772f0,0x44f772f6,0x44f772fc,0x44f77314,0x44f7734e,0x44f77374,0x44f77386,0x44f77630,0x44f774ac,0x44f77504,0x44f77506,0x44f77540,0x44f7708a,0x44f77212,0x44f77004,0x44f770a0,0x44f77158]},
    **{pc:f'UI init checkpoint {pc:08x}' for pc in [0x44d22c7c,*range(0x44d22c82,0x44d22cb6,4)]},
    0x44d22c64:'UI init entry',
    0x44e91cc0:'InitBook constructor',0x44d22cb2:'InitBook constructor caller',
    0x44e917b0:'StartUp enter',0x44e917b4:'LockedMode enter',
    0x44e91830:'Customization enter',0x44e91858:'Customization progress',
    0x44e91978:'UserGreeting enter',0x44e918d4:'Started enter',
    0x44e91940:'WaitForSIM active',0x44e919b8:'MMIInit enter',
    0x44e91a88:'MMIInit event30a7',0x44e91a6c:'MMIInit exit',
    0x44e91aa4:'Animation enter',0x44e91abc:'Animation timer',
    0x44e91b2c:'StartupMode enter',0x44e91b98:'FastReset enter',
    0x44e9196c:'Base SIM active',0x44e91974:'Base SIM event',
    0x44e917dc:'StartUp event',0x44e917f4:'LockedMode event',
    0x4493d02c:'FS type marker result',0x4493d052:'FS version marker result',
    0x44ab88b4:'PDI completion IRQ',0x44a8f94c:'First app launch ok',
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flash',default=str(ROOT/'firmware/prepared/flash-gdfs.bin'))
    parser.add_argument('--idle-seconds',type=float,default=3,help='Bounded observation after the last startup callback')
    a=parser.parse_args();events=[];frames=[]
    with QemuBackend(exploratory=True,debug=True,trace=True,mmio_limit=1000000,flash_path=a.flash) as q:
        d=Debugger(q);d.sock.settimeout(min(30,max(.1,a.idle_seconds)))
        for pc in PAGES:d.breakpoint(pc,thumb=True)
        try:
            for _ in range(500):
                stop=d.run();r=d.registers();pc=r[15]
                event={'pc':hex(pc),'label':PAGES.get(pc),'stop':stop,'regs':[hex(x) for x in r]}
                if pc in [0x44a995da,0x44a996b8,0x4487f3fc,0x4487f402]:
                    event['stack_bytes']=d.read(r[13],48).hex()
                    offset=8 if pc==0x44a995da else (4 if pc==0x44a996b8 else 0)
                    msg=struct.unpack('<I',d.read(r[13]+offset,4))[0]
                    if 0x4c000000 <= msg < 0x4c800000:event['message_bytes']=d.read(msg,48).hex()
                events.append(event);print(json.dumps(event),flush=True)
                if pc==0x44ab88b4 and sum(e['pc']==hex(pc) for e in events)==3:
                    QImage(str(q.framebuffer_path())).save(str(ROOT/'reports/ui-logo-original.png'))
                if pc not in PAGES:break
                d.step_over_breakpoint(pc,thumb=True)
        except TimeoutError:pass
        finally:q.command('stop');d.close()
        q.command('cont')
        def capture(label):
            im=QImage(str(q.framebuffer_path()))
            im.save(str(ROOT/f'reports/ui-frame-{label}.png'))
            body=im.constBits().tobytes()
            frames.append({'label':label,'sha256':hashlib.sha256(body).hexdigest(),
                           'colors':len({im.pixel(x,y) for x in range(im.width()) for y in range(im.height())})})
        capture('initial')
        for key in ['soft_left','select','back','walkman','5']:
            q.send_key(key,True);time.sleep(.12);q.send_key(key,False);time.sleep(.2);capture(key)
        q.command('stop')
        (ROOT/'reports/ui-startup-trace.log').write_bytes((q.directory/'trace.log').read_bytes())
        report={'flash':str(a.flash),'events':events,'frames':frames,'snapshot':q.snapshot()}
    (ROOT/'reports/ui-startup-research.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(frames,indent=2))
if __name__=='__main__':main()
