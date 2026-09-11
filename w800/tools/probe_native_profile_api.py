"""Provision a native data account/profile through the original service APIs."""
import json
import struct
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import capture, trace_calls

out=ROOT/'reports/native-profile-api'
out.mkdir(exist_ok=True)
s=OriginalUISession(default_theme=True,start_phone_standby=True)
rows=[]
def call(fn,*args):
    result=s.call_on_mmi(fn,*args)
    rows.append({'function':hex(fn),'args':[hex(a) for a in args],'result':hex(result)})
    print(rows[-1],flush=True)
    return result
try:
    s.start();press(s,'select')
    trace_calls(s,{0x44f3de62,0x44f3de64,0x44f3de78,0x44f3de7a,0x44f3df06,0x44f3df08,0x44f3df2e,0x44f3df30},'account_trace')
    name=s.files.stage(12000,'Host Internet\0'.encode('utf-16le'))
    apn=s.files.stage(12200,'host\0'.encode('utf-16le'))
    if not s.word(0x4c0b8d80):
        call(0x44d5c814,0x441eeb20,0x441ef330,0x4c0b8d80)
    af=s.word(0x4c0b8d80)
    lockslot=s.files.stage(12300,bytes(4))
    call(s.word(s.word(af)+0x34)&~1,af,2,lockslot)
    token=s.word(lockslot)
    rows.append({'account_write_token':hex(token)})
    account=call(0x44f3ddf4,2,0,0,token)
    call(0x44f3e0f4,account,name)
    if not 0x4c000000<=account<0x4c7ffff0:raise RuntimeError('Native account creation failed')
    account_id=s.word(account)
    call(0x44f3e5fc,account,apn)
    interface=s.word(account+16)
    readback=s.files.stage(12800,bytes(104))
    getter=s.word(s.word(interface)+0x1c)&~1
    call(getter,interface,readback)
    print({'name_readback':s.d.read(readback,60).hex()},flush=True)
    call(s.word(s.word(af)+0x38)&~1,af,token)
    rows.append({'account_id':hex(account_id),'object':s.d.read(account,20).hex()})
    root=call(0x44c90070)
    slot=s.files.stage(12400,bytes(8))
    call(0x44766ce0,root,0x441eec20,0x441ef5b0,slot)
    factory=s.word(slot)
    if not 0x4c000000<=factory<0x4c7ffff0:raise RuntimeError('Native profile factory missing')
    call(0x44ced8a4,factory,root,slot+4)
    creator=s.word(slot+4)
    payload=bytearray(300)
    struct.pack_into('<I',payload,0,account_id)
    text='Host Internet\0'.encode('utf-16le')
    payload[6:6+len(text)]=text
    data=s.files.stage(12500,payload)
    result=call(0x44efb260,creator,data)
    if result:raise RuntimeError('Native profile creation failed')
    for _ in range(3):s.advance(1000)
    p=s.files.stage(14000,b'http://example.com/\0')
    call(0x450bec44,0x44245520,0,6,p)
    for _ in range(5):s.advance(1000)
    capture(s,out,'profile-configured')
except Exception as exc:
    rows.append({'error':str(exc)})
    print(repr(exc),flush=True)
finally:
    (out/'report.json').write_text(json.dumps({'calls':rows,'report':s.report() if s.q else {}},indent=2))
    s.close()
