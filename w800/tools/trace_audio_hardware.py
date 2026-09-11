"""Observe the original audio UI and DSP requests in a disposable VM.

Only local QEMU Unix sockets are used. No file decoding, service replies,
firmware instruction changes or playback substitutions are performed.
"""
import argparse
from array import array
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import struct
import traceback
import wave

from w800 import guest_ui
from w800.backend import ROOT
from w800.tools.debug import Debugger


POINTS = {
    0x447367a8: 'DSP queued send',
    0x44736898: 'DSP channel error',
    0x449cbf7c: 'DSP mailbox send',
    0x44858c38: 'DSP DMA request',
    0x44736540: 'DSP DMA completion callback',
    0x44736278: 'DSP mailbox completion callback',
    0x44a872d4: 'Original output configuration reply callback',
    0x446a275c: 'Original mixer table reply accepted',
    0x448826c4: 'Original synthesizer initialization reply received',
    0x448832bc: 'Original synthesizer reply callback',
    0x4473674c: 'Original DSP addressed upload',
    0x448d5714: 'Original stream write',
    0x44882904: 'Original synthesizer run control',
    0x44882724: 'Original synthesizer release call',
    0x44883472: 'Original MIDI startup return',
    0x448847a0: 'Original MIDI stop reason',
    0x4488286c: 'Original MIDI stream close call',
    0x44884782: 'Original MIDI end marker',
    0x449ff6f4: 'DSP kernel upload',
    0x4488d660: 'Original compressed decoder control',
    0x4488d6e4: 'Original compressed decoder configuration arguments',
    0x4488da0a: 'Original compressed decoder configuration wait return',
    0x44e44e08: 'Physical key signal',
}
ROUTE = ('select', 'down', 'down', 'select', 'select', 'down', 'down',
         'select', 'select', 'down', 'down', 'down', 'soft_right')
