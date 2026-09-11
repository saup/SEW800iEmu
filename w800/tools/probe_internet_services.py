"""Inspect native browser address entry with physical keys in a disposable VM."""
import json
import sys
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture, trace_calls

out=ROOT/'reports/internet-services'
out.mkdir(exist_ok=True)
with OriginalUISession(default_theme=True,start_phone_standby=True,host_web=True) as s:
    press(s,'select')
    press(s,'soft_right','up','select')
    for _ in range(12):s.advance(1000)
    capture(s,out,'internet-services')
    (out/'report.json').write_text(json.dumps(s.report(),indent=2))
    for line in sys.stdin:
        cmd=json.loads(line)
        if cmd.get('close'):break
        if 'trace' in cmd:trace_calls(s,set(cmd['trace']),'profile_calls')
        if 'call' in cmd:s.call_on_mmi(*cmd['call'])
        for key in cmd.get('keys',[]):press(s,key)
        for _ in range(cmd.get('seconds',0)):s.advance(1000)
        capture(s,out,cmd.get('label','frame'))
        (out/'report.json').write_text(json.dumps(s.report(),indent=2))
