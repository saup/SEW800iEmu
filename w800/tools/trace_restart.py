"""Observe original cold/warm boot and a physical power-key restart."""
import hashlib
import json
from w800.backend import QemuBackend, ROOT
from w800.tools.trace_reset_boot import observe


class RestartBackend(QemuBackend):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observed_resets = []

    def _read(self):
        reply = super()._read()
        # Thousands of GDB stop/resume events can evict RESET from the
        # backend's bounded general event history before a phase ends.
        if reply.get('event') == 'RESET':
            self.observed_resets.append(reply)
        return reply


def main():
    report = {'instruction_patches': False, 'debugger_register_setup': False,
              'qemu_sha256': hashlib.sha256((ROOT / 'build/qemu-system-arm').read_bytes()).hexdigest(),
              'phases': []}
    with RestartBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
        initial = hashlib.sha256(q.flash_path.read_bytes()).hexdigest()
        report['phases'].append(observe(q, 'restart-cold'))
        q.command('system_reset')
        report['phases'].append(observe(q, 'restart-warm'))
        prior_resets = len(q.observed_resets)
        q.command('cont')
        q.send_key('power', True)
        try:
            report['phases'].append(observe(q, 'restart-power'))
        finally:
            q.command('cont')
            q.send_key('power', False)
            q.command('stop')
        report['guest_reset_events_after_power'] = q.observed_resets[prior_resets:]
        report['source_sha256'] = initial
        report['source_unchanged'] = hashlib.sha256(q.flash_path.read_bytes()).hexdigest() == initial
    (ROOT / 'reports/restart-boot-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'phases'}, indent=2))


if __name__ == '__main__':
    main()
