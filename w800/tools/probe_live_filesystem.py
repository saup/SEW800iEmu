"""Private no-audio live filesystem and original installer observation."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.live_filesystem import LiveFilesystem


def main():
    app = QApplication.instance() or QApplication([])
    directory = ROOT / 'reports/live-filesystem-probe'
    directory.mkdir(exist_ok=True)
    actions = []
    with OriginalUISession(default_theme=True, audio_output='none') as session:
        files = LiveFilesystem(session)
        def capture(label):
            session.frame().save(str(directory / (label + '.png')))
            report = dict(actions=actions, session=session.report())
            (directory / (label + '.json')).write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(dict(label=label, messages=list(session.messages)[-15:])), flush=True)
        capture('00-startup')
        for index, line in enumerate(sys.stdin, 1):
            action = json.loads(line)
            if action.get('close'):
                break
            actions.append(action)
            for key in action.get('keys', []):
                session.advance(120, (key, True))
                session.advance(450, (key, False))
            if 'copy' in action:
                data = files.read_file(action['copy']['source'])
                print(json.dumps(files.upload_bytes(action['copy']['destination'], data,
                                                    action['copy'].get('replace', False))), flush=True)
            if 'list' in action:
                print(json.dumps(files.list_directory(action['list'])), flush=True)
            if 'write' in action:
                print(json.dumps(files.upload_bytes(action['write']['path'], action['write']['text'].encode())), flush=True)
            if 'install' in action:
                print(json.dumps(files.begin_installation(action['install'])), flush=True)
            if action.get('notify'):
                files.notify_changed()
            for _ in range(action.get('wait_ms', 0) // 250):
                session.advance(250)
            capture(action.get('label', f'{index:02}'))


if __name__ == '__main__':
    main()
