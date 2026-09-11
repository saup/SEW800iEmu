"""Observe original SMS UI with physical keys in a private silent phone VM."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'reports/sms-compose')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    actions = []
    started = time.monotonic()
    session = OriginalUISession(default_theme=True, audio_output='none')
    try:
        session.start()
        def capture(label):
            if not label or Path(label).name != label:
                raise ValueError('Capture label must be one filename component')
            session.frame().save(str(args.directory / (label + '.png')))
            result = dict(actions=actions, elapsed=time.monotonic()-started, session=session.report())
            result['message_type_registry'] = session.d.read(0x4c15325c, 128).hex()
            (args.directory / (label + '.json')).write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(dict(label=label, messages=list(session.messages)[-30:])), flush=True)
        capture('00-startup')
        for index, line in enumerate(sys.stdin, 1):
            action = json.loads(line)
            if action.get('close'):
                break
            if index > 200 or len(action.get('keys', [])) > 100:
                raise ValueError('Probe action limit exceeded')
            actions.append(action)
            if action.get('initialize_sms'):
                before = session.word(0x4c153260)
                if before:
                    raise RuntimeError('Original SMS provider is already registered')
                result = session.call_on_mmi(0x44fb7db8)
                print(json.dumps(dict(initialization_result=result,
                                      provider=hex(session.word(0x4c153260)))), flush=True)
                session.advance(1000)
            if action.get('activate_sms'):
                result = session.call_on_mmi(0x44fb7ea0)
                print(json.dumps(dict(activation_result=result)), flush=True)
                session.advance(1000)
            for key in action.get('keys', []):
                remaining = action.get('hold_ms', 120)
                session.advance(min(remaining,1000), (key, True))
                remaining -= min(remaining,1000)
                while remaining:
                    count = min(remaining,1000)
                    session.advance(count)
                    remaining -= count
                session.advance(action.get('up_ms',450), (key, False))
            remaining = action.get('wait_ms',0)
            while remaining:
                count = min(remaining,1000)
                session.advance(count)
                remaining -= count
            capture(action.get('label',f'{index:02}'))

    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
        if session.d:
            report['registers'] = [hex(x) for x in session.d.registers()]
        if session.q:
            report['diagnostics'] = session.q.diagnostics()
        (args.directory / 'failure.json').write_text(json.dumps(report, indent=2)+'\n')
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
