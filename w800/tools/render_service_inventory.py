"""Render measured component status, keeping creation separate from readiness."""
import json
from w800.backend import ROOT

def main():
    directory=ROOT/'reports/service-inventory'
    j=json.loads((directory/'inventory.json').read_text())
    before={r['id']:r for r in j['startup_choice']}
    rows=j['phone_standby']
    lines=['# Original firmware service inventory','',
      'Measured in a fresh disposable VM using the current OriginalUISession, full_services=True, after selecting Start phone. This is not an attachment to the user’s open GUI instance.',
      '',f"The original application manager has {len(rows)} descriptors: {sum(r['started'] for r in rows)} started and {sum(not r['started'] for r in rows)} not started.",
      '', 'Started means the manager marks the component started and the snapshot records its handle. It does not prove successful initialization, scheduler activity, healthy dependencies, or working hardware. Components are not a complete list of kernel/platform processes.',
      '', '| ID | Original name | At startup choice | At phone standby | Handle | Allowed mode mask |', '| --- | --- | --- | --- | --- | --- |']
    for r in rows:
        lines.append(f"| {r['id']} | {r['name']} | {'Started' if before[r['id']]['started'] else 'Not started'} | {'Started' if r['started'] else 'Not started'} | {r['handle']} | {r['mode_mask']:#x} |")
    lines += ['', '## What to start or fix', '',
      '- **Customization:** the only unstarted application component. Its mode mask is 0x80, while this session uses mode 1. It belongs to provisioning/customization rather than ordinary standby. Starting it could run setup/content operations; it is not a general repair for menus. Leave it excluded from normal startup.',
      '- **Java Debug Monitor:** already started. Starting it again will not create a debug menu. Its debugger transport and protocol have not been validated.',
      '- **Java VM, OAF, UI_OAF, Java timers:** already started. Games depend on the VM lifecycle, installation records and graphics/input/audio services, not just process creation.',
      '- **Media Player, Audio Control, ImageHandler, Video Editing:** already started. Camera/media improvements need working device interfaces, callbacks and timing. Individual services are not certified healthy by this snapshot.',
      '- **Phonebook, Calendar, Notes, Clock, Extras:** already started. Remaining failures should be investigated at request/reply and storage boundaries.',
      '- **Bluetooth Connection Manager, Handsfree, OBEX, vObject, Accessory, LC:** already started. Useful capabilities would require supported transport/device endpoints and completed initialization.',
      '- **WAP, Browser, GET_POST, Download, Email, POP3, MMS, WV, SyncML, BSD/SSTCP:** already started. Networking, account/server configuration and protocol support remain separate requirements.',
      '- **FM radio:** application started; it does not establish that a tuner exists in emulation.',
      '- **AT Command Server/Handler and Mass Storage:** already started. Their host-facing transport paths need separate testing; starting duplicate instances is not the next step.',
      '', 'No additional application component is recommended for blind startup. The next useful work is per-service readiness and dependency tests. Existing compatibility adapters and source code are recorded separately in the JSON provenance.',
      '', '## Kernel process observations', '']
    if 'processes' in j:
        lines += [f"Found {len(j['processes'])} structurally matched allocated task-control blocks in a frozen 8 MiB RAM snapshot. Matching requires the observed TCB signature and consistent PID fields. Names are candidate strings reached through fields in each record. This is a bounded structural inventory, not a verified traversal of the OSE process table; freed lookalikes or missed variants remain possible. Scheduler state values are not decoded.", '', '| PID | Name | TCB |', '| --- | --- | --- |']
        for r in j['processes']:lines.append(f"| {r['pid']} | {r.get('name', str(r.get('name_candidates', {})))} | {r['tcb']} |")
    else:lines.append('Kernel task enumeration is not included in this snapshot.')
    lines += ['',f"Source firmware unchanged: {j['source_unchanged']}. Raw RAM is deleted after extraction; JSON keeps only bounded diagnostic evidence."]
    (directory/'README.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
