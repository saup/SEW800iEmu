"""Exercise the asynchronous host bridge through native URL entry."""
import json
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture

def main():
    out=ROOT/'reports/browser-host-bridge'
    out.mkdir(exist_ok=True)
    with OriginalUISession(default_theme=True,start_phone_standby=True,host_web=True) as s:
        press(s,'select')
        url=s.files.stage(14000,b'https://example.com/\0')
        s.call_on_mmi(0x450bec44,0x44245520,0,6,url)
        for _ in range(15):s.advance(1000)
        capture(s,out,'host-page')
        press(s,'select')
        for _ in range(15):s.advance(1000)
        capture(s,out,'follow-link')
        (out/'report.json').write_text(json.dumps(s.report(),indent=2))
if __name__=='__main__':main()
