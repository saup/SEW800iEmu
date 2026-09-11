"""Exercise read-only service codes using physical keys in disposable VMs."""
import json
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press

CASES = {
    'imei': list('*#06#'),
    'service': ['right', '*', 'left', 'left', '*', 'left', '*'],
    'lock_status': ['left', '*', '*', 'left'],
    'software_numeric': list('*#9999#'),
}

def main():
    directory = ROOT / 'reports/standby-codes'
    directory.mkdir(exist_ok=True)
    for name, keys in CASES.items():
        with OriginalUISession(default_theme=True, start_phone_standby=True) as session:
            press(session, 'select')
            session.frame().save(str(directory / (name + '-before.png')))
            before = list(session.messages)
            press(session, *keys)
            session.advance(1000)
            session.frame().save(str(directory / (name + '-after.png')))
            report = {'keys': keys, 'messages': list(session.messages),
                      'source_unchanged': session.report()['source_unchanged'],
                      'keys_released': not session.q.pressed_keys}
            (directory / (name + '.json')).write_text(json.dumps(report, indent=2))
            print(name, json.dumps(report), flush=True)

if __name__ == '__main__':
    main()
