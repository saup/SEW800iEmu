"""Inspect name-only native standby updates in one disposable offline VM."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tools.probe_menu_services import capture, press


def main():
    directory = ROOT / 'reports/virtual-network-native'
    directory.mkdir(exist_ok=True)
    session = OriginalUISession(default_theme=True)
    try:
        session.start()
        previous = capture(session, directory, '00-startup')
        book = session.call_on_mmi(0x44e9202c)
        print(json.dumps({'standby': hex(book), 'data': session.d.read(book, 0x160).hex()}), flush=True)
        for index, line in enumerate(sys.stdin, 1):
            cmd = json.loads(line)
            if cmd.get('close'): break
            for key in cmd.get('keys', []): press(session, key)
            if 'name' in cmd:
                name = cmd['name']
                pointer = session.files.stage(15000, name.encode('utf-16le') + b'\0\0')
                text = session.call_on_mmi(0x44edd944, pointer, 0, len(name))
                print(json.dumps({'text': hex(text)}), flush=True)
                session.call_on_mmi(0x44e93c14, text, 0)
                print(json.dumps({'stored': hex(session.word(book + 0x64))}), flush=True)
            if 'call' in cmd:
                result = session.call_on_mmi(int(cmd['call'], 0), *[int(x, 0) for x in cmd.get('args', [])])
                print(json.dumps({'result': hex(result)}), flush=True)
            for _ in range(cmd.get('seconds', 0)): session.advance(1000)
            label = cmd.get('label', f'{index:02d}-frame')
            previous = capture(session, directory, label, previous)
    finally:
        report = session.report() if session.q else {}
        session.close()
        report['owned_vm_closed'] = session.q is None or session.q.process is None
        (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__': main()
