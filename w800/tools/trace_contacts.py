"""Observe the original Contacts activation using physical keypad input."""
import argparse
import json
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from .. import guest_ui
from .debug import Debugger
from .trace_messages import cstring

POINTS = {
    0x44db305c: 'PB task message',
    0x44db3128: 'PB native payload',
    0x44db343c: 'PB_ACTIVATE_REQ handler',
    0x44db4f2c: 'PB_INIT task message',
    0x44db5040: 'PB_INIT native payload',
    0x44db51a0: 'PB_INIT activation',
    0x44db53f8: 'PB_INIT state update',
    0x44903980: 'PBS no access indication',
    0x44903954: 'PBS active indication',
    0x44ddf02c: 'Contacts entry page',
    0x44ddf068: 'Contacts main page',
    0x44ddf07e: 'Contacts readiness query result',
    0x44ddf0fc: 'Contacts second page',
    0x450bc34c: 'Phonebook UI readiness query',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=5)
    parser.add_argument('--point', action='append', default=[])
    parser.add_argument('--initialize', choices=('activate', 'both', 'sort'))
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--complete-unavailable', action='store_true')
    parser.add_argument('--output', default='w800/reports/contacts-activation.json')
    args = parser.parse_args()
    for address in args.point:
        POINTS[int(address, 0)] = 'Additional original checkpoint'
    app = QGuiApplication.instance() or QGuiApplication([])
    samples = []

    class Observer(Debugger):
        def registers(self):
            regs = super().registers()
            pc = regs[15]
            diagnostic = pc in guest_ui.LOGGERS and any(
                n in cstring(self, regs[0]) for n in ('HPB:', 'PBS:', 'PB_', 'PBUI', 'Phonebook'))
            if (pc in POINTS or diagnostic) and len(samples) < 1500:
                item = {'pc': hex(pc), 'name': POINTS.get(pc, 'Phonebook diagnostic'),
                        'registers': [hex(n) for n in regs],
                        'stack': self.read(regs[13], 128).hex(),
                        'pb_init_state': self.read(0x4c06068c, 16).hex(),
                        'pbs_state': self.read(0x4c07eaa4, 32).hex()}
                item['objects'] = {hex(p): self.read(p, 64).hex()
                                   for p in regs[:7] if 0x4c000000 <= p < 0x4c7fffc0}
                if diagnostic:
                    item['format'] = cstring(self, regs[0])
                samples.append(item)
            return regs

    guest_ui.Debugger = Observer
    guest_ui.CHECKPOINTS.update(POINTS)
    session = guest_ui.OriginalUISession(start_screen='menu',
                                        progress=lambda s: print(s, flush=True))
    output = {}
    prefix = Path(args.output).with_suffix('')
    try:
        session.start()
        if args.complete_unavailable:
            from ..phonebook_services import install_unavailable_adapter
            install_unavailable_adapter(session)
        if args.initialize and not getattr(session, '_phonebook_initialized', False):
            context = session.word(0x4424552c)
            output['initialization'] = {'context': hex(context), 'requests': []}
            requests = {'activate': (0x450bb9c8,), 'sort': (0x450bb9e4,),
                        'both': (0x450bb9c8, 0x450bb9e4)}[args.initialize]
            for entry in requests:
                result = session.call_on_mmi(entry, context)
                output['initialization']['requests'].append({'entry': hex(entry), 'result': hex(result)})
                session.advance(1000)
        session.frame().save(str(prefix) + '-menu.png')
        for key in ('down', 'select'):
            session.advance(120, (key, True))
            session.advance(450, (key, False))
        session.frame().save(str(prefix) + '-opening.png')
        for _ in range(args.seconds):
            session.advance(1000)
        session.frame().save(str(prefix) + '-final.png')
        output['registers'] = [hex(n) for n in session.d.registers()]
        if args.interactive:
            import code
            def capture(label='interactive'):
                path = str(prefix) + '-' + label + '.png'
                session.frame().save(path)
                return path
            def press(key, count=1, delay=450):
                output.setdefault('input_sequence', []).append(
                    {'key': key, 'count': count, 'down_ms': 120, 'up_ms': delay})
                for _ in range(count):
                    session.advance(120, (key, True))
                    session.advance(delay, (key, False))
                return capture()
            def hold(key, milliseconds=1200):
                output.setdefault('input_sequence', []).append(
                    {'key': key, 'hold_ms': milliseconds})
                session.advance(100, (key, True))
                remaining = milliseconds - 100
                while remaining:
                    step = min(1000, remaining)
                    session.advance(step)
                    remaining -= step
                session.advance(450, (key, False))
                return capture()
            code.interact(local={'session': session, 'samples': samples,
                                 'output': output, 'press': press,
                                 'hold': hold, 'capture': capture})
    except Exception as error:
        output['error'] = f'{type(error).__name__}: {error}'
        print(output['error'], flush=True)
    finally:
        output['samples'] = samples
        output['report'] = session.report()
        if session.q:
            output['qemu_diagnostics'] = session.q.diagnostics()
        Path(args.output).write_text(json.dumps(output, indent=2) + '\n')
        session.close()
    print(args.output, flush=True)


if __name__ == '__main__':
    main()
