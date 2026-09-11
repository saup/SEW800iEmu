"""Read actual PDI transfer descriptors from original guest code via QEMU traps."""
from w800.backend import QemuBackend
from w800.tools.debug import Debugger
import json,struct
from pathlib import Path

def main():
 captures=[]
 with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
  d=Debugger(q);d.sock.settimeout(4)
  sites=[0x447a0cee,0x447a0674,0x44ab89ee,0x44aa36c0]
  for a in sites:d.breakpoint(a,thumb=True)
  try:
   for _ in range(64):
    d.run();regs=d.registers();pc=regs[15]
    data=d.read(0xf7000100,0x100)
    c={'pc':hex(pc),'regs':[hex(r) for r in regs],'pdi':data.hex()}
    captures.append(c);print(json.dumps(c),flush=True)
    d.step_over_breakpoint(pc,thumb=True)
  except (TimeoutError,RuntimeError) as e:print(type(e).__name__,e)
  d.close()
 Path('w800/reports/lcd-transfers.json').write_text(json.dumps(captures,indent=2))
if __name__=='__main__':main()
