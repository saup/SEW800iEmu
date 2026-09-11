"""Run the existing Contacts text/storage regression after native game startup.

Uses a fresh private QEMU and separate report directory. No network requests,
source firmware writes, direct contact writes or permission-state changes.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from ..tests import test_contacts_keypad as contacts
from ..guest_ui import OriginalUISession
from ..java_services import notify_application_startup, provision_bundled_games


class LifecycleSession(OriginalUISession):
    def __init__(self, *args, **kwargs):
        kwargs['start_screen'] = 'startup'
        super().__init__(*args, **kwargs)

    def start(self):
        super().start()
        self.advance(120, ('select', True))
        self.advance(450, ('select', False))
        notify_application_startup(self)
        self.advance(1000)
        provision_bundled_games(self)
        return self


def main():
    report_root = Path(__file__).resolve().parents[1] / 'reports/java-contacts-lifecycle'
    (report_root / 'reports').mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(contacts.ContactsKeypadTests)
    with patch.object(contacts, 'OriginalUISession', LifecycleSession), \
            patch.object(contacts, 'ROOT', report_root):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
