"""Bounded experiment invoking the original UI task-creation interface.

This is a disposable component test. It does not patch MAIN or replace any
device/integrity result. Original startup is suspended at a file-API checkpoint
while normal task-creation arguments are supplied through the debugger.
"""
import hashlib
import argparse
import json
import re
import struct
import time
from PySide6.QtGui import QImage
from w800.backend import QemuBackend, ROOT
from w800.guest_files import ANCHOR, OriginalFiles, validate_firmware
from w800.tools.debug import Debugger
from w800.tools.trace_messages import cstring, decode


CHECKPOINTS = {0x44d22c64: 'MMI_Process entry', 0x44e91cc0: 'InitBook constructor',
               0x44e917b0: 'StartUp page', 0x44e918d4: 'Started page',
               0x44e919b8: 'MMIInit page', 0x44ab88b4: 'LCD completion'}
CHECKPOINTS.update({pc: f'UI initializer boundary {pc:08x}'
                    for pc in [0x44d22c7c, *range(0x44d22c82, 0x44d22cb6, 4)]})
CHECKPOINTS.update({pc: f'UI service initializer {pc:08x}' for pc in [
    0x44e71b94, 0x44e71b98, 0x44e71b9c, 0x44e71ba0, 0x44e71ba4,
    0x44e71ba8, 0x44e71bac, 0x44e71bb0, 0x44e71bb4, 0x44e71bb8,
    0x44e71bc8, 0x44e71bcc, 0x44e71bd4, 0x44e71bde, 0x44e71bee,
    0x44e71bf2, 0x44e71bf6, 0x44e71c00, 0x44e71c04]})
CHECKPOINTS[0x44ea0970] = 'PR_Process entry'
CHECKPOINTS.update({pc: f'InitBook boundary {pc:08x}' for pc in [
    0x44e91d06, 0x44e91ddc, 0x44e91df4, 0x44e91dfe, 0x44e91e0a,
    0x44e91e14, 0x44e91e20, 0x44e91e28, 0x44e91e6c, 0x44e91e7a,
    0x44e91e86, 0x44e91e8e, 0x44e91e94, 0x44e91e9e, 0x44e91ea8, 0x44e91eb2,
    0x44e91eee, 0x44e91f22, 0x44e91f2a, 0x44e91f30, 0x44e91f3a]})
CHECKPOINTS[0x44d22cb6] = 'InitBook constructor returned'
CHECKPOINTS[0x44e91b2c] = 'StartupMode page'
CHECKPOINTS[0x44c95eb4] = 'Camera flash service factory call'
CHECKPOINTS[0x44c95eb6] = 'Camera flash service factory returned'
CHECKPOINTS[0x44e44e08] = 'Original key signal'
CHECKPOINTS[0x44a4f7e0] = 'Original input state machine'
CHECKPOINTS[0x44a4fe86] = 'Input focus recipient'
CHECKPOINTS[0x44a4fee4] = 'Input focus delivery'
CHECKPOINTS.update({pc: f'MMI event loop {pc:08x}' for pc in [0x44d22d42, 0x44d22d48,
    0x44d22d4e, 0x44d22d54, 0x44d22d64, 0x44d22d6e, 0x44d22d82,
    0x44d22d86, 0x44d22dae, 0x44d22dbc, 0x44d22dc6]})
CHECKPOINTS.update({pc: f'MMI input handler {pc:08x}' for pc in [
    0x44e6e592, 0x44e6e598, 0x44e6e5b4, 0x44e6e5c2, 0x44e6e5e6,
    0x44e6e5f4, 0x44e6e604, 0x44e6e60c, 0x44e6ec90]})
CHECKPOINTS.update({pc: f'MMI redraw {pc:08x}' for pc in [
    0x44e6e62c, 0x44e6e64c, 0x44e6e69c, 0x44e6e6a0, 0x44e6e6b0,
    0x44e6e6be, 0x44e6e6f8, 0x44e6e70c]})
CHECKPOINTS.update({pc: f'Canvas redraw {pc:08x}' for pc in [
    0x44e76830, 0x44e76836, 0x44e7685c, 0x44e76860, 0x44e7686a, 0x44e768ae]})
