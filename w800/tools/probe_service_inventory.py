"""Snapshot original components and trace the existing debugmenu launcher."""
import json
import struct
from w800.backend import ROOT
from w800.components import descriptors
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.probe_menu_services import trace_calls

def main():
    output=ROOT/'reports/service-inventory'
    output.mkdir(exist_ok=True)
    with OriginalUISession(default_theme=True,start_phone_standby=True) as s:
        snapshots={'startup_choice':descriptors(s)}
        press(s,'select')
        snapshots['phone_standby']=descriptors(s)
        snapshots['provenance']=s.provenance.copy()
        tcb=s.word(0x4c041c68)
        snapshots['current_tcb']={'address':hex(tcb),'bytes':s.d.read(tcb,256).hex()}
        trace_calls(s,{0x45059cda,0x45059ce0,0x45058faa,0x45058fd6,0x45058fdc,0x45059d86},'debug_menu_dispatch')
        observed=s.d.registers
        def registers():
            regs=observed()
            if regs[15] in (0x45059cda,0x45059ce0,0x45058faa,0x45058fd6,0x45058fdc,0x45059d86):
                row=s.provenance['debug_menu_dispatch'][-1]
                for index in (0,5,6):
                    p=regs[index]
                    if 0x4c000000<=p<0x4c7fff00:
                        row['r'+str(index)+'_memory']=s.d.read(p,80).hex()
            return regs
        s.d.registers=registers
        pointer=s.files.stage(15000,'debugmenu\0'.encode('utf-16le'))
        s.call_on_mmi(0x45059c7c,pointer)
        s.advance(1000)
        dump=output/'ram.bin'
        s.q.command('human-monitor-command', {'command-line': 'pmemsave 0x4c000000 0x800000 '+json.dumps(str(dump))})
        ram=dump.read_bytes()
        snapshots['ram_tcb']=ram[tcb-0x4c000000:tcb-0x4c000000+256].hex()
        snapshots['current_name_bytes']=s.d.read(s.word(tcb+0x60),128).hex()
        processes=[]
        for off in range(0,len(ram)-256,4):
            if struct.unpack_from('<H',ram,off)[0] != 0x5718:
                continue
            pid=struct.unpack_from('<I',ram,off+0x38)[0]
            if (pid & 0xffff) != struct.unpack_from('<H',ram,off+2)[0]:
                continue
            names={}
            for field in range(0x3c,256,4):
                pointer=struct.unpack_from('<I',ram,off+field)[0]
                if not 0x4c000000<=pointer<0x4c800000:
                    continue
                raw=ram[pointer-0x4c000000:pointer-0x4c000000+128].split(b'\0')[0]
                if len(raw)>=3 and all(32<=v<127 for v in raw):
                    names[hex(field)]=raw.decode()
            processes.append({'pid':hex(pid),'tcb':hex(0x4c000000+off),'name':names.get('0x40','<no printable name>'),'name_candidates':names,'status':'TCB signature candidate; scheduler state not decoded'})
        snapshots['processes']=processes
        dump.unlink()
        snapshots['debug_trace']=s.provenance.get('debug_menu_dispatch',[])
        snapshots['source_unchanged']=s.report()['source_unchanged']
        (output/'inventory.json').write_text(json.dumps(snapshots,indent=2))
        print('Inventory saved',flush=True)
if __name__=='__main__':main()
