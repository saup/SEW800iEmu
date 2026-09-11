"""Capture original Stop MP3 stream records and their terminal lifecycle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import re

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui
from w800.backend import ROOT
from w800.tools.debug import Debugger


def validate_result(directory):
    directory=Path(directory)
    result=json.loads((directory/'result.json').read_text())
    log=(directory/'complete-diagnostics.log').read_text()
    compressed=bytearray()
    terminal=None
    records=0
    for row in result['records']:
        data=bytes.fromhex(row['data'])
        header=struct.unpack('<6H',data[:12])
        assert len(data)==12+header[5]*2, 'Native record length mismatch'
        records+=1
        if header[0]==2:
            terminal=header
            break
        assert header[0]==1, 'Unexpected native compressed record'
        compressed.extend(b''.join(data[i:i+2][::-1] for i in range(12,len(data),2)))
    source_hash=hashlib.sha256(compressed[:-1]).hexdigest()
    completion=re.findall(r'AAC_COMPLETE packets=734 decoded=(\d+) submitted=(\d+) reply=0e06 status=2',log)
    complete_offset=log.find('AAC_COMPLETE packets=734 ')
    release_offset=log.find('AAC_RESOURCE operation=4 capacity=0',complete_offset)
    next_offset=log.find('AAC_RESOURCE operation=3 capacity=1024',release_offset)
    next_pcm_offset=log.find('AAC_PCM source=guest-stream4',next_offset)
    session=result['session']
    checks={
        'original_mpeg_bytes_match': len(compressed)==153392 and source_hash==
            'f882d7fe29058bb4105e5a7a7cab199763441699d2d3db90bdb74a1f792505b2',
        'terminal_record_and_one_zero_pad': bool(terminal) and terminal[1:3]==(0,1) and terminal[5]==0 and compressed[-1:]==b'\0',
        'one_alignment_byte_consumed': log.count('MP3_ALIGNMENT bytes=1 value=00 terminal=1')==1,
        'all_734_mpeg_frames_and_fir_tail_drained': completion==[('422784','845630')],
        'original_release_after_completion': 0<=complete_offset<release_offset,
        'original_next_track_has_pcm': release_offset<next_offset<next_pcm_offset,
        'original_source_and_validation_unchanged': session['source_unchanged'] and
            not session['guest_instruction_patches'] and not session['validation_result_changes'],
        'no_held_physical_keys': not result['held_keys'],
        'no_decoder_error': not any(x in log for x in ('UNSUPPORTED','DECODE_ERROR','CONFIG_ERROR')),
    }
    summary=dict(result='PASS' if all(checks.values()) else 'FAIL',
        qemu_sha256=session['qemu_sha256'],source_mpeg_sha256=source_hash,
        source_mpeg_bytes=len(compressed)-1,transport_bytes=len(compressed),
        records_through_first_eof=records,encoded_frames=734,decoded_frames=422784,
        decoded_rate=22050,submitted_frames=845630,output_rate=44100,
        real_fir_history_frames=31,checks=checks)
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if not all(checks.values()):raise AssertionError(summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'reports/walkman-stop-eof')
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    app=QApplication.instance() or QApplication([])
    directory=args.directory
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'device.wav').unlink(missing_ok=True)
    session=guest_ui.OriginalUISession(default_theme=False,audio_capture_path=directory/'device.wav')
    previous=Debugger.step_over_breakpoint
    old_points=dict(guest_ui.CHECKPOINTS)
    records=[]
    phase='startup'
    fd=None
    result={}
    def observe(debugger,address,thumb=False):
        if address==0x448d5714:
            regs=debugger.registers()
            if regs[0]==4 and 0<regs[2]<=1024:
                data=debugger.read(regs[1],regs[2]*2)
                records.append(dict(phase=phase,words=regs[2],data=data.hex()))
        return previous(debugger,address,thumb)
    def key(value):
        session.advance(120,(value,True));session.advance(450,(value,False))
    def snapshot():
        result[phase]=dict(records=len(records),capture_bytes=(directory/'device.wav').stat().st_size,
                           messages=list(session.messages)[-20:])
        session.frame().save(str(directory/f'{phase}.png'))
    try:
        guest_ui.CHECKPOINTS[0x448d5714]='Original compressed stream record'
        Debugger.step_over_breakpoint=observe
        session.start()
        fd=(session.q.directory/'stderr.log').open()
        phase='start'
        for value in ('down','select','down','down','select','select'):key(value)
        snapshot()
        phase='natural-end'
        for i in range(25*25):session.advance(40)
        snapshot()
        phase='pause'
        key('soft_left')
        for i in range(25):session.advance(40)
        snapshot()
        phase='resume'
        key('soft_left')
        for i in range(50):session.advance(40)
        snapshot()
        result['session']=session.report()
        result['held_keys']=sorted(session.q.pressed_keys)
        result['result']='OBSERVED'
    except Exception as error:
        result.update(result='FAIL',error=repr(error))
        raise
    finally:
        result['records']=records
        session.close()
        if fd:
            fd.seek(0);(directory/'complete-diagnostics.log').write_text(fd.read());fd.close()
        Debugger.step_over_breakpoint=previous
        guest_ui.CHECKPOINTS.clear();guest_ui.CHECKPOINTS.update(old_points)
        (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({key:value for key,value in result.items() if key not in ('session','records')}),flush=True)
    if args.verify:
        print(json.dumps(validate_result(directory)),flush=True)


if __name__=='__main__':main()
