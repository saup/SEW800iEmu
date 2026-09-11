"""Observe original KGEN calls and compare bounded stored-checksum hypotheses.

This neither replaces the ROM service nor modifies provisioning. Expected
values come from stored records, not from a successful physical ROM capture.
Raw record contents and checksum prefixes are not exported in the report.
"""
import hashlib
import json
import time

from w800.backend import QemuBackend, ROOT
from w800.gdfs import parse_backup
from w800.tools.debug import Debugger


def swap_words(data):
    return b''.join(data[i:i + 4][::-1] for i in range(0, len(data), 4))


def stored_digest_hypotheses(units):
    """Test the input layout implied by MAIN, without treating it as proven ROM behavior."""
    prefix = units[0, 19][:20]
    results = []
    for number in (6, 14):
        record = units[0, number]
        checks = {}
        for input_name, stream in (('payload', record[20:]),
                                   ('static_prefix_then_payload', prefix + record[20:])):
            for order, data in (('bytes', stream), ('word_reversed', swap_words(stream))):
                digest = hashlib.sha1(data).digest()
                checks[f'{input_name}/{order}/standard_output'] = digest == record[:20]
                checks[f'{input_name}/{order}/word_reversed_output'] = swap_words(digest) == record[:20]
        results.append({'unit': [0, number], 'record_length': len(record),
                        'candidate_matches': checks})
    return {'records': results,
            'limitation': 'Stored records are not independently verified successful ROM test vectors; nonmatches do not identify the algorithm.'}


def capture():
    backup = (ROOT / 'firmware/W800_GDFS.bin').read_bytes()
    units = {(u.bank, u.number): u.data for u in parse_backup(backup)}
    stored_prefix = units[0, 19][:20]
    contexts = {}
    results = []
    calls = {hex(pc): 0 for pc in (0x44d9ef3c, 0x44d9f990, 0x44d9f9ec, 0x44d9fa50)}
    checkpoints = {0x44d9ef3c, 0x44d9f990, 0x44d9f9ec, 0x44d9fa50}
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
        d = Debugger(q)
        d.sock.settimeout(5)
        stop = 'checkpoint budget'
        started = time.monotonic()
        try:
            for pc in checkpoints:
                d.breakpoint(pc, thumb=True)
            for _ in range(128):
                d.run()
                r = d.registers()
                pc = r[15]
                if pc not in checkpoints:
                    stop = f'guest stopped at {pc:#x}'
                    break
                calls[hex(pc)] += 1
                if pc == 0x44d9ef3c:
                    if not 20 <= r[1] <= 65536:
                        raise RuntimeError('Unexpected dynamic-record size')
                    record = d.read(r[0], r[1])
                    matches = [list(key) for key, data in units.items() if data == record]
                    # Original push consumes12 bytes, locals112; context is
                    # locals+20, so its address is entry-SP minus104 bytes.
                    contexts[r[13] - 104] = {'record': record, 'units': matches,
                                             'chunks': [], 'init_observed': False}
                elif pc == 0x44d9f990 and r[0] in contexts:
                    contexts[r[0]]['init_observed'] = True
                elif pc == 0x44d9f9ec and r[0] in contexts:
                    if r[2] > 65536:
                        raise RuntimeError('Unexpected KGEN update length')
                    contexts[r[0]]['chunks'].append(d.read(r[1], r[2]))
                elif pc == 0x44d9fa50 and r[1] in contexts:
                    context = contexts.pop(r[1])
                    chunks = context['chunks']
                    stream = b''.join(chunks)
                    record = context['record']
                    digest = hashlib.sha1(stream).digest()
                    transformed = hashlib.sha1(swap_words(stream)).digest()
                    results.append({
                        'matching_backup_units': context['units'],
                        'record_length': len(record),
                        'init_observed': context['init_observed'],
                        'update_lengths': [len(chunk) for chunk in chunks],
                        'stream_matches_static_prefix_then_record_body':
                            stream == stored_prefix + record[20:],
                        'sha1_matches_stored_value': digest == record[:20],
                        'sha1_reversed_output_words_matches': swap_words(digest) == record[:20],
                        'sha1_reversed_input_words_matches': transformed == record[:20],
                        'sha1_reversed_input_and_output_words_matches':
                            swap_words(transformed) == record[:20],
                        'comparison_reference': 'stored record; not an observed successful ROM result'
                    })
                    unique = {tuple(unit) for item in results for unit in item['matching_backup_units']}
                    if (0, 6) in unique and (0, 14) in unique:
                        stop = 'both original dynamic-record inputs captured'
                        break
                d.step_over_breakpoint(pc, thumb=True)
                if time.monotonic() - started > 20:
                    stop = 'host time budget'
                    break
        except TimeoutError:
            stop = 'no observed KGEN checkpoint within five seconds'
        finally:
            q.command('stop')
            d.close()
        return {'stop': stop, 'results': results, 'checkpoint_counts': calls,
                'stored_digest_hypotheses': stored_digest_hypotheses(units),
                'instruction_patches': False, 'guest_memory_writes': False,
                'debugger_register_setup': False,
                'qemu_sha256': hashlib.sha256((ROOT / 'build/qemu-system-arm').read_bytes()).hexdigest(),
                'backup_sha256': hashlib.sha256(backup).hexdigest(),
                'diagnostics': q.diagnostics()}


if __name__ == '__main__':
    report = capture()
    (ROOT / 'reports/kgen-vector-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