MMI_POINTS = {
    0x44a47ae8: 'MMI timed receive', 0x44a47af0: 'MMI receive',
    0x44a44798: 'MMI native send', 0x448ca366: 'MMI delivered message',
    0x44d22daa: 'MMI returned event',
    0x44d22df4: 'MMI event predispatch',
    0x44e6e370: 'MMI book event routing',
    0x44e75c9e: 'MMI key consumed by display activation',
    0x44e78688: 'Original display activation',
    0x44e75f80: 'MMI key release owner comparison',
}
LATE_MMI_POINTS = (0x448ca366, 0x44d22daa, 0x44d22df4, 0x44e6e370,
                   0x44e75c9e, 0x44e78688, 0x44e75f80)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'reports/audio-hardware.json'))
    parser.add_argument('--wav', help='Capture only emulated-device PCM through QEMU WAV output')
    parser.add_argument('--audio-output', choices=('none', 'coreaudio'), default='none',
                        help='Explicitly choose host speakers; private probes are silent by default')
    parser.add_argument('--seconds', type=int, default=5)
    parser.add_argument('--media', choices=('greeting', 'latin'), default='greeting',
                        help='Original Alarm signal picker entry to preview')
    parser.add_argument('--capture-kernels', action='store_true',
                        help='Save actual guest DSP upload payloads for protocol analysis')
    parser.add_argument('--back-count', type=int, choices=(0, 1, 2), default=1,
                        help='Physical Back presses after playback; zero observes natural completion')
    parser.add_argument('--trace-mmi', action='store_true',
                        help='Read native MMI waits/messages near the late Back action')
    args = parser.parse_args()
    previous_wav_mtime = (Path(args.wav).stat().st_mtime_ns
                          if args.wav and Path(args.wav).exists() else None)
    rows, counts, waits = [], Counter(), []
    final_stream_writes = deque(maxlen=8)
    phase, mmi_pid, mmi_tcb, mmi_stack = 'startup', None, None, None
    mmi_snapshots = []
    original_step = Debugger.step_over_breakpoint

    def observe(debug, address, thumb=False):
        nonlocal mmi_tcb, mmi_stack
        if address in POINTS or (args.trace_mmi and address in MMI_POINTS):
            regs = debug.registers()
            name = POINTS.get(address, MMI_POINTS.get(address))
            pid = None
            if address in MMI_POINTS:
                tcb = struct.unpack('<I', debug.read(0x4c041c68, 4))[0]
                pid = struct.unpack('<H', debug.read(tcb + 2, 2))[0]
                if pid != mmi_pid:
                    return original_step(debug, address, thumb)
                mmi_tcb = tcb
                if mmi_stack is None:
                    mmi_stack = regs[13]
            counts[name] += 1
            essential = address in (0x44884782, 0x448847a0, 0x44882724,
                                    0x4488286c, 0x44882904, 0x4488d660,
                                    0x4488d6e4, 0x4488da0a)
            if len(rows) < 1000 or essential or address == 0x448d5714:
                row = {'event': name, 'pc': hex(address), 'phase': phase,
                       'registers': [hex(x) for x in regs]}
                if essential:
                    row['virtual_time_ns'] = session.q.command('qom-get', {
                        'path': '/machine', 'property': 'virtual-time-ns'})
                if address in (0x4488d6e4, 0x4488da0a):
                    row['stack'] = debug.read(regs[13], 192).hex()
                if pid is not None:
                    row['native_pid'] = hex(pid)
                    row['stack'] = debug.read(regs[13], 512).hex()
                    pointer = regs[1] if address == 0x44a47ae8 else regs[0]
                    if address == 0x44d22daa:
                        pointer = struct.unpack('<I', debug.read(regs[13], 4))[0]
                    if address == 0x44a44798:
                        pointer = struct.unpack('<I', debug.read(pointer, 4))[0]
                    if 0x44000000 <= pointer <= 0x4c7fffc0:
                        row['object_address'] = hex(pointer)
                        row['object'] = debug.read(pointer, 64).hex()
                    if address in (0x44e75c9e, 0x44e78688, 0x44e75f80):
                        row['key_routing_state'] = debug.read(0x4c050e44, 0xf8).hex()
                        display = regs[5] if address == 0x44e75c9e else regs[0]
                        if address == 0x44e75f80:
                            display = regs[7]
                        if 0x4c000000 <= display <= 0x4c7fff00:
                            row['display_address'] = hex(display)
                            row['display'] = debug.read(display, 0x90).hex()
                if address == 0x447367a8:
                    header = debug.read(regs[1] - 8, 8)
                    length = struct.unpack_from('<H', header, 4)[0]
                    row.update(header=header.hex(), halfwords=length,
                               payload=debug.read(regs[1], min(length * 2, 16380)).hex())
                elif address == 0x449cbf7c:
                    row['payload'] = debug.read(regs[2], min(regs[1] * 2, 64)).hex()
                elif address == 0x448d5714:
                    row['payload'] = debug.read(regs[1], min(regs[2] * 2, 16380)).hex()
                elif address == 0x4473674c:
                    row['payload'] = debug.read(regs[1], min(regs[3] * 2, 16380)).hex()
                elif address == 0x44884782:
                    row['payload'] = debug.read(regs[0], 12).hex()
                elif address == 0x449ff6f4 and args.capture_kernels and 0 < regs[1] <= 131072:
                    data = debug.read(regs[2], regs[1] * 2)
                    directory = Path(args.output).with_suffix('')
                    directory = directory.with_name(directory.name + '-dsp')
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / f'{counts[name]:04d}-{regs[0]:06x}.bin'
                    path.write_bytes(data)
                    row.update(word_address=regs[0], words=regs[1], arm_source=hex(regs[2]),
                               sha256=hashlib.sha256(data).hexdigest(), path=str(path.resolve()))
                row['queue_state'] = debug.read(0x4c040584, 0x48).hex()
                if len(rows) < 1000 or essential:
                    rows.append(row)
                if address == 0x448d5714:
                    final_stream_writes.append(row)
        return original_step(debug, address, thumb)

    guest_ui.CHECKPOINTS.update(POINTS)
    # These are original component descriptors, not replacement host services.
    present = {entry[0] for entry in guest_ui.COMPONENTS}
    extra = tuple(entry for entry in ((26, 'Channels'), (27, 'AudioControl'),
                                     (53, 'MediaPlayer')) if entry[0] not in present)
    mmi = next(i for i, entry in enumerate(guest_ui.COMPONENTS) if entry[0] == 2)
    guest_ui.COMPONENTS = guest_ui.COMPONENTS[:mmi] + extra + guest_ui.COMPONENTS[mmi:]
    Debugger.step_over_breakpoint = observe
    session = guest_ui.OriginalUISession(
        progress=lambda message: print(message, flush=True), default_theme=False,
        audio_output=args.audio_output,
        audio_capture_path=args.wav)
    report = {}

    def snapshot_mmi(label):
        if not args.trace_mmi or mmi_tcb is None:
            return
        raw = session.d.read(mmi_tcb, 512)
        words = struct.unpack('<128I', raw)
        pointers = sorted({word for word in words
                           if mmi_stack - 16384 <= word <= mmi_stack + 4096})
        mmi_snapshots.append({'phase': label, 'tcb': hex(mmi_tcb),
            'native_pid': hex(mmi_pid), 'tcb_bytes': raw.hex(),
            'observed_stack': hex(mmi_stack),
            'stack_candidates': {hex(pointer): session.d.read(pointer, 1024).hex()
                                 for pointer in pointers}})

    try:
        session.start()
        mmi_pid = session.word(0x4c0bae24) & 0x7fff
        for _ in range(3):
            session.advance(1000)
        route = ROUTE if args.media == 'greeting' else ROUTE[:-1] + ('down', ROUTE[-1])
        for key in route:
            phase = 'key-' + key
            session.advance(100, key_event=(key, True))
            session.advance(300, key_event=(key, False))
        for _ in range(args.seconds):
            phase = f'play-{_+1}'
            if args.trace_mmi and _ == max(0, args.seconds - 1):
                # Observe only the late window: startup graphics RPCs would
                # otherwise fill the bounded samples before Back is pressed.
                for address in LATE_MMI_POINTS:
                    guest_ui.CHECKPOINTS[address] = MMI_POINTS[address]
                    session.d.breakpoint(address, thumb=True)
            before = session.word(0xf901001c)
            virtual_before = session.q.command('qom-get', {
                'path': '/machine', 'property': 'virtual-time-ns'})
            result = session.advance(1000)
            waits.append({'requested_ms': 1000, 'result': hex(result),
                          'counter_before': before, 'counter_after': session.word(0xf901001c),
                          'virtual_before_ns': virtual_before,
                          'virtual_after_ns': session.q.command('qom-get', {
                              'path': '/machine', 'property': 'virtual-time-ns'}),
                          'filter': session.d.read(session.wait_filter, 8).hex()})
        snapshot_mmi('before-back')
        if args.back_count and (args.wav or args.audio_output == 'coreaudio' or
                                args.trace_mmi or args.back_count == 2):
            for back in range(args.back_count):
                prefix = 'back' if back == 0 else f'back-{back+1}'
                phase = prefix + '-down'
                session.advance(100, key_event=('back', True))
                phase = prefix + '-up'
                session.advance(300, key_event=('back', False))
                for second in range(3 if args.trace_mmi else 1):
                    phase = f'after-{prefix}-{second+1}'
                    session.advance(1000)
        snapshot_mmi('after-back')
        state = session.d.read(0x4c040584, 0x48)
        base, _, consumer, producer = struct.unpack_from('<4I', state)
        ring = session.d.read(base, 13 * 24)
        report = session.report()
        report.update(queue_base=hex(base), queue_consumer=consumer,
                      queue_producer=producer, queue_state=state.hex(),
                      queue_entries=[ring[i:i+24].hex() for i in range(0, len(ring), 24)],
                      mailbox_registers=session.d.read(0xfa040000, 0xc0).hex(),
                      dma_register_bank=session.d.read(0xf2000100, 0x100).hex(),
                      midi_context=session.d.read(0x4c0467b0, 0x50).hex(),
                      observation_start='Mounted filesystem UI component startup',
                      pcm_generated=any(marker in session.q.diagnostics() for marker in (
                          'W800_DSP_PCM source=guest-midi-stream1', 'W800_DSP_AAC_PCM source=guest-stream4')),
                      hardware_diagnostics=session.q.diagnostics(),
                      target_media='Greeting.mid' if args.media == 'greeting' else 'Latin.mp3',
                      audio_model='Guest mailbox/DMA streams -> implemented device codec HLE -> QEMU PCM',
                      blocker='Original C55x instrument bank and remaining codec operations are not implemented')
        session.frame().save(str(Path(args.output).with_suffix('.png')))
    except BaseException:
        report = session.report()
        report['error'] = traceback.format_exc()
        if session.q:
            report['hardware_diagnostics'] = session.q.diagnostics()
        raise
    finally:
        session.close()
        if (args.wav and Path(args.wav).exists() and
                Path(args.wav).stat().st_mtime_ns != previous_wav_mtime):
            with wave.open(args.wav, 'rb') as capture:
                pcm = capture.readframes(capture.getnframes())
                samples = array('h', pcm)
                report['pcm_capture'] = {'path': str(Path(args.wav).resolve()),
                    'rate': capture.getframerate(), 'channels': capture.getnchannels(),
                    'frames': capture.getnframes(), 'sample_width': capture.getsampwidth(),
                    'nonzero_samples': sum(sample != 0 for sample in samples),
                    'peak': max((abs(sample) for sample in samples), default=0),
                    'sha256': hashlib.sha256(pcm).hexdigest()}
        report.update(events_observed=dict(counts), samples=rows, scheduler_waits=waits,
                      mmi_snapshots=mmi_snapshots, final_stream_writes=list(final_stream_writes))
        with open(args.output, 'w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        Debugger.step_over_breakpoint = original_step
    print(json.dumps(dict(counts)), flush=True)


if __name__ == '__main__':
    main()
