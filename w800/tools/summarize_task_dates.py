"""Summarize captured original task save/reload dates without changing a VM."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import struct


def summarize(directory):
    report = json.loads((directory / '03-task-done-reopened.json').read_text())
    rows = report['session']['task_date_trace']

    def memory(row, register, offset=0, length=8):
        return bytes.fromhex(row['objects'][row['registers'][register]])[offset:offset + length]

    def date(raw):
        return datetime(*struct.unpack('<H5B', raw[:7]))

    result = {'qemu_sha256': report['session']['qemu_sha256'],
              'source_unchanged': report['session']['source_unchanged'],
              'native_records': [], 'timezone_reads': [], 'daylight_reads': []}
    for row in rows:
        point = row['point']
        field = None
        if point == 'Current time acquired':
            field = (4, 0x40, 'local clock value placed in live task book')
        elif point == 'CheckToDo after UTC conversion':
            field = (4, 0x40, 'live task book changed to UTC')
        elif point == 'Task save requester' and memory(row, 2, 1, 1) == b'\x01':
            field = (2, 0x14, 'native save request completion field')
        elif point == 'Task information view' and memory(row, 1, 0x2d, 1) == b'\x01':
            field = (1, 0x40, 'information view completion field')
        elif point == 'Convert UTC date to local':
            field = (0, 0, 'native reload before UTC to local conversion')
        elif point == 'Native offset applied':
            field = (4, 0, 'native reload after UTC to local conversion')
        if field:
            register, offset, meaning = field
            raw = memory(row, register, offset)
            result['native_records'].append({
                'point': point, 'meaning': meaning,
                'pointer': row['registers'][register], 'offset': offset,
                'raw_date': raw.hex(), 'date': str(date(raw)),
            })
        if point in ('Timezone getter returned', 'Timezone getter for read returned'):
            result['timezone_reads'].append({'status': row['registers'][0],
                'quarter_hours': struct.unpack('<b', memory(row, 13, 0, 1))[0]})
        if point == 'Daylight getter returned':
            result['daylight_reads'].append({'status': row['registers'][0],
                                             'hours': memory(row, 13, 1, 1)[0]})
    result['native_rtc'] = {
        label: json.loads((directory / (label + '.json')).read_text())['native_rtc']
        for label in ('02-task-done', '03-task-done-reopened')}
    records = result['native_records']
    local = next(row['date'] for row in records if row['point'] == 'Current time acquired')
    utc = next(row['date'] for row in records if row['point'] == 'Task save requester')
    views = [row['date'] for row in records if row['point'] == 'Task information view']
    zone = result['timezone_reads'][0]['quarter_hours']
    daylight = result['daylight_reads'][0]['hours']
    checks = {
        'source_and_validation_unchanged': result['source_unchanged'] and not
            report['session']['guest_instruction_patches'] and not
            report['session']['validation_result_changes'],
        'all_timezone_calls_successful_and_consistent': all(
            row == {'status': '0x0', 'quarter_hours': zone} for row in result['timezone_reads']),
        'all_daylight_calls_successful_and_consistent': all(
            row == {'status': '0x0', 'hours': daylight} for row in result['daylight_reads']),
        'save_matches_actual_offset': datetime.fromisoformat(local) - datetime.fromisoformat(utc)
            == timedelta(minutes=15 * zone + 60 * daylight),
        'immediate_view_uses_utc_save_value': views[0] == utc,
        'all_native_reload_values_preserve_utc': all(row['date'] == utc for row in records
            if row['point'] == 'Convert UTC date to local'),
        'all_native_local_conversions_restore_original_local_value': all(row['date'] == local
            for row in records if row['point'] == 'Native offset applied'),
        'reopened_view_uses_local_value': views[-1] == local,
        'no_held_keys': not report['held_keys'],
    }
    result['checks'] = checks
    result['status'] = 'PASS' if all(checks.values()) else 'FAIL'
    (directory / 'task-records.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    result = summarize(parser.parse_args().directory)
    print(json.dumps({'status': result['status'], 'checks': result['checks']}, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