CHECKPOINTS[0x44c902bc] = 'Original synchronous receive'
CHECKPOINTS[0x44d30bb0] = 'Canvas clip query'
CHECKPOINTS.update({pc: f'Draw operation {pc:08x}' for pc in [
    0x44e76100, 0x44e76176, 0x44e76230, 0x44e7623e, 0x44e76244,
    0x44e76272, 0x44e76374, 0x44e76390, 0x44e7642c, 0x44e764b8,
    0x44e764ca, 0x44e76536, 0x44e76586, 0x44e765da, 0x44e7663e,
    0x44e766c8, 0x44e766f6, 0x44e7674a, 0x44e767b2, 0x44e7680e]})
CHECKPOINTS.update({pc: f'Theme draw {pc:08x}' for pc in [
    0x45049690, 0x45048e70, 0x45048e8c, 0x45048e94, 0x45048e9e, 0x45048eea,
    0x45048ef0, 0x45048f48, 0x45048f64, 0x45048fc8, 0x4504903e,
    0x450492fa, 0x45049316, 0x45049376, 0x450493ba, 0x45049408]})
LOGGERS = {0x45169c70, 0x44ad6474}
FATAL_CHECKPOINTS = {0x44020000: False, 0x0: False, 0x4: False, 0xc: False, 0x10: False,
                     0x450809a8: True, 0x4490cf80: True}


