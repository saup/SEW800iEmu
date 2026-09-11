"""Read-only original MMU range writes and CPU mapping state before KGEN."""
import json, struct, xml.etree.ElementTree as ET
from pathlib import Path
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger

BREAKS={0x440204d4:'set region bounds',0x4402057a:'commit region bounds',
        0x440205a4:'configure region',0x440207ea:'MMU setup complete',
        0x44d9f996:'before GetChipId/KGEN'}

def xml_file(d,name):
    data='';offset=0
    while True:
        reply=d.packet(f'qXfer:features:read:{name}:{offset:x},1000')
        if reply[0] not in 'ml':raise RuntimeError(reply)
        data+=reply[1:];offset+=len(reply)-1
        if reply[0]=='l':return data

def main():
    events=[]
    with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
        d=Debugger(q)
        for pc in BREAKS:d.breakpoint(pc,thumb=True)
        for _ in range(150):
            stop=d.run();r=d.registers();pc=r[15]
            event={'pc':hex(pc),'label':BREAKS.get(pc),'stop':stop,'regs':[hex(x) for x in r]}
            if pc in (0x440207ea,0x44d9f996):
                regs=struct.unpack('<24I',d.read(0xfe004000,96))
                event['hardware_ranges']=[{'index':i,'range':hex(regs[i]),'attr':hex(regs[i+12])} for i in range(12)]
                event['generated_fff_section']=hex(struct.unpack('<I',d.read(0xfe003ffc,4))[0])
                event['cp15']={}
                root=ET.fromstring(xml_file(d,'system-registers.xml'))
                for reg in root.findall('reg'):
                    if reg.attrib['name'] in ('DACR','TTBR0_EL1','TTBR1_EL1','TTBCR','SCTLR_EL1','FCSEIDR','CONTEXTIDR_EL1','DFAR','DFSR','IFSR'):
                        event['cp15'][reg.attrib['name']]=hex(int.from_bytes(bytes.fromhex(d.packet('p'+format(int(reg.attrib['regnum']),'x'))),'little'))
            events.append(event)
            if pc==0x44d9f996 or pc not in BREAKS:break
            d.step_over_breakpoint(pc,thumb=True)
        d.close();q.command('stop')
    out=ROOT/'reports/ui-mapping-audit.json';out.write_text(json.dumps(events,indent=2))
    print(json.dumps(events[-1],indent=2))
if __name__=='__main__':main()
