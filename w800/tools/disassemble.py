"""Disassemble a local flash address; never sends network requests."""
from pathlib import Path
import argparse
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
p = argparse.ArgumentParser()
p.add_argument('address', type=lambda s:int(s,0))
p.add_argument('--size', type=lambda s:int(s,0), default=256)
p.add_argument('--thumb', action='store_true')
a = p.parse_args()
flash = Path(__file__).resolve().parents[1] / 'firmware/prepared/flash.bin'
data = flash.read_bytes()
o = a.address - 0x44000000
if not 0 <= o < len(data) or not 1 <= a.size <= 65536:
    p.error('Address/size outside flash')
cs = Cs(CS_ARCH_ARM, CS_MODE_THUMB if a.thumb else CS_MODE_ARM)
for i in cs.disasm(data[o:o+a.size], a.address):
    print(f'{i.address:08x}  {i.bytes.hex():12} {i.mnemonic:8} {i.op_str}')
