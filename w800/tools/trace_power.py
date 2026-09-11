"""Capture original Vincenne driver register operations, without guest writes."""
import json,socket,time
from w800.backend import QemuBackend,ROOT
from w800.tools.debug import Debugger

def main():
 rows=[]
 with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
  d=Debugger(q);d.sock.settimeout(3)
  points={0x44ace2c8:'read',0x44ace34c:'write',0x44ace2f4:'read-many'}
  for p in points:d.breakpoint(p,thumb=True)
  deadline=time.monotonic()+25
  try:
   while time.monotonic()<deadline and len(rows)<3000:
    d.run();r=d.registers();pc=r[15]
    if pc not in points:break
    index=r[0]; row={'operation':points[pc],'index':index,'r1':r[1],'r2':r[2],'caller':hex(r[14])}
    if index<73:row['register']=d.read(0x4c0617cc+index*3,1)[0]
    rows.append(row);print(row,flush=True)
    d.step_over_breakpoint(pc,thumb=True)
  except (socket.timeout,RuntimeError) as e:print(type(e).__name__,e)
  q.command('stop');d.close()
 (ROOT/'reports/power-transfers.json').write_text(json.dumps(rows,indent=2)+'\n')
if __name__=='__main__':main()
