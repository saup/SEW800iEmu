"""Compare logged native stream4 presentation times with actual device time."""
import argparse
import json
from pathlib import Path
import re


def signed32(value):
    return (value + 0x80000000) % 0x100000000 - 0x80000000


def analyze(path):
    records, gaps, statuses = [], [], []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = dict(re.findall(r'(\w+)=([^\s,]+)', line))
        if 'W800_DSP_AAC_INPUT' in line and 'pts_us' in fields:
            row = {key: int(fields[key], 16 if key == 'first' else 10)
                   for key in ('words', 'queued', 'first', 'pts_us',
                               'payload_words', 'virtual_us', 'packets',
                               'decoded', 'submitted')}
            row['line'] = number
            row['normal'] = row['first'] == 1
            if not row['normal']:
                continue
            row['lead_us'] = signed32(row['pts_us'] - row['virtual_us'])
            row['index'] = len(records)
            if records:
                delta = signed32(row['pts_us'] - records[-1]['pts_us'])
                row['delta_pts_us'] = delta
                if delta != 64000:
                    # A nonintegral change may indicate a clock-base change;
                    # it is not automatically a missing AAC access unit.
                    gaps.append(dict(index=row['index'], line=number,
                                     delta_pts_us=delta,
                                     omitted_64ms_units=(delta // 64000 - 1)
                                     if delta > 0 and delta % 64000 == 0 else None))
            records.append(row)
        elif 'W800_DSP_AAC_UNDERRUN' in line:
            statuses.append(dict(line=number, inputs=len(records),
                                 values={key: int(value) for key, value in fields.items()
                                         if value.isdecimal()}))
    summary = dict(inputs=len(records), deviations=len(gaps), underruns=len(statuses))
    if records:
        summary.update(first=records[0], last=records[-1],
                       lead_us_min=min(row['lead_us'] for row in records),
                       lead_us_max=max(row['lead_us'] for row in records),
                       pts_span_us=signed32(records[-1]['pts_us'] - records[0]['pts_us']),
                       arrival_span_us=records[-1]['virtual_us'] - records[0]['virtual_us'],
                       integral_omitted_units=sum(row['omitted_64ms_units'] or 0 for row in gaps),
                       nonintegral_changes=sum(row['omitted_64ms_units'] is None for row in gaps))
    return dict(summary=summary, gaps=gaps, statuses=statuses, records=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostics', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = analyze(args.diagnostics)
    output = args.output or args.diagnostics.with_name('pts-analysis.json')
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
