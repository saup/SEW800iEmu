"""Observe original Java/OAF lifecycle in a private QEMU instance.

No network requests, guest instruction edits, or validation-result changes.
The optional --post-startup experiment calls the original delayed InitBook
initialization callback on the MMI task, rather than manufacturing its events.
"""
import argparse
import json
import struct
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from .. import guest_ui
from .debug import Debugger


POINTS = {
    0x44e91700: 'InitBook delayed initialization',
    0x44e2d83c: 'Original startup event producer',
    0x44f68d20: 'OAF startup event handler',
    0x44f68a84: 'OAF operating mode event',
    0x44f68fb4: 'OAF lifecycle flags',
    0x44f69970: 'OAF request dispatcher',
    0x44f636a0: 'OAF application request',
    0x44f69650: 'OAF application request reply',
    0x4513e1fc: 'Java startup event handler',
    0x4513e03c: 'Java leaves startup wait',
    0x4513e050: 'Java VM main returned',
    0x44d6e088: 'Java VM main entered',
    0x44d6e0c8: 'Java VM initialization callback',
    0x44d6e0d2: 'Java VM event wait callback',
    0x44d6e10a: 'Java application execution',
    0x4513e140: 'Java VM event callback',
    0x44f628e0: 'OAF application descriptor initialization',
    0x44f64154: 'OAF readiness guard',
    0x44f643ee: 'OAF request state result',
    0x450f227c: 'Download registration request send',
    0x450f228e: 'Download registration response wait',
    0x44c2a9ea: 'OAF original service initialization',
    0x44c2aa34: 'OAF enters native receive loop',
    0x44cea4d0: 'BT CM server initialization',
    0x44cea4e4: 'BT CM initialize first dependency result',
    0x44cea4ee: 'BT CM initialize second dependency result',
    0x44cea4f8: 'BT CM initialize third dependency result',
    0x44cea502: 'BT CM initialize fourth dependency result',
    0x44cea50c: 'BT CM initialize fifth dependency result',
    0x44cea516: 'BT CM initialize sixth dependency result',
    0x44f25898: 'BT UI initialization',
    0x44f263d8: 'BT UI service acquisition',
    0x44f1e0a8: 'Remote interface query',
    0x44f1e13a: 'Remote interface query send',
    0x45124a66: 'BT platform interface query',
    0x45124a84: 'BT platform subscription 0',
    0x45124aa4: 'BT platform subscription 1',
    0x45124ac4: 'BT platform subscription 3',
    0x45124ae4: 'BT platform subscription 4',
    0x45124b04: 'BT platform subscription 5',
    0x45124b24: 'BT platform subscription 6',
    0x447f1e34: 'HCITL start request',
    0x447f1e56: 'HCITL UART open result',
    0x447f2104: 'HCITL UART open completed',
    0x447f197c: 'HCITL transport connected',
    0x447f2b10: 'HCITL received message',
    0x44a97c94: 'UART transmit',
    0x447f1f84: 'HCITL received bytes',
    0x44a984b8: 'UART3 original interrupt process',
    0x44a98198: 'UART transmitter original interrupt handler',
    0x44f30ef4: 'MoCa data service acquisition',
    0x44f2a3d4: 'MoCa data service acquisition result',
    0x44d6acf4: 'SIM utility initialization',
    0x44d6ad8e: 'SIM descriptor count result',
    0x44d6adde: 'SIM descriptor enumeration result',
    0x44d6ae10: 'SIM interface construction result',
    0x44d6ae58: 'SIM utility initialized',
}


def state(session):
    return {f'{address:08x}': session.d.read(address, size).hex()
            for address, size in [(0x4c3912b6, 2), (0x4c0bad90, 16),
                                  (0x4c050a80, 4)]}


