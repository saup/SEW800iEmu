/* Bounded capture FIFO. The caller serializes access. GPL-2.0-or-later. */
#ifndef W800_COREAUDIO_INPUT_BUFFER_H
#define W800_COREAUDIO_INPUT_BUFFER_H

typedef struct CoreaudioInputBuffer {
    float *data;
    size_t capacity, read, used;
    unsigned channels;
    uint64_t captured, delivered, dropped, discarded;
} CoreaudioInputBuffer;

static void coreaudio_input_discard(CoreaudioInputBuffer *ring)
{
    ring->discarded += ring->used;
    ring->used = ring->read = 0;
}

/* Buffer channels describe CoreAudio's actual interleaved/planar layout.
 * A full FIFO retains already captured frames; overflow is counted explicitly.
 */
static size_t coreaudio_input_push(CoreaudioInputBuffer *ring,
                                  const AudioBufferList *buffers)
{
    size_t frames = SIZE_MAX;
    unsigned channels = 0;
    for (UInt32 i = 0; i < buffers->mNumberBuffers && channels < ring->channels; i++) {
        const AudioBuffer *b = &buffers->mBuffers[i];
        if (!b->mNumberChannels) { continue; }
        if (!b->mData) { return 0; }
        size_t n = b->mDataByteSize / (sizeof(float) * b->mNumberChannels);
        if (n < frames) { frames = n; }
        channels += b->mNumberChannels;
    }
    if (!ring->capacity || channels < ring->channels || frames == SIZE_MAX) { return 0; }
    size_t accepted = frames;
    if (accepted > ring->capacity - ring->used) { accepted = ring->capacity - ring->used; }
    for (size_t frame = 0; frame < accepted; frame++) {
        size_t destination = (ring->read + ring->used + frame) % ring->capacity;
        unsigned channel = 0;
        for (UInt32 i = 0; i < buffers->mNumberBuffers && channel < ring->channels; i++) {
            const AudioBuffer *b = &buffers->mBuffers[i];
            const float *source = b->mData;
            for (unsigned c = 0; c < b->mNumberChannels && channel < ring->channels; c++) {
                ring->data[destination * ring->channels + channel++] =
                    source[frame * b->mNumberChannels + c];
            }
        }
    }
    ring->used += accepted;
    ring->captured += accepted;
    ring->dropped += frames - accepted;
    return accepted;
}

static size_t coreaudio_input_pop(CoreaudioInputBuffer *ring, void *destination,
                                 size_t bytes)
{
    size_t stride = sizeof(float) * ring->channels;
    if (!stride || !ring->capacity) { return 0; }
    size_t frames = bytes / stride;
    if (frames > ring->used) { frames = ring->used; }
    size_t first = frames;
    if (first > ring->capacity - ring->read) { first = ring->capacity - ring->read; }
    memcpy(destination, ring->data + ring->read * ring->channels, first * stride);
    memcpy((uint8_t *)destination + first * stride, ring->data, (frames - first) * stride);
    ring->read = (ring->read + frames) % ring->capacity;
    ring->used -= frames;
    ring->delivered += frames;
    return frames * stride;
}
#endif
