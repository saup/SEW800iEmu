"""Inspect the original profile wizard with physical keys in a disposable VM."""
import json
import sys
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture, trace_calls

out=ROOT/'reports/phone-profile'
out.mkdir(exist_ok=True)
with OriginalUISession(default_theme=True,start_phone_standby=True) as s:
    press(s,'select')
    p=s.files.stage(14000,b'http://example.com/\0')
    s.call_on_mmi(0x450bec44,0x44245520,0,6,p)
    for _ in range(5):s.advance(1000)
    capture(s,out,'profile-question')
    for line in sys.stdin:
        cmd=json.loads(line)
        if cmd.get('close'):break
        if 'trace' in cmd:trace_calls(s,set(cmd['trace']),'profile_calls')
        if 'call' in cmd:s.call_on_mmi(*cmd['call'])
        for key in cmd.get('keys',[]):press(s,key)
        for _ in range(cmd.get('seconds',0)):s.advance(1000)
        capture(s,out,cmd.get('label','frame'))
        (out/'report.json').write_text(json.dumps(s.report(),indent=2))
