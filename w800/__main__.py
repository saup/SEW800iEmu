from pathlib import Path
import argparse
import json
import shutil
import time
from .backend import QemuBackend, ROOT

p = argparse.ArgumentParser(description='W800 original-firmware QEMU bring-up')
p.add_argument('command', choices=['boot', 'gui', 'ui', 'files'], nargs='?', default='ui')
p.add_argument('--seconds', type=float, default=2)
p.add_argument('--exploratory', action='store_true')
p.add_argument('--mask-rom', type=Path,
               help='Optional raw mask ROM image, 16–64 KiB in 4096-byte increments')
p.add_argument('--nor-otp', type=Path,
               help='Optional 18-byte raw NOR OTP image: little-endian words 0x80–0x88; requires --mask-rom')
p.add_argument('--chip-id', type=lambda value: int(value, 0), choices=(0x8000, 0x8040), default=0x8040,
               help='DB2010 chip ID: 0x8000 or 0x8040 (default); active only with --mask-rom')
p.add_argument('--mmio-limit', type=int, default=10000,
               help='Pause after this many unknown accesses (1000–1000000)')
a = p.parse_args()
if a.nor_otp is not None and a.mask_rom is None:
    p.error('--nor-otp requires --mask-rom')
if a.command == 'ui':
    if a.mask_rom is not None or a.nor_otp is not None:
        p.error('UI component startup uses the prepared R1L002 profile; use gui or boot for ROM inputs')
    from .ui_gui import main
    main()
elif a.command == 'files':
    from .filesystem_gui import main
    main()
elif a.command == 'gui':
    from .gui import main
    main(mask_rom_path=a.mask_rom, chip_id=a.chip_id, nor_otp_path=a.nor_otp)
else:
    if not .01 <= a.seconds <= 60: p.error('--seconds must be between 0.01 and 60')
    if not 1000 <= a.mmio_limit <= 1000000: p.error('--mmio-limit must be between 1000 and 1000000')
    with QemuBackend(trace=True, exploratory=a.exploratory, mmio_limit=a.mmio_limit,
                     mask_rom_path=a.mask_rom, chip_id=a.chip_id, nor_otp_path=a.nor_otp) as q:
        q.command('cont')
        deadline = time.monotonic() + a.seconds
        while time.monotonic() < deadline:
            time.sleep(.02)
            if not q.command('query-status')['running']: break
        running = q.command('query-status')['running']
        q.command('stop')
        report = q.snapshot()
        report['stop_reason'] = ('host time budget reached' if running else
            'internal mask-ROM bytes unavailable' if 'W800_MASK_ROM_UNAVAILABLE' in report['diagnostics'] else
            'unknown-access budget reached' if 'W800_MMIO_LIMIT' in report['diagnostics'] else
            'guest/device stop')
        report['time_budget_seconds'] = a.seconds
        report['mmio_limit'] = a.mmio_limit
        name = 'exploratory' if a.exploratory else 'strict'
        (ROOT / f'reports/{name}-boot.json').write_text(json.dumps(report, indent=2)+'\n')
        shutil.copy2(q.directory / 'trace.log', ROOT / f'reports/{name}-trace.log')
        print(json.dumps(report, indent=2))
