"""Read original handsfree dependency creation results in a private QEMU VM."""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tools.probe_menu_services import trace_calls


class TracedSession(OriginalUISession):
    def invoke(self, *args, **kwargs):
        if not getattr(self, '_handsfree_traced', False):
            trace_calls(self, (0x44d6c54a, 0x44d6c59c, 0x44d6c5ce,
                              0x44d6c5d0, 0x44d6c5e8, 0x44d6c5ea,
                              0x44d6c5fe, 0x44d6c600, 0x44d6da34,
                              0x44cea310, 0x44cea324, 0x44cea328), 'handsfree_init')
            original_registers = self.d.registers
            def registers():
                regs = original_registers()
                rows = self.provenance['handsfree_init']
                if rows and int(rows[-1]['pc'], 16) == regs[15]:
                    from w800.components import descriptors
                    rows[-1]['bt_components'] = [r for r in descriptors(self) if r['id'] in (44, 45)]
                    pointer = regs[4]
                    if 0x4c000000 <= pointer <= 0x4c7fff00:
                        rows[-1]['object'] = self.d.read(pointer, 256).hex()
                return regs
            self.d.registers = registers
            self._handsfree_traced = True
        return super().invoke(*args, **kwargs)


def main():
    directory = ROOT / 'reports/menu-services-handsfree-init'
    directory.mkdir(parents=True, exist_ok=True)
    session = TracedSession(start_screen='menu', default_theme=True)
    try:
        session.start()
        for _ in range(25):
            session.advance(1000)
    except Exception as error:
        session.provenance['error'] = str(error)
    finally:
        (directory / 'trace.json').write_text(json.dumps(session.report(), indent=2) + '\n')
        for row in session.provenance.get('handsfree_init', []):
            print(row['pc'], row['arguments'], row['caller'], flush=True)
        print(session.provenance.get('error', 'completed'), flush=True)
        session.close()


if __name__ == '__main__':
    main()
