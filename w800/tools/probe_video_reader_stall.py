"""Observe native DemoTour at GUI cadence, then inspect a bounded late window."""
import argparse
import collections
import hashlib
import json
import os
import struct
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


WRITER = 0x4488dc84
LATE_POINTS = {
    WRITER: 'writer', 0x4488dcde: 'late comparison',
    0x4488de30: 'sample-reader result', 0x4488d5d8: 'decoder indication',
    0x448d55d2: 'stream available words', 0x4488ef64: 'queue-reader result',
    0x4488d22c: 'queue reader', 0x447367a8: 'DSP send',
}
REFILL_POINTS = {
    0x448d52e4: 'credit received', 0x4488dfb0: 'credit callback',
    0x4488b71e: 'credit task dispatch', 0x4488cc30: 'timer179 dispatch',
    0x4488cdb8: 'timer17a dispatch', 0x4488cdb0: 'timer179 arm',
    0x4488de8c: 'writer result', 0x4488cb64: 'decoder status',
    0x4488bf58: 'sync request', 0x4488c4cc: 'queue input request',
    0x4488c658: 'queue input accepted',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='video-reader-stall')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--coreaudio', action='store_true')
    parser.add_argument('--trace-refill', action='store_true',
                        help='Observe native refill events throughout playback; adds debugger overhead')
    parser.add_argument('--refill-after-ms', type=int, default=0,
                        help='Delay refill breakpoints until this playback window')
    parser.add_argument('--sparse-refill', action='store_true',
                        help='Limit refill observations to credit, writer, status, sync and accepted data')
    args = parser.parse_args()
    directory = ROOT / 'reports' / args.directory
    directory.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(default_theme=True,
        audio_output='coreaudio' if args.coreaudio else 'none',
        audio_capture_path=None if args.coreaudio else directory / 'audio.wav',
        progress=lambda value: print(value, flush=True))
    previous = guest_ui.CHECKPOINTS.copy()
    phase, events, samples, contexts = 'startup', [], [], {}
    report, log = {}, None
    started = time.monotonic()
    refill_points = {pc: name for pc, name in REFILL_POINTS.items()
                     if not args.sparse_refill or pc in (
                         0x448d52e4, 0x4488de8c, 0x4488cb64,
                         0x4488bf58, 0x4488c658)}
    refill_started = False

    def enable_refill():
        nonlocal refill_started
        guest_ui.CHECKPOINTS.update(refill_points)
        for pc in refill_points:
            session.d.breakpoint(pc, thumb=True)
        refill_started = True

    def memory(pointer, size):
        if 0x4c000000 <= pointer <= 0x4c800000 - size:
            return session.d.read(pointer, size).hex()
        return None

    def snapshot(index):
        # Original AAC task global44...cd14 loads4c07a25c+4. Its active
        # media object owns these embedded reader/writer records.
        media = session.word(0x4c07a260)
        if 0x4c000000 <= media <= 0x4c7ffcc0:
            contexts.update(media=media, writer=media+0x2f4,
                            sample_reader=media+0x210)
            queue = session.word(media+0x2c0)
            if 0x4c000000 <= queue <= 0x4c7ffcc0:
                contexts['queue'] = queue
        else:
            contexts.clear()
        frame = session.frame()
        frame.save(str(directory / f'frame-{index:04}.png'))
        row = dict(index=index, phase=phase, wall=time.monotonic()-started,
            virtual_ns=session.q.command('qom-get', {
                'path': '/machine', 'property': 'virtual-time-ns'}),
            tick=session.word(0x4200f264), lcd=session.frames_completed,
            image_hash=hashlib.sha256(frame.constBits().tobytes()).hexdigest(),
            native_stream_table=memory(session.word(0x4c046bfc), 24 * 8),
            contexts={name: dict(pointer=hex(pointer), bytes=memory(pointer, 0x340))
                      for name, pointer in contexts.items()},
            diagnostic_tail=session.q.diagnostics().splitlines()[-8:])
        samples.append(row)
        print(json.dumps({k:row[k] for k in ['index','phase','wall','tick','lcd']}),flush=True)

    try:
        session.start()
        log = (session.q.directory / 'stderr.log').open()
        original = session.d.registers

        def registers():
            values = original()
            pc = values[15]
            if args.trace_refill and pc in refill_points:
                packet = memory(session.word(values[13]+4), 8) if pc == 0x448d52e4 else None
                if packet and int.from_bytes(bytes.fromhex(packet[:4]), 'little') != 0x0504:
                    return values
                media = session.word(0x4c07a260)
                row = dict(point=REFILL_POINTS[pc], phase=phase,
                    wall=time.monotonic()-started, registers=[hex(x) for x in values],
                    tick=session.word(0x4200f264), packet=packet)
                if 0x4c000000 <= media <= 0x4c7ffcc0:
                    row['media'] = memory(media+0x2ac, 0x60)
                    queue = session.word(media+0x2c0)
                    row['queue'] = memory(queue, 0x28)
                    if pc == 0x4488de8c:
                        row['reader'] = memory(media+0x210, 0x60)
                        row['queue_head'] = memory(session.word(queue), 0x20) if row['queue'] else None
                        row['stream'] = memory(session.word(0x4c046bfc)+4*24, 24)
                        row['stack'] = memory(values[13], 0x4c)
                if pc == 0x4488cb64:
                    row['status'] = memory(session.word(values[0]+4), 8)
                elif pc in (0x4488bf58, 0x4488c4cc):
                    row['request'] = memory(values[0], 0x20)
                events.append(row)
            if pc not in LATE_POINTS or (pc == 0x447367a8 and values[0] not in (18,20)):
                return values
            row = dict(point=LATE_POINTS[pc], phase=phase,
                wall=time.monotonic()-started, registers=[hex(x) for x in values])
            if pc == WRITER:
                contexts.update(media=values[0], writer=values[3])
                row['stack'] = memory(values[13], 24)
            elif pc == 0x4488dcde:
                row['stack'] = memory(values[13], 0x4c)
            elif pc in (0x4488d5d8,0x447367a8):
                row['packet'] = memory(values[1],24)
            elif pc == 0x4488d22c:
                contexts['queue'] = values[0]
                row['queue'] = memory(values[0],0x20)
            elif pc == 0x4488ef64:
                contexts['sample_reader'] = values[4]
                row['reader'] = memory(values[4],0x60)
            events.append(row)
            return values

        session.d.registers = registers
        guest_ui.CHECKPOINTS[WRITER] = LATE_POINTS[WRITER]
        session.d.breakpoint(WRITER,thumb=True)
        for key in ('select','down','left','select','down','select','down','select'):
            press(session,key)
        session.advance(1000)
        phase='play'
        started=time.monotonic()
        if args.trace_refill and not args.refill_after_ms:
            enable_refill()
        session.advance(100,('soft_left',True))
        session.advance(100,('soft_left',False))
        session.d.breakpoint(WRITER,enabled=False,thumb=True)
        guest_ui.CHECKPOINTS.pop(WRITER,None)
        snapshot(0)
        for index in range(1,args.seconds*25+1):
            if args.trace_refill and not refill_started and index*40 >= args.refill_after_ms:
                enable_refill()
            session.advance(40)
            session.frame()
            if index%125==0:
                snapshot(index)
        phase='bounded-late-trace'
        guest_ui.CHECKPOINTS.update(LATE_POINTS)
        for pc in LATE_POINTS:
            session.d.breakpoint(pc,thumb=True)
        for index in range(100):
            session.advance(40)
        snapshot(args.seconds*25+100)
        report['before_back_diagnostics']=session.q.diagnostics()
        phase='back'
        press(session,'back')
        snapshot(args.seconds*25+101)
        report['session']=session.report()
    except Exception as error:
        report['error']=repr(error)
        report['session']=session.report()
    finally:
        session.close()
        if log:
            (directory/'diagnostics.log').write_text(log.read())
            log.close()
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous)
    report.update(samples=samples, reader_trace=events,
        late_counts=dict(collections.Counter(row['point'] for row in events
                         if row['phase']=='bounded-late-trace')))
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(error=report.get('error'),late_counts=report['late_counts'])),flush=True)


if __name__=='__main__':
    main()
