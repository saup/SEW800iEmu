"""Verify original guest IRQ -> matrix scan -> key-signal press/release.

Uses private QMP and GDB Unix sockets only. No guest bytes are changed.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import time
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


NAVIGATION_KEYS = (('1', 15), ('up', 24), ('down', 22), ('left', 21),
                   ('right', 20), ('ret', 23), ('f1', 5), ('f2', 6), ('esc', 7))


def capture(flash_path=None, keys=NAVIGATION_KEYS):
    evidence = []
    flash_path = flash_path or ROOT / 'firmware/prepared/flash-gdfs.bin'
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000, flash_path=flash_path) as q:
        d = Debugger(q)
        try:
            # Let the guest finish registering its keypad interrupt recipient.
            ready = 0x44e447be
            d.breakpoint(ready, thumb=True)
            d.run()
            assert d.registers()[15] == ready
            d.breakpoint(ready, enabled=False, thumb=True)
            for qcode, scan_code, down in [(key, code, down) for key, code in keys
                                          for down in (True, False)]:
                irq = 0x44912014
                d.breakpoint(irq, thumb=True)
                # RSP continue waits for a stop; QMP injects a physical switch edge.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    stopped = pool.submit(d.run)
                    time.sleep(.05)
                    q.command('input-send-event', {'events': [{
                        'type': 'key', 'data': {'down': down,
                        'key': {'type': 'qcode', 'data': qcode}}}]})
                    stopped.result(timeout=10)
                assert d.registers()[15] == irq
                evidence.append({'phase': 'irq', 'key': qcode, 'down': down, 'pc': hex(irq)})
                d.breakpoint(irq, enabled=False, thumb=True)
                dispatch = 0x44e44e08
                d.breakpoint(dispatch, thumb=True)
                d.run()
                registers = d.registers()
                assert registers[15] == dispatch, [hex(x) for x in registers]
                assert registers[0] == (0 if down else 1), registers
                assert registers[1] == scan_code, registers
                evidence.append({'phase': 'original_key_signal', 'key': qcode, 'down': down,
                                 'pc': hex(dispatch), 'event': registers[0],
                                 'scan_code': registers[1]})
                d.breakpoint(dispatch, enabled=False, thumb=True)
        finally:
            d.close()
    return evidence


if __name__ == '__main__':
    result = capture()
    target = ROOT / 'reports/keypad-guest-trace.json'
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
