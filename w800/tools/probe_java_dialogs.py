"""Inspect the original orange-theme installer in a private controlled session.

The private host reentrancy guard defers automatic provisioning so each native
dialog can be observed. Original Phone mode, lifecycle, installer and keys run
normally; no guest permission or validation state is supplied.
"""
import code
import json
from pathlib import Path

from PySide6.QtWidgets import QApplication
from ..guest_ui import OriginalUISession
from .. import java_services


def main():
    app = QApplication.instance() or QApplication([])
    session = OriginalUISession(default_theme=True, progress=lambda s: print(s, flush=True))
    output = {'experiment': 'Original orange-theme native installer dialog inspection'}
    try:
        session.start()
        session._starting_application_services = True

        def press(key):
            output.setdefault('physical_keys', []).append(key)
            session.advance(120, (key, True))
            session.advance(450, (key, False))
            session.frame().save('w800/reports/java-orange-dialog-current.png')

        press('select')
        java_services.notify_application_startup(session)
        session.advance(1000)
        record = java_services.begin_bundled_game_installation(session, 'QuadraPop')
        session.advance(500)
        session.frame().save('w800/reports/java-orange-save-games.png')
        code.interact(local={'session': session, 'press': press, 'record': record,
                             'output': output, 'java_services': java_services})
    finally:
        output['session'] = session.report()
        Path('w800/reports/java-orange-dialogs.json').write_text(json.dumps(output, indent=2) + '\n')
        session.close()


if __name__ == '__main__':
    main()