def word(d, address):
    return struct.unpack('<I', d.read(address, 4))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys', default='', help='Comma-separated physical phone keys to exercise')
    options = parser.parse_args()
    flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
    validate_firmware(flash)
    initial = hashlib.sha256(flash.read_bytes()).hexdigest()
    report = {'guest_instruction_patches': False, 'validation_result_changes': False,
              'debugger_register_setup': True, 'scope': 'disposable UI component experiment',
              'events': [], 'messages': [], 'source_sha256': initial}
    report['qemu_sha256'] = hashlib.sha256((ROOT / 'build/qemu-system-arm').read_bytes()).hexdigest()
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
        d = Debugger(q)
        try:
            # Read an existing application-manager interface as it initializes.
            d.breakpoint(0x44f772b4, thumb=True)
            d.run()
            if d.registers()[15] != 0x44f772b4:
                raise RuntimeError('Application manager initialization was not reached')
            manager = word(d, d.registers()[0] + 4)
            task_service = word(d, manager + 0x34)
            create_task = word(d, word(d, task_service) + 0xb4)
            if create_task != 0x446f3df9:
                raise RuntimeError('Unsupported task-service interface')
            d.breakpoint(0x44f772b4, enabled=False, thumb=True)
            d.breakpoint(ANCHOR, thumb=True)
            d.run()
            if d.registers()[15] != ANCHOR:
                raise RuntimeError('Mounted filesystem checkpoint was not reached')
            files = OriginalFiles(d)
            report['mmi_handle_before'] = word(d, 0x4c0bae24)
            descriptor = word(d, 0x4c04efd0) + 6 * 32
            if word(d, descriptor) != 2 or word(d, descriptor + 0x10) != 0x44d22c65:
                raise RuntimeError('Original MMI descriptor did not match')
            fields = d.read(descriptor, 32)
            report['manager_startup_mode_before'] = word(d, 0x4c39dcdc)
            for pc in {*CHECKPOINTS, *LOGGERS}:
                d.breakpoint(pc, thumb=True)
            for pc, thumb in FATAL_CHECKPOINTS.items():
                d.breakpoint(pc, thumb=thumb)
            d.sock.settimeout(5)

            def invoke(label, function, *args, key_event=None):
                packet = bytearray(files.saved)
                stack = struct.unpack_from('<I', packet, 13 * 4)[0]
                count = ((max(0, len(args) - 4) * 4 + 7) // 8) * 8
                stack -= count
                if not 0x4c000000 <= stack <= 0x4c800000 - count:
                    raise RuntimeError('Original call stack is outside RAM')
                old_stack = d.read(stack, count)
                if count:
                    arguments = struct.pack('<' + 'I' * (len(args) - 4), *args[4:]).ljust(count, b'\0')
                    if d.packet(f'M{stack:x},{count:x}:' + arguments.hex()) != 'OK':
                        raise RuntimeError('Unable to stage task call arguments')
                for i, value in enumerate(args[:4]):
                    struct.pack_into('<I', packet, i * 4, value)
                struct.pack_into('<I', packet, 13 * 4, stack)
                struct.pack_into('<I', packet, 14 * 4, ANCHOR | 1)
                struct.pack_into('<I', packet, 15 * 4, function & ~1)
                if d.packet('G' + packet.hex()) != 'OK':
                    raise RuntimeError('Unable to set component call arguments')
                started = time.monotonic()
                delivered = key_event is None
                for _ in range(5000):
                    d.send_packet('c')
                    if not delivered:
                        # QEMU accepts physical input only while running.
                        # The guest is executing the bounded timed receive,
                        # never the suspended original startup instruction.
                        try:
                            q.send_key(*key_event)
                            delivered = True
                        except RuntimeError as error:
                            if 'VM not running' not in str(error):
                                raise
                    d.receive_packet()
                    regs = d.registers()
                    pc = regs[15]
                    if pc in FATAL_CHECKPOINTS:
                        report['fatal_registers'] = [hex(x) for x in regs]
                        if (0x4c000000 <= regs[13] <= 0x4c7ffe00 or 0x2000 <= regs[13] <= 0xfe00):
                            raw = d.read(regs[13], 512)
                            report['fatal_stack'] = [hex(x) for x in struct.unpack('<128I', raw)]
                            report['fatal_stack_strings'] = [s.decode('ascii') for s in re.findall(rb'[\x20-\x7e]{6,}', raw)]
                        raise RuntimeError(f'Original component fault/reset at {pc:08x}')
                    if pc in LOGGERS:
                        fmt = cstring(d, regs[0])
                        arguments = list(regs[1:4]) + list(struct.unpack('<8I', d.read(regs[13], 32)))
                        report['messages'].append(decode(d, fmt, arguments).strip())
                        if 'User called assert' in report['messages'][-1] or 'Assert Failure' in report['messages'][-1]:
                            raise RuntimeError('Original component assertion; VM discarded')
                        if time.monotonic() - started > 30:
                            raise RuntimeError('Component observation time exceeded')
                        d.step_over_breakpoint(pc, thumb=True)
                        continue
                    if pc == ANCHOR:
                        if not delivered:
                            raise RuntimeError('Input was not delivered during the scheduler window')
                        if regs[13] != stack:
                            raise RuntimeError('Component call returned on a different stack')
                        report[label + '_return'] = hex(regs[0])
                        report[label + '_host_seconds'] = round(time.monotonic() - started, 4)
                        if count and d.packet(f'M{stack:x},{count:x}:' + old_stack.hex()) != 'OK':
                            raise RuntimeError('Unable to restore original argument stack')
                        if d.packet('G' + files.saved.hex()) != 'OK':
                            raise RuntimeError('Unable to restore original caller')
                        return regs[0]
                    report['events'].append({'call': label, 'checkpoint': CHECKPOINTS.get(pc, 'unexpected stop'),
                                             'pc': hex(pc), 'args': [hex(x) for x in regs[:4]]})
                    if pc == 0x44a4fe86 and 0x4c000000 <= regs[0] <= 0x4c7fffc0:
                        report['events'][-1]['focus'] = [hex(x) for x in struct.unpack('<12I', d.read(regs[0], 48))]
                    if pc == 0x44d22dc6:
                        report['events'][-1]['message'] = d.read(regs[0], 64).hex()
                    if pc == 0x44c902bc:
                        report['events'][-1]['lr'] = hex(regs[14])
                        report['events'][-1]['sp'] = hex(regs[13])
                        if 0x44000000 <= regs[0] <= 0x4c7ffff0:
                            report['events'][-1]['filter'] = d.read(regs[0], 16).hex()
                    if pc == 0x44d30bb0:
                        report['events'][-1]['method'] = hex(regs[5])
                    if pc not in CHECKPOINTS or time.monotonic() - started > 30:
                        raise RuntimeError('Component call did not return within observation bounds')
                    if pc == 0x44ab88b4:
                        QImage(str(q.framebuffer_path())).save(str(ROOT / 'reports/ui-component-frame.png'))
                    d.step_over_breakpoint(pc, thumb=True)
                raise RuntimeError('Component checkpoint count exceeded')

            # Let the original application manager perform creation,
            # registration, handle assignment and startup-message delivery
            # for this one UI component. It owns the full internal ABI.
            # The UI initializer sends a synchronous request to PR_Process
            # through its original handle at 4c0bae58 (4514e6cc).
            # Each component receives one startup mode, not a combined
            # eligibility bitmask. MMI explicitly accepts mode 1 at 44d22cdc.
            mode = report['manager_startup_mode_before']
            if mode != 1:
                raise RuntimeError('Unsupported original component startup mode')
            report['component_startup_mode_argument'] = mode
            result = invoke('start_windowsystem_component', 0x44f773f8, manager, 55, mode)
            if result:
                raise RuntimeError('Original Windowsystem component startup failed')
            result = invoke('start_pr_component', 0x44f773f8, manager, 16, mode)
            if result:
                raise RuntimeError('Original PR component startup failed')
            # InitBook reads clock/alarm state through 450bb458, whose
            # destination is the CLOCK_Process handle at 4c0baeac.
            result = invoke('start_clock_component', 0x44f773f8, manager, 4, mode)
            if result:
                raise RuntimeError('Original clock component startup failed')
            # CallManagerBook queries the original Call Handler interface
            # through 44ce6040. Without its factory component the interface
            # is uninitialized and causes a data abort at 44bb3a58.
            result = invoke('start_call_handler_component', 0x44f773f8, manager, 29, mode)
            if result:
                raise RuntimeError('Original Call Handler component startup failed')
            # ConnectionManagerBook creates its service interface at
            # 44ced6bc. Its absence causes the original assertion at 44ced6f2.
            result = invoke('start_connection_manager_component', 0x44f773f8, manager, 32, mode)
            if result:
                raise RuntimeError('Original Connection Manager component startup failed')
            for label, component in [('timer', 18), ('ui_util', 3), ('swbp', 10),
                                     ('memory', 15), ('pdh', 19), ('image', 12)]:
                if invoke(f'start_{label}_component', 0x44f773f8, manager, component, mode):
                    raise RuntimeError(f'Original {label} component startup failed')
            # The camera flash factory asks for component 43 at 44c9a4b6;
            # its missing service returns 80040800 and leaves a null object.
            if invoke('start_at_server_component', 0x44f773f8, manager, 43, mode):
                raise RuntimeError('Original AT server component startup failed')
            result = invoke('start_ui_component', 0x44f773f8, manager, 2, mode)
            report['created_handle'] = hex(word(d, word(d, descriptor + 0xc)))
            if result:
                raise RuntimeError('Original UI component startup failed')
            # Let the scheduler execute the component while this test caller
            # waits normally, without consuming any startup result.
            wait_filter = files.stage(2056, struct.pack('<2I', 1, 0x7ffffffe))
            invoke('component_scheduler_window', 0x44a47ae8, 1000, wait_filter)
            invoke('component_scheduler_window_2', 0x44a47ae8, 1000, wait_filter)
            q.command('human-monitor-command', {'command-line': f'logfile {q.directory}/ui-device.log'})
            q.command('human-monitor-command', {'command-line': 'log unimp,guest_errors'})
            report['frames'] = []
            for index, key in enumerate(['', *filter(None, options.keys.split(','))]):
                if key:
                    invoke(f'key_{index}_{key}_down', 0x44a47ae8, 120, wait_filter, key_event=(key, True))
                    invoke(f'key_{index}_{key}_up', 0x44a47ae8, 450, wait_filter, key_event=(key, False))
                frame = ROOT / f'reports/ui-component-key-{index}-{key or "initial"}.png'
                QImage(str(q.framebuffer_path())).save(str(frame))
                report['frames'].append(str(frame))
            report['caller_restored'] = True
            report['mmi_handle_after'] = word(d, 0x4c0bae24)
            report['pdi_registers'] = d.read(0xf7000100, 256).hex()
            report['graphics_process'] = hex(word(d, 0x4c0b8f70))
        except (RuntimeError, TimeoutError, ValueError) as error:
            report['stop'] = str(error) or 'No component checkpoint within the bounded wait'
        finally:
            q.command('stop')
            report['snapshot'] = q.snapshot()
            device_log = q.directory / 'ui-device.log'
            if device_log.exists():
                report['device_log'] = device_log.read_text(errors='replace')[-16000:]
            d.close()
    report['source_unchanged'] = hashlib.sha256(flash.read_bytes()).hexdigest() == initial
    (ROOT / 'reports/ui-component-experiment.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'snapshot'}, indent=2))


if __name__ == '__main__':
    main()
