"""Compare captured native stream-4 AAC access units to original MP4 samples.

This is an offline, read-only format check. It sends no DSP replies and does
not decode, alter, or replace media in the emulated phone.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def native_packet(packet):
    """Parse one complete 0804 message produced by ARM4488dc84/448d51cc."""
    if len(packet) < 16 or len(packet) % 2:
        raise ValueError('incomplete native packet')
    words = struct.unpack('<' + 'H' * (len(packet) // 2), packet)
    if words[0] != 0x0804 or words[1] != len(words) - 2:
        raise ValueError('transport opcode/count mismatch')
    kind, metadata, unit, hi, lo, count = words[2:8]
    if count != len(words) - 8:
        raise ValueError('payload word count mismatch')
    result = dict(kind=kind, metadata=metadata, unit=unit,
                  timestamp_us=(hi << 16) | lo, payload_words=count)
    if not count:
        return result, b''
    payload = b''.join(struct.pack('>H', value) for value in words[8:])
    if len(payload) < 8 or payload[:2] != b'\xff\xf1':
        raise ValueError('unsupported native AAC prefix')
    # ARM4488e864 writes N>>5 and (N<<3)+7. This is not ADTS's
    # differently aligned 13-bit frame-length field.
    byte_count = (payload[4] << 5) | (payload[5] >> 3)
    if byte_count < 8 or (byte_count + 1) // 2 != count:
        raise ValueError('native byte length contradicts payload words')
    if payload[byte_count:] not in (b'', b'\0'):
        raise ValueError('nonzero odd-byte padding')
    au = payload[8:byte_count]
    result.update(prefix=payload[:8].hex(), native_bytes=byte_count,
                  padding_bytes=len(payload) - byte_count, aac_bytes=len(au),
                  aac_sha256=hashlib.sha256(au).hexdigest())
    return result, au


def audio_samples(data):
    """Read original stsc/stsz/stco tables; no byte-pattern sample search."""
    def boxes(start, end):
        while start < end:
            if end - start < 8:
                raise ValueError('truncated MP4 box')
            size, kind = struct.unpack_from('>I4s', data, start)
            if size < 8 or start + size > end:
                raise ValueError('unsupported/truncated MP4 box size')
            yield kind, start + 8, start + size
            start += size

    def child(box, name):
        return next(item for item in boxes(box[1], box[2]) if item[0] == name)

    moov = next(box for box in boxes(0, len(data)) if box[0] == b'moov')
    for track in boxes(moov[1], moov[2]):
        if track[0] != b'trak':
            continue
        mdia = child(track, b'mdia')
        handler = child(mdia, b'hdlr')
        if data[handler[1] + 8:handler[1] + 12] != b'soun':
            continue
        table = child(child(mdia, b'minf'), b'stbl')
        stsz, stsc, stco = (child(table, name) for name in
                            (b'stsz', b'stsc', b'stco'))
        fixed, count = struct.unpack_from('>II', data, stsz[1] + 4)
        sizes = ([fixed] * count if fixed else list(struct.unpack_from(
            '>' + 'I' * count, data, stsz[1] + 12)))
        run_count = struct.unpack_from('>I', data, stsc[1] + 4)[0]
        runs = [struct.unpack_from('>III', data, stsc[1] + 8 + 12 * i)
                for i in range(run_count)]
        chunk_count = struct.unpack_from('>I', data, stco[1] + 4)[0]
        chunks = struct.unpack_from('>' + 'I' * chunk_count, data, stco[1] + 8)
        sample_index, run_index = 0, 0
        result = []
        for chunk_index, offset in enumerate(chunks, 1):
            while run_index + 1 < len(runs) and runs[run_index + 1][0] <= chunk_index:
                run_index += 1
            for _ in range(runs[run_index][1]):
                size = sizes[sample_index]
                if offset + size > len(data):
                    raise ValueError('sample exceeds source file')
                result.append((offset, data[offset:offset + size]))
                offset += size
                sample_index += 1
        if sample_index != len(sizes):
            raise ValueError('MP4 sample/chunk counts disagree')
        return result
    raise ValueError('no audio track')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, default=Path(
        'w800/reports/aac-service-probe/result.json'))
    parser.add_argument('--media', type=Path, default=Path(
        'w800/reports/video-demo-format.mp4'))
    args = parser.parse_args()
    source = args.media.read_bytes()
    samples = audio_samples(source)
    trace = json.loads(args.trace.read_text())
    frames = []
    for event in trace['aac_trace']:
        packet = bytes.fromhex(event.get('packet', ''))
        if not packet.startswith(b'\x04\x08'):
            continue
        frame, au = native_packet(packet)
        index = len(frames)
        if index < len(samples):
            offset, sample = samples[index]
            frame.update(mp4_sample=index, mp4_offset=offset,
                         mp4_bytes=len(sample), exact_mp4_match=au == sample)
        frames.append(frame)
    print(json.dumps(dict(source=str(args.media),
                          source_sha256=hashlib.sha256(source).hexdigest(),
                          mp4_audio_sample_count=len(samples), frames=frames), indent=2))


if __name__ == '__main__':
    main()
