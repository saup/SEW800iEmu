"""Keep the clean movie sampler and add native observations at a late underrun."""
import argparse
import json
import sys
import time

from w800 import guest_ui
from w800.backend import ROOT
from w800.tools import probe_video_realtime


POINTS = {0x448d52e4: 'credit received', 0x4488de8c: 'writer result',
          0x4488cb64: 'decoder status', 0x4488bf58: 'sync request',
          0x4488c658: 'queue input accepted'}


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--directory', default='video-realtime-refill-boundary')
    parser.add_argument('--trace-after-underruns', type=int, default=3)
    parser.add_argument('--trace-client', action='store_true')
    args, rest = parser.parse_known_args()
    directory = ROOT / 'reports' / args.directory
    directory.mkdir(parents=True, exist_ok=True)
    previous = guest_ui.CHECKPOINTS.copy()
    points = dict(POINTS)
    if args.trace_client:
        points[0x4495ee00] = 'sync sender'

    class Session(guest_ui.OriginalUISession):
        def start(self):
            result = super().start()
            self.refill_events = []
            self.refill_enabled = False
            self.trace_epoch = time.monotonic()
            original_registers = self.d.registers
            original_diagnostics = self.q.diagnostics

            def memory(pointer, count):
                return self.d.read(pointer, count).hex() if 0x4c000000 <= pointer < 0x4c800000-count else None

            def registers():
                values = original_registers()
                pc = values[15]
                if pc not in points:
                    return values
                packet = memory(self.word(values[13]+4), 8) if pc == 0x448d52e4 else None
                if packet and packet[:4] != '0405':
                    return values
                media = self.word(0x4c07a260)
                row = dict(point=points[pc], tick=self.word(0x4200f264),
                    wall=time.monotonic()-self.trace_epoch, packet=packet,
                    registers=[hex(v) for v in values])
                if 0x4c000000 <= media <= 0x4c7ffcc0:
                    row['media'] = memory(media+0x2ac, 0x60)
                    queue = self.word(media+0x2c0)
                    row['queue'] = memory(queue, 0x28)
                    row['stream'] = memory(self.word(0x4c046bfc)+4*24, 24)
                if pc == 0x4488cb64:
                    row['status'] = memory(self.word(values[0]+4), 8)
                if pc == 0x4495ee00:
                    row['stack'] = memory(values[13], 0x40)
                    row['client'] = {f'r{i}': memory(values[i], 0x100)
                                     for i in range(4, 8)}
                self.refill_events.append(row)
                return values

            def diagnostics():
                text = original_diagnostics()
                if not self.refill_enabled and self.ready and text.count('AAC_UNDERRUN') >= args.trace_after_underruns:
                    guest_ui.CHECKPOINTS.update(points)
                    for pc in points:
                        self.d.breakpoint(pc, thumb=True)
                    self.refill_enabled = True
                    print('Late native refill observations enabled', flush=True)
                return text

            self.d.registers = registers
            self.q.diagnostics = diagnostics
            return result

        def close(self):
            try:
                super().close()
            finally:
                (directory/'refill-events.json').write_text(json.dumps(
                    getattr(self, 'refill_events', []), indent=2)+'\n')
                guest_ui.CHECKPOINTS.clear()
                guest_ui.CHECKPOINTS.update(previous)

    probe_video_realtime.OriginalUISession = Session
    sys.argv = [sys.argv[0], '--directory', args.directory, *rest]
    probe_video_realtime.main()


if __name__ == '__main__':
    main()
