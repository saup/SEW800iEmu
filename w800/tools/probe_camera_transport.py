"""Opt into incomplete sensor transport for original native camera tracing.

The production GUI does not enable this path. It supplies no host frames and
must not be used as evidence that camera preview works.
"""
import sys
import json
import struct
from unittest.mock import patch

from w800.backend import QemuBackend
from w800.tools import probe_menu_services


def main():
    original_options = QemuBackend._machine_option
    original_capture = probe_menu_services.capture

    def options(backend):
        return original_options(backend) + ',camera-transport=on'

    def capture(session, directory, label, previous_messages=()):
        values = original_capture(session, directory, label, previous_messages)
        state = {}
        for address, count in ((0x4c0406a4, 16), (0xf7000200, 12),
                               (0xf7000300, 2), (0xf2000100, 64)):
            state[hex(address)] = [hex(value) for value in struct.unpack(
                '<' + 'I' * count, session.d.read(address, count * 4))]
        context = state['0x4c0406a4']
        for index in (5, 6):
            pointer = int(context[index], 16)
            if 0x4c000000 <= pointer <= 0x4c7fffc0:
                state[hex(pointer)] = [hex(value) for value in struct.unpack(
                    '<16I', session.d.read(pointer, 64))]
        (directory / (label + '-hardware.json')).write_text(json.dumps(state, indent=2) + '\n')
        return values

    if '--hardware-log' not in sys.argv:
        sys.argv.append('--hardware-log')
    if '--trace-camera' not in sys.argv:
        sys.argv.append('--trace-camera')
    with patch.object(QemuBackend, '_machine_option', options), \
         patch.object(probe_menu_services, 'capture', capture):
        probe_menu_services.main()


if __name__ == '__main__':
    main()
