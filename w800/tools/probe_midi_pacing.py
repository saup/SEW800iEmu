"""Compare original Greeting MIDI timing to its native DSP stream and CoreAudio."""
import hashlib
import argparse
import json
import os
import struct
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.trace_audio_hardware import ROUTE


def midi_events(data):
    if data[:4] != b'MThd':
        raise ValueError('Not SMF')
    header_size=struct.unpack_from('>I',data,4)[0]
    fmt,tracks,division=struct.unpack_from('>HHH',data,8)
    if division & 0x8000:
        raise ValueError('SMPTE division requires separate conversion')
    events=[];tempos=[];track_ends=[];offset=8+header_size
    def vlq(pos):
        value=0
        for _ in range(4):
            v=data[pos];pos+=1;value=(value<<7)|(v&127)
            if not v&128:return value,pos
        raise ValueError('Oversize VLQ')
    for track in range(tracks):
        if data[offset:offset+4]!=b'MTrk':raise ValueError('Missing track')
        size=struct.unpack_from('>I',data,offset+4)[0];pos=offset+8;end=pos+size
        ticks=0;running=None
        while pos<end:
            delta,pos=vlq(pos);ticks+=delta
            status=data[pos]
            if status&128:pos+=1
            elif running is not None:status=running
            else:raise ValueError('Missing running status')
            if status==255:
                kind=data[pos];pos+=1;length,pos=vlq(pos);payload=data[pos:pos+length];pos+=length
                if kind==81:
                    if length!=3:raise ValueError('Bad tempo')
                    tempos.append(dict(tick=ticks,us_per_quarter=int.from_bytes(payload,'big'),track=track))
                elif kind==47:track_ends.append(dict(track=track,tick=ticks))
                running=None
            elif status in (240,247):
                length,pos=vlq(pos);pos+=length;running=None
            elif 128<=status<240:
                count=1 if status&240 in (192,208) else 2
                payload=data[pos:pos+count];pos+=count;running=status
                events.append(dict(tick=ticks,track=track,status=status,data1=payload[0],data2=payload[1] if count==2 else 0))
            else:raise ValueError(f'Unexpected status {status:x}')
        offset=end
    tempos.sort(key=lambda e:(e['tick'],e['track']))
    def seconds(tick):
        previous=0;tempo=500000;microseconds=0
        for t in tempos:
            if t['tick']>tick:break
            microseconds+=(t['tick']-previous)*tempo/division
            previous=t['tick'];tempo=t['us_per_quarter']
        return (microseconds+(tick-previous)*tempo/division)/1e6
    for row in events+track_ends:row['seconds']=seconds(row['tick'])
    events.sort(key=lambda e:(e['tick'],e['track']))
    return dict(format=fmt,tracks=tracks,division=division,tempos=tempos,
                events=events,track_ends=track_ends)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-directory',default='midi-pacing')
    parser.add_argument('--trace-events',action=argparse.BooleanOptionalAction,default=True)
    parser.add_argument('--interval',type=int,default=100)
    parser.add_argument('--count',type=int,default=120)
    parser.add_argument('--capture-frames',action='store_true')
    parser.add_argument('--audio-debug-stop-grace-us',type=int,default=20000)
    args=parser.parse_args()
    directory=ROOT/'reports'/args.report_directory;directory.mkdir(parents=True,exist_ok=True)
    session=OriginalUISession(default_theme=False,audio_output='coreaudio',
        audio_debug_stop_grace_us=args.audio_debug_stop_grace_us,
        progress=lambda message:print(message,flush=True))
    trace=[];windows=[];phase='startup';start=time.monotonic();source={}
    point=0x447367a8;previous=guest_ui.CHECKPOINTS.copy()
    def state():
        return dict(wall_seconds=time.monotonic()-start,
            virtual_ns=session.q.command('qom-get',{'path':'/machine','property':'virtual-time-ns'}),
            native_ticks=session.word(0x4200f264))
    try:
        session.start()
        path=session.files.stage(0,'/tpa/user/audio/Greeting.mid'.encode('utf-16le')+b'\0\0')
        fd=session.invoke(0x45105f48,path,1,0x1b6)
        if fd&0x80000000:raise ValueError('MIDI source open failed')
        data=bytearray()
        try:
            for _ in range(3):
                ptr=session.files.buffer+1024;count=session.invoke(0x451063d4,fd,ptr,8192)
                if not 0<=count<=8192:raise ValueError('MIDI source read failed')
                data.extend(session.d.read(ptr,count))
                if not count:break
        finally:session.invoke(0x45106254,fd,0)
        (directory/'Greeting.mid').write_bytes(data)
        source=midi_events(data);source['sha256']=hashlib.sha256(data).hexdigest()
        (directory/'source-events.json').write_text(json.dumps(source,indent=2)+'\n')
        print(json.dumps(dict(source_events=len(source['events']),tempos=source['tempos'],last_seconds=max(e['seconds'] for e in source['events']))),flush=True)
        original_registers=session.d.registers
        def registers():
            r=original_registers()
            if r[15]==point and r[0] in (9,12,20):
                opcode=struct.unpack('<H',session.d.read(r[1],2))[0]
                if opcode in (0x0801,0x0600,0x0608,0x060a,0x0900):
                    words=struct.unpack('<H',session.d.read(r[1]-4,2))[0]
                    if opcode == 0x0801:
                        # The allocator's buffer extent includes padding;
                        # stream count word describes the actual message.
                        words=2+struct.unpack('<H',session.d.read(r[1]+2,2))[0]
                    trace.append(dict(phase=phase,channel=r[0],opcode=opcode,
                        packet=session.d.read(r[1],min(words*2,4096)).hex(),**state()))
            return r
        if args.trace_events:
            session.d.registers=registers
            guest_ui.CHECKPOINTS[point]='MIDI pacing DSP send'
            session.d.breakpoint(point,thumb=True)
        for key in ROUTE:
            phase='key-'+key;press(session,key)
        phase='play'
        first=state()
        for index in range(args.count):
            before=state();session.advance(args.interval)
            if args.capture_frames:session.frame()
            after=state()
            windows.append(dict(index=index,before=before,after=after))
            if index%20==19:print(json.dumps(dict(window=index+1,events=len(trace),state=after)),flush=True)
        phase='back';press(session,'back');session.advance(1000)
        report=session.report();report['diagnostics']=session.q.diagnostics()
    except Exception as error:
        report=session.report();report['error']=repr(error)
        if session.q:report['diagnostics']=session.q.diagnostics()
    finally:
        # Keep the already-open private log inode through backend cleanup:
        # CoreAudio's final HAL counters are emitted only when QEMU closes.
        log_path=session.q.directory/'stderr.log' if session.q else None
        log=log_path.open() if log_path and log_path.exists() else None
        try:
            session.close()
            if log:
                complete_log=log.read()
                (directory/'complete-diagnostics.log').write_text(complete_log)
                report['closed_diagnostics']=complete_log
        finally:
            if log:log.close()
            guest_ui.CHECKPOINTS.clear();guest_ui.CHECKPOINTS.update(previous)
    report.update(source=source,midi_trace=trace,pacing_windows=windows,
                  settings=vars(args))
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(error=report.get('error'),trace=len(trace),windows=len(windows))),flush=True)


if __name__=='__main__':main()
