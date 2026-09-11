"""Validate full native MIDI timing and host PCM accounting from saved evidence."""
from pathlib import Path
import argparse
import json
import re
from w800.backend import ROOT


def values(line):
    return {key: float(value) if '.' in value else int(value)
            for key, value in re.findall(r'(\w+)=([0-9]+(?:\.[0-9]+)?)', line)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'reports/midi-natural-coreaudio')
    args = parser.parse_args()
    directory = args.input
    result = json.loads((directory / 'result.json').read_text())
    log = (directory / 'complete-diagnostics.log').read_text()
    observations = result['observations']
    def observed(marker):
        return next(row for row in observations if marker in row['line'])
    def last_line(marker):
        return [line for line in log.splitlines() if marker in line][-1]
    active = observed('W800_DSP_PCM_ACTIVITY running=1')
    eof = observed('W800_DSP_SYNTH_EOF ')
    complete = observed('W800_DSP_SYNTH_COMPLETE ')
    tail = observed('W800_DSP_SYNTH_TAIL_DRAINED ')
    rate = values(observed('W800_DSP_PCM source=')['line'])['rate']
    host = values(last_line('W800_COREAUDIO_FORMAT '))
    final = values(last_line('W800_COREAUDIO_DEBUG '))
    pcm = values(last_line('W800_DSP_PCM_END '))
    source_seconds = result['source']['last_note_off_seconds']
    source_eof_frame = values(eof['line'])['frame']
    def elapsed_bounds(end):
        return [(end['previous_observation_wall_ns']-active['observed_wall_ns'])/1e9,
                (end['observed_wall_ns']-active['previous_observation_wall_ns'])/1e9]
    eof_bounds = elapsed_bounds(eof)
    complete_bounds = elapsed_bounds(complete)
    rendered_seconds = pcm['rendered']/rate
    delivered_seconds = final['delivered_frames']/host['actual_hz']
    checks = {
        'native_completed': result['result'] == 'PASS' and len(result['natural_completion_callbacks']) == 1,
        'all_source_events_consumed': pcm['midi_events'] == result['source']['event_count'],
        'all_rendered_frames_submitted': pcm['rendered'] == pcm['submitted'],
        'source_last_note_within_native_render_block':
            source_eof_frame/rate <= source_seconds <= (source_eof_frame+256)/rate + .001,
        'source_duration_within_wall_observation_bounds':
            eof_bounds[0] <= source_seconds <= eof_bounds[1],
        'actual_host_rate_matches_requested': host['actual_hz'] == host['requested_hz'],
        'converted_pcm_agrees_within_one_host_frame':
            abs(rendered_seconds-delivered_seconds) <= 1/host['actual_hz'],
        'host_callbacks_fully_accounted': final['callbacks']*host['callback_frames'] ==
            final['delivered_frames']+final['underrun_frames'],
        'host_ring_fully_drained': final['pending_bytes'] == 0,
        'pcm_wall_duration_within_one_percent':
            abs(rendered_seconds-sum(complete_bounds)/2)/(sum(complete_bounds)/2) < .01,
        'source_firmware_unchanged': result['session']['source_unchanged'],
    }
    summary = {
        'result': 'PASS' if all(checks.values()) else 'FAIL',
        'qemu_sha256': result['session']['qemu_sha256'],
        'source_sha256': result['source']['sha256'],
        'source_events': result['source']['event_count'],
        'source_last_note_seconds': source_seconds,
        'native_eof_render_block_start_frame': source_eof_frame,
        'native_eof_render_block_start_seconds': source_eof_frame/rate,
        'source_to_native_eof_error_seconds': source_eof_frame/rate-source_seconds,
        'eof_observed_wall_seconds': (eof['observed_wall_ns']-active['observed_wall_ns'])/1e9,
        'eof_wall_observation_bounds_seconds': eof_bounds,
        'complete_wall_observation_bounds_seconds': complete_bounds,
        'native_completion_callbacks': len(result['natural_completion_callbacks']),
        'rendered_frames': pcm['rendered'], 'submitted_frames': pcm['submitted'],
        'renderer_sample_rate': rate, 'renderer_pcm_seconds': rendered_seconds,
        'host_delivered_frames': final['delivered_frames'], 'host_sample_rate': host['actual_hz'],
        'host_pcm_seconds': delivered_seconds,
        'host_conversion_difference_frames': (rendered_seconds-delivered_seconds)*host['actual_hz'],
        'compatible_GM_tail_after_last_source_note_seconds': rendered_seconds-source_seconds,
        'tail_silent_to_native_complete_observed_seconds':
            (complete['observed_wall_ns']-tail['observed_wall_ns'])/1e9,
        'host_callback_frames': host['callback_frames'], 'host': final,
        'host_missing_frame_seconds': final['underrun_frames']/host['actual_hz'],
        'added_breakpoints': ['0x44883658 native natural completion only'],
        'normal_sample_interval_ms': 40, 'windows': len(result['windows']),
        'checks': checks,
        'limits': ['Wall event boundaries include initial-key and 40 ms observation intervals.',
                   f'Renderer uses compatible host GM timbres; its {rendered_seconds-source_seconds:.2f} s tail is separate from the source note schedule.',
                   f'{final["expired"]} debugger-stop grace timers expired; callback padding is not a gap-free claim.',
                   'Callback underruns count zero padding and do not independently identify audible glitches.',
                   'Python observation and QEMU log realtime clocks have different epochs; compare intervals within each clock.']}
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