def main():
    parser = argparse.ArgumentParser()
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument('--post-startup', action='store_true')
    lifecycle.add_argument('--application-startup', action='store_true',
                           help='Call original application notification API independently')
    parser.add_argument('--phone-first', action='store_true',
                        help='Select Start phone with the physical key before scheduling lifecycle')
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--keys', default='select,up,right,select,select')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--stage-game', choices=('PuzzleSlider', 'QuadraPop', 'WorldClock3D'),
                        help='Copy exact preset JAD/JAR to the private Other folder for native installation')
    parser.add_argument('--begin-native-game', choices=('PuzzleSlider', 'QuadraPop', 'WorldClock3D'),
                        help='Call the original JAR installer on MMI, leaving native dialogs visible')
    parser.add_argument('--provision-native-games', action='store_true',
                        help='Install QuadraPop and PuzzleSlider with native APIs and reviewed native dialogs')
    parser.add_argument('--output', default='w800/reports/java-lifecycle.json')
    args = parser.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])
    samples = []

    class Observer(Debugger):
        def __init__(self, backend):
            super().__init__(backend)
            backend.command('human-monitor-command',
                            {'command-line': f'logfile {backend.directory / "bluetooth-mmio.log"}'})
            backend.command('human-monitor-command', {'command-line': 'log unimp'})

        def registers(self):
            regs = super().registers()
            pc = regs[15]
            if pc in POINTS and len(samples) < 3000:
                sample = {'pc': hex(pc), 'name': POINTS[pc],
                          'registers': [hex(n) for n in regs],
                          'flags': self.read(0x4c3912b6, 2).hex(),
                          'oaf_state': self.read(0x4c0bad90, 16).hex()}
                if pc == 0x44f69970 and 0x4c000000 <= regs[0] < 0x4c7fffe0:
                    sample['request'] = self.read(regs[0], 32).hex()
                if pc == 0x450f227c:
                    pointer = struct.unpack('<I', self.read(regs[0], 4))[0]
                    sample['request'] = self.read(pointer, 20).hex()
                    sample['download_handle'] = hex(struct.unpack('<I', self.read(0x4c0bae68, 4))[0])
                if pc == 0x44f1e13a:
                    pointer = struct.unpack('<I', self.read(regs[13], 4))[0]
                    sample['request'] = self.read(pointer, 92).hex()
                    sample['proxy'] = self.read(regs[5], 48).hex()
                if pc == 0x447f2b10:
                    pointer = struct.unpack('<I', self.read(regs[13], 4))[0]
                    sample['message'] = self.read(pointer, 32).hex()
                if pc == 0x44a97c94 and 0 < regs[2] <= 1024:
                    sample['bytes'] = self.read(regs[1], regs[2]).hex()
                if pc == 0x44f30ef4:
                    manager = struct.unpack('<I', self.read(0x4c0b977c, 4))[0]
                    sample['network_manager'] = hex(manager)
                    if 0x4c000000 <= manager < 0x4c800000:
                        table = struct.unpack('<I', self.read(manager, 4))[0]
                        sample['network_manager_vtable'] = self.read(table, 48).hex()
                if pc in (0x44d6ad8e, 0x44d6adde, 0x44d6ae10, 0x44d6ae58):
                    sample['sim_utility'] = self.read(regs[4], 48).hex()
                    sample['sim_enumeration_stack'] = self.read(regs[13], 112).hex()
                samples.append(sample)
            return regs

    guest_ui.Debugger = Observer
    guest_ui.CHECKPOINTS.update(POINTS)
    session = guest_ui.OriginalUISession(progress=lambda s: print(s, flush=True))
    experiment = ('original InitBook delayed initialization' if args.post_startup else
                  'original application startup notification' if args.application_startup else
                  'unmodified original UI component startup')
    output = {'experiment': experiment, 'states': []}
    try:
        session.start()
        output['states'].append({'at': 'startup', **state(session)})
        if args.post_startup or args.application_startup:
            if args.phone_first:
                session.advance(100, key_event=('select', True))
                session.advance(200, key_event=('select', False))
                session.advance(500)
            if args.application_startup:
                from ..java_services import notify_application_startup
                notify_application_startup(session)
            else:
                session.call_on_mmi(0x44e91798, 0)
            session.advance(1000)
            output['states'].append({'at': 'lifecycle API returned', **state(session)})
        if args.stage_game:
            from ..java_services import stage_bundled_game_files
            output['staged_game_files'] = stage_bundled_game_files(session, args.stage_game)
        if args.begin_native_game:
            from ..java_services import begin_bundled_game_installation
            output['native_installation'] = begin_bundled_game_installation(session, args.begin_native_game)
        if args.provision_native_games:
            from ..java_services import provision_bundled_games
            output['native_installations'] = provision_bundled_games(session)
        for key in filter(None, args.keys.split(',')):
            session.advance(100, key_event=(key, True))
            session.advance(200, key_event=(key, False))
            session.advance(500)
            output['states'].append({'at': key, **state(session)})
        for i in range(args.seconds):
            session.advance(1000)
        output['states'].append({'at': 'final', **state(session)})
        output['task_stacks'] = {hex(a): session.d.read(a, 1024).hex()
                                 for a in [0x4c224b00, 0x4c21e700]}
        image = str(Path(args.output).with_suffix('.png'))
        session.frame().save(image)
        output['image'] = image
        if args.interactive:
            import code
            def press(key, count=1):
                output.setdefault('interactive_physical_keys', []).append(
                    {'key': key, 'count': count, 'down_ms': 100, 'up_ms': 700})
                for _ in range(count):
                    session.advance(100, key_event=(key, True))
                    session.advance(200, key_event=(key, False))
                    session.advance(500)
                session.frame().save(image)
                return image
            code.interact(local={'session': session, 'press': press,
                                 'samples': samples, 'state': state,
                                 'output': output, 'image': image})
    except Exception as error:
        output['error'] = f'{type(error).__name__}: {error}'
        print(output['error'], flush=True)
        if session.d:
            output['registers_on_error'] = [hex(n) for n in session.d.registers()]
            output['state_on_error'] = state(session)
            try:
                image = str(Path(args.output).with_suffix('.png'))
                session.frame().save(image)
                output['image'] = image
            except Exception as frame_error:
                output['frame_error'] = str(frame_error)
    finally:
        output['samples'] = samples
        output['report'] = session.report()
        if session.q:
            output['qemu_diagnostics'] = session.q.diagnostics()
            log = session.q.directory / 'bluetooth-mmio.log'
            if log.exists():
                output['controller_commands'] = [line for line in log.read_text().splitlines()
                                                 if 'W800_BT' in line]
        Path(args.output).write_text(json.dumps(output, indent=2) + '\n')
        session.close()
    print(args.output, flush=True)


if __name__ == '__main__':
    main()
