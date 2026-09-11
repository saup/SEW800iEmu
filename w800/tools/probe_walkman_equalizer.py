"""Capture original Walkman equalizer packets through a bounded physical UI probe."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui
from w800.backend import KEY_QCODES, ROOT
from w800.tools.debug import Debugger


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default=str(ROOT/'reports/walkman-equalizer-coefficients'))
    args=parser.parse_args()
    directory=Path(args.directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    # Each invocation owns a fresh capture; a previous run's WAV must not
    # appear as new PCM before this run opens its first native audio voice.
    (directory/'device.wav').unlink(missing_ok=True)
    app=QApplication.instance() or QApplication([])
    session=guest_ui.OriginalUISession(default_theme=False,audio_capture_path=directory/'device.wav')
    previous=Debugger.step_over_breakpoint
    previous_points=dict(guest_ui.CHECKPOINTS)
    packets=[]
    actions=[]
    phase='startup'
    def observe(debugger,address,thumb=False):
        if address==0x447367a8:
            regs=debugger.registers()
            if regs[0]==12:
                data=debugger.read(regs[1],6)
                opcode,target,enabled=struct.unpack('<3H',data)
                if opcode==0x090e:
                    size=22 if enabled else 6
                    words=list(struct.unpack('<'+'H'*(size//2),debugger.read(regs[1],size)))
                    packets.append(dict(phase=phase,words=words))
        return previous(debugger,address,thumb)
    def save():
        session.frame().save(str(directory/f'{len(actions):02d}-{phase}.png'))
        row=dict(phase=phase,packets=len(packets),pressed_keys=sorted(session.q.pressed_keys),capture_bytes=(directory/'device.wav').stat().st_size if (directory/'device.wav').exists() else 0,
                 native_bands=session.d.read(0x4c38e45c,30).hex(),messages=list(session.messages)[-15:])
        actions.append(row)
        report=dict(result='OBSERVED',actions=actions,packets=packets,diagnostics=session.q.diagnostics(),session=session.report())
        (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(row),flush=True)
    try:
        guest_ui.CHECKPOINTS[0x447367a8]='Original equalizer packet sender'
        Debugger.step_over_breakpoint=observe
        session.start()
        save()
        for index,line in enumerate(sys.stdin):
            if index>=40: raise ValueError('Action limit reached')
            command=json.loads(line)
            if command.get('finish'):break
            phase=command.get('label',f'action-{index}')
            if not phase.replace('-','').replace('_','').isalnum():raise ValueError('Invalid label')
            for key in command.get('keys',[]):
                if key not in KEY_QCODES:raise ValueError('Unknown physical key')
                session.advance(120,(key,True));session.advance(450,(key,False))
            seconds=command.get('seconds',0)
            if not isinstance(seconds,int) or not 0<=seconds<=20:raise ValueError('Duration out of bounds')
            for _ in range(seconds*25):session.advance(40)
            save()
    finally:
        session.close()
        if (directory/'device.wav').exists():
            with wave.open(str(directory/'device.wav'),'rb') as audio:
                data=audio.readframes(audio.getnframes())
                pcm=dict(rate=audio.getframerate(),channels=audio.getnchannels(),frames=audio.getnframes(),nonzero_bytes=sum(value!=0 for value in data))
                (directory/'pcm.json').write_text(json.dumps(pcm,indent=2)+'\n')
        Debugger.step_over_breakpoint=previous
        guest_ui.CHECKPOINTS.clear();guest_ui.CHECKPOINTS.update(previous_points)


if __name__=='__main__':main()
