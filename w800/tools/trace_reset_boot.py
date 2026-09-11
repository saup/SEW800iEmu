"""Observe original boot twice with nonvolatile state retained across QEMU reset."""
import hashlib
import json
import struct
import time
from PySide6.QtGui import QImage
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger
from w800.tools.trace_messages import cstring, decode


def flash_digest(q):
    path=q.directory/'flash-reset-observation.bin'
    q.command('human-monitor-command',{'command-line':f'pmemsave 0x44000000 0x2000000 "{path}"'})
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    return digest


def observe(q, number):
    checkpoints={0x44d22c64:'MMI_Process entry',0x44e91cc0:'InitBook constructor',
                 0x44ab88b4:'LCD completion'}
    loggers={0x45169c70,0x44ad6474}
    messages=[];events=[];frames=[];calls=0;lcd=0
    reason='checkpoint budget'
    d=Debugger(q);d.sock.settimeout(3)
    for pc in {*loggers,*checkpoints}:d.breakpoint(pc,thumb=True)
    started=time.monotonic()
    try:
        for _ in range(11000):
            d.run();r=d.registers();pc=r[15]
            if pc in loggers:
                calls+=1
                fmt=cstring(d,r[0])
                args=list(r[1:4])+list(struct.unpack('<8I',d.read(r[13],32)))
                message=decode(d,fmt,args).strip()
                if any(token in message for token in ('MAPP:','[sysctrl]', '[SecLD]',
                           'StartupVoltageCheck:', 'GDFS_Wrapper:', 'ERRORS were found',
                           'FS: Partition', 'DSP Version', 'RTS ERROR')):
                    messages.append(message)
                    print(f'boot{number}: {message}',flush=True)
            elif pc in checkpoints:
                if pc==0x44ab88b4:
                    lcd+=1
                    if lcd==3:
                        path=ROOT/f'reports/reset-boot-{number}-logo.png'
                        QImage(str(q.framebuffer_path())).save(str(path))
                        frames.append(str(path))
                else:
                    events.append(checkpoints[pc])
                    print(f'boot{number}: {checkpoints[pc]}',flush=True)
            else:
                reason=f'guest stop at {pc:#x}';break
            d.step_over_breakpoint(pc,thumb=True)
            if time.monotonic()-started>45:
                reason='host time budget';break
    except TimeoutError:
        reason='no observed checkpoint within three seconds'
    finally:
        q.command('stop');d.close()
    return {'number':number,'stop':reason,'logger_calls':calls,
            'messages':messages,'events':events,'lcd_completions':lcd,
            'frames':frames,'flash_after':flash_digest(q),'snapshot':q.snapshot()}


def main():
    with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
        source_before=hashlib.sha256(q.flash_path.read_bytes()).hexdigest()
        first=observe(q,1)
        q.command('system_reset')
        after_reset=flash_digest(q)
        second=observe(q,2)
        source_after=hashlib.sha256(q.flash_path.read_bytes()).hexdigest()
    report={'boots':[first,second],'source_sha256':source_before,
            'source_unchanged':source_before==source_after,
            'flash_immediately_after_reset':after_reset,
            'reset_retained_flash':first['flash_after']==after_reset,
            'guest_changed_flash':first['flash_after']!=source_before}
    (ROOT/'reports/reset-boot-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='boots'},indent=2))


if __name__=='__main__':main()
