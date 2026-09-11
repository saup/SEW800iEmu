"""Inspect original diagnostic menus in a disposable local VM."""
import json
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture

def main():
    directory = ROOT / 'reports/developer-menus'
    directory.mkdir(exist_ok=True)
    with OriginalUISession(default_theme=True, start_phone_standby=True) as s:
        press(s, 'select', 'right', '*', 'left', 'left', '*', 'left', '*')
        capture(s, directory, 'service')
        press(s, 'select')
        capture(s, directory, 'info')
        press(s, 'back', 'down', 'select')
        capture(s, directory, 'tests')
        for n in range(3):
            press(s, *(['down'] * 5))
            capture(s, directory, 'tests-scroll-' + str(n))
        press(s, 'back', 'down', 'select')
        capture(s, directory, 'labels')
        press(s, 'back', 'back')
        pointer=s.files.stage(15000, 'debugmenu\0'.encode('utf-16le'))
        s.call_on_mmi(0x45059c7c, pointer)
        s.advance(1000)
        capture(s, directory, 'debugmenu-launch')
        (directory / 'result.json').write_text(json.dumps(s.report(), indent=2))
        print('Finished; source unchanged:',s.report()['source_unchanged'],flush=True)
if __name__=='__main__': main()
