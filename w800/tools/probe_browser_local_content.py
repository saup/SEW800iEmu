"""Test host-fetched HTML in the original firmware's local-page renderer."""
import json
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.host_web import HostWeb
from w800.live_filesystem import LiveFilesystem
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture, trace_calls

def main():
    out=ROOT/'reports/browser-host-local'
    out.mkdir(exist_ok=True)
    with HostWeb() as host:
        response=host.fetch('https://example.com/').result(timeout=15)
    with OriginalUISession(default_theme=True,start_phone_standby=True) as s:
        press(s,'select')
        for _ in range(3):s.advance(1000)
        upload=LiveFilesystem(s).upload_bytes('/tpa/user/other/host-web-test.html',response.body,replace=True)
        p=s.files.stage(14000,b'file:///tpa/user/other/host-web-test.html\0')
        s.call_on_mmi(0x450bec44,0x44245520,0,6,p)
        for _ in range(10):s.advance(1000)
        capture(s,out,'host-page')
        trace_calls(s,{0x450bec44,0x44b7537e},'browser_link_calls')
        from w800.guest_ui import LOGGERS, cstring
        original_registers = s.d.registers
        def registers():
            r = original_registers()
            if r[15] in LOGGERS and 'Req_ContentOperation' in cstring(s.d,r[0]):
                s.provenance.setdefault('content_calls',[]).append([hex(x) for x in r])
            return r
        s.d.registers = registers
        press(s,'select')
        for _ in range(5):s.advance(1000)
        capture(s,out,'follow-link')
        (out/'report.json').write_text(json.dumps({'fetch':{'url':response.url,'status':response.status,'bytes':len(response.body)},'upload':upload,'report':s.report()},indent=2))
if __name__=='__main__':main()
