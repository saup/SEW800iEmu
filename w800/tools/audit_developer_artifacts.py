"""Read-only ASCII/UTF-16 developer artifact inventory of local MAIN flash."""
import hashlib
import json
import re
from w800.backend import ROOT

def main():
    source=ROOT/'firmware/prepared/flash.bin'
    data=source.read_bytes()
    categories={
        'service_pages':r'ServiceMenu|DisplayTest|CameraUtilityDriver',
        'debug_test':r'debugmenu|debug menu|testbook|testmode|test mode|engineering|factory test|selftest|self test|assert|trace level',
        'build_paths':r'IAR-ARM|appdesc_|\.c$',
        'easter_egg':r'ROCKS!|\beaster\b|hello world|\bTODO\b|\bFIXME\b',
    }
    rows=[]
    for encoding,pattern in [('ascii',rb'[\x20-\x7e\n]{6,}'),('utf-16le',rb'(?:[\x20-\x7e]\x00){6,}')]:
        for m in re.finditer(pattern,data):
            value=m.group().decode(encoding)
            if len(value)>500: continue
            tags=[tag for tag,rule in categories.items() if re.search(rule,value,re.I)]
            if tags: rows.append({'address':hex(0x44000000+m.start()),'encoding':encoding,'categories':tags,'text':value})
    out=ROOT/'reports/developer-artifacts.json'
    out.write_text(json.dumps({'source':str(source),'sha256':hashlib.sha256(data).hexdigest(),'artifacts':rows},indent=2))
    print('Artifacts:',len(rows))
    for row in rows:
        if any(t in row['categories'] for t in ('debug_test','easter_egg')):
            print(row['address'],row['text'][:180])
if __name__=='__main__': main()
