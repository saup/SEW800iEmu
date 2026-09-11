"""Exercise native browser initialization independently in a disposable VM."""
import json
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800 import guest_ui
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture, trace_calls

def main():
    out=ROOT/'reports/browser-host-startup'
    out.mkdir(exist_ok=True)
    s=OriginalUISession(default_theme=True,start_phone_standby=True)
    observations=[]
    try:
        s.start();press(s,'select')
        for _ in range(3):s.advance(1000)
        observations.append({'automatic_startup':s.provenance.get('browser_startup'), 'state':s.d.read(0x4c391261,2).hex()})
        p=s.files.stage(14000,b'http://example.com/\0')
        s.call_on_mmi(0x450bec44,0x44245520,0,6,p)
        result=s.mmi_call_result
        
        for _ in range(8):s.advance(1000)
        observations.append({'url_result':hex(result),'state':s.d.read(0x4c391261,16).hex()})
        capture(s,out,'native-browser')
    except Exception as exc:
        observations.append({'error':str(exc),'state':s.d.read(0x4c391261,16).hex() if s.d else None,'call_entered':bool(s.pending_menu and 'saved' in s.pending_menu)})
        print(str(exc),flush=True)
    finally:
        (out/'stalled-ram.bin').write_bytes(s.d.read(0x4c000000,0x800000))
        (out/'startup.json').write_text(json.dumps({'observations':observations,'report':s.report() if s.q else {}},indent=2))
        s.close()
if __name__=='__main__':main()
