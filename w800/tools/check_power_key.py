"""Verify that a QMP power-key edge reaches the original Vincenne IRQ task."""
import argparse
import json
import time
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flash', help='Local prepared flash including GDFS')
    args=parser.parse_args()
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                     flash_path=args.flash) as q:
        debug=Debugger(q)
        # Wait until the original power task has completed initialization.
        debug.breakpoint(0x44ace614, thumb=True)
        debug.run()
        pc=debug.registers()[15]
        if pc != 0x44ace614:
            raise RuntimeError(f'Power initialization was not reached: PC={pc:#x}')
        debug.breakpoint(0x44ace614, False, thumb=True)
        q.command('cont')
        time.sleep(.3)
        q.command('stop')
        debug.breakpoint(0x44ace958, thumb=True)
        q.command('cont')
        q.command('input-send-event', {'events': [{
            'type': 'key', 'data': {'down': True, 'key': {
                'type': 'qcode', 'data': 'power'}}}]})
        deadline=time.monotonic()+2
        while q.command('query-status')['running'] and time.monotonic()<deadline:
            time.sleep(.01)
        q.command('stop')
        regs=debug.registers()
        report={'irq_task_pc':hex(regs[15]), 'irq_task_reached':regs[15]==0x44ace958,
                'pmic_cache':debug.read(0x4c0617cc,24).hex(),
                'startup_events':debug.read(0x4c0618a7,6).hex()}
        if report['irq_task_reached']:
            debug.breakpoint(0x44ace958,False,thumb=True)
            debug.breakpoint(0x44aceb68,thumb=True)
            debug.run()
            regs=debug.registers()
            report['subscriber_dispatch_pc']=hex(regs[15])
            report['subscriber_events']=hex(regs[0])
            report['pmic_cache_after_irq']=debug.read(0x4c0617cc,24).hex()
            if regs[15] != 0x44aceb68:
                raise RuntimeError('Original PMIC worker did not dispatch the press')
            debug.breakpoint(0x44aceb68,False,thumb=True)
            # Watch the actual platform power-off request while holding the
            # physical key; do not equate a host process stop with phone off.
            debug.breakpoint(0x44b0d874,thumb=True)
            q.command('cont')
            time.sleep(3)
            q.command('stop')
            regs=debug.registers()
            report['hold_stop_pc']=hex(regs[15])
            report['platform_poweroff_requested']=regs[15]==0x44b0d874
            debug.breakpoint(0x44b0d874,False,thumb=True)
            debug.breakpoint(0x44ace958,thumb=True)
            q.command('cont')
            q.command('input-send-event', {'events': [{
                'type': 'key', 'data': {'down': False, 'key': {
                    'type': 'qcode', 'data': 'power'}}}]})
            deadline=time.monotonic()+2
            while q.command('query-status')['running'] and time.monotonic()<deadline:
                time.sleep(.01)
            q.command('stop')
            regs=debug.registers()
            report['release_irq_pc']=hex(regs[15])
            report['release_irq_reached']=regs[15]==0x44ace958
            if report['release_irq_reached']:
                debug.breakpoint(0x44ace958,False,thumb=True)
                debug.breakpoint(0x44aceb68,thumb=True)
                debug.run()
                regs=debug.registers()
                report['release_dispatch_pc']=hex(regs[15])
                report['release_subscriber_events']=hex(regs[0])
                report['pmic_cache_after_release']=debug.read(0x4c0617cc,24).hex()
        debug.close()
        print(json.dumps(report,indent=2))
        (ROOT/'reports/power-key-guest.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
