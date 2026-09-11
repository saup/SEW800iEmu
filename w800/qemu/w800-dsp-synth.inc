/* Guest MIDI event FIFO -> compatible General MIDI synthesis -> QEMU PCM.
 * GPL-2.0-or-later. No host media-file path or GUI callback is accepted here.
 * The macOS instrument bank supplies compatible timbres; this is DSP HLE,
 * not execution of the original C55x synthesizer or its instrument bank.
 */
#include "qemu/audio.h"
#ifdef __APPLE__
#include <AudioToolbox/AudioToolbox.h>
#include <dlfcn.h>
#endif

#define W800_SYNTH_FRAMES 256

typedef struct W800DspSynth {
    AudioBackend *backend;
    SWVoiceOut *voice;
    uint64_t frames, rendered, submitted, events, nonzero, callbacks;
    uint64_t eof_frame, eof_markers;
    unsigned read_pos, credits, eof_credits;
    unsigned pcm_bytes, pcm_offset;
    int16_t pcm[W800_SYNTH_FRAMES * 2];
    bool running, failed, pcm_reported, unsupported_reported, completion_requested;
    bool eof, renderer_silent, pcm_drained, completion_sent;
#ifdef __APPLE__
    void *framework;
    AudioUnit unit;
    __typeof__(&AudioComponentFindNext) find;
    __typeof__(&AudioComponentInstanceNew) create;
    __typeof__(&AudioComponentInstanceDispose) dispose;
    __typeof__(&AudioUnitSetProperty) set;
    __typeof__(&AudioUnitInitialize) initialize;
    __typeof__(&AudioUnitUninitialize) uninitialize;
    __typeof__(&AudioUnitRender) render;
    __typeof__(&AudioUnitReset) reset;
    __typeof__(&MusicDeviceMIDIEvent) midi;
#endif
} W800DspSynth;

static void w800_dsp_synth_free(W800DspSynth *synth)
{
    if (!synth) { return; }
    if (synth->voice) { AUD_close_out(synth->backend, synth->voice); }
#ifdef __APPLE__
    if (synth->unit) {
        if (synth->uninitialize) { synth->uninitialize(synth->unit); }
        if (synth->dispose) { synth->dispose(synth->unit); }
    }
    if (synth->framework) { dlclose(synth->framework); }
#endif
    error_report("W800_DSP_PCM_END rendered=%"PRIu64" submitted=%"PRIu64
                 " midi_events=%"PRIu64" nonzero_samples=%"PRIu64" callbacks=%"PRIu64,
                 synth->rendered, synth->submitted, synth->events, synth->nonzero, synth->callbacks);
    g_free(synth);
}

static void w800_dsp_synth_credits(W800State *s)
{
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer ? mixer->synth : NULL;
    if (!synth) { return; }
    if (synth->pcm_drained) {
        synth->credits += synth->eof_credits;
        synth->eof_credits = 0;
    }
    if (!synth->credits || !mixer->synth_writer ||
        s->dsp_reply_pending || (s->dsp_regs[0x04 / 4] & 1)) { return; }
    /* The peer requested a notification threshold with0401. Return only
     * words whose actual MIDI commands the renderer has consumed, and
     * wake the original ARM producer with0501 when a batch is available.
     */
    if (synth->credits < mixer->synth_threshold && mixer->synth_fifo_used) { return; }
    uint8_t reply[4];
    /* ARM448d5308..5310 ->52ac only adds0201's count to stream+4;
     * it changes no indices/state and invokes no callback. Native0501 also
     * asks it to produce more. EOF remains in progress while its audio tail
     * drains. Return those final credits quietly after that work finishes,
     * rather than repeatedly asking the exhausted parser for another EOF.
     */
    uint16_t opcode = synth->pcm_drained ? 0x0201 : 0x0501;
    stw_le_p(reply, opcode);
    stw_le_p(reply + 2, synth->credits);
    w800_dsp_stage_reply(s, 20, reply, sizeof(reply), opcode);
    synth->credits = 0;
}

static void w800_dsp_synth_activity(W800State *s, bool running)
{
    W800DspMixer *mixer = s->dsp_mixer;
    if (!mixer || !mixer->synth) { return; }
    mixer->synth->running = running;
    AUD_set_active_out(mixer->synth->voice, running);
    error_report("W800_DSP_PCM_ACTIVITY running=%u writer=%u frames=%"PRIu64" clock=%"PRId64,
                 running, mixer->synth_writer, mixer->synth->frames,
                 qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
}

static void w800_dsp_synth_complete(W800State *s)
{
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer ? mixer->synth : NULL;
    if (!synth || !synth->running || synth->failed ||
        !synth->completion_requested || synth->completion_sent ||
        !synth->eof || !synth->renderer_silent || !synth->pcm_drained ||
        synth->pcm_offset != synth->pcm_bytes || synth->credits || synth->eof_credits ||
        mixer->synth_fifo_used || s->dsp_reply_pending ||
        (s->dsp_regs[0] & 1) || (s->dsp_regs[0x04 / 4] & 1)) { return; }
    /* Original ARM44883364 reads the low/high frame count in060b, then
     * 44883658 handles natural completion with stop reason1. The count
     * is the real rendered clock, including this compatible bank's tail;
     * the zero timestamp in the EOF record never rewinds that clock.
     */
    uint8_t reply[6];
    stw_le_p(reply, 0x060b);
    stw_le_p(reply + 2, synth->frames);
    stw_le_p(reply + 4, synth->frames >> 16);
    w800_dsp_stage_reply(s, 9, reply, sizeof(reply), 0x060b);
    synth->completion_sent = true;
    w800_dsp_synth_activity(s, false);
    error_report("W800_DSP_SYNTH_COMPLETE frames=%"PRIu64" markers=%"PRIu64
                 " pcm_submitted=%"PRIu64" reply=060b",
                 synth->frames, synth->eof_markers, synth->submitted);
}

static bool w800_dsp_synth_eof_record(W800State *s)
{
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer->synth;
    unsigned capacity = s->dsp_stream_capacity[1];
    if (!capacity || mixer->synth_fifo_used < 5) { return false; }
    for (unsigned i = 0; i < 5; i++) {
        if (s->dsp_stream_words[1][(synth->read_pos + i) % capacity]) { return false; }
    }
    /* Captured original44884782 ->448829d0 ->448d5714 sends exactly five
     * zero halfwords after every track finishes. The ARM producer can
     * repeat that same marker when later consumption credits arrive.
     * Consume each real record once, but retain one logical EOF.
     */
    synth->read_pos = (synth->read_pos + 5) % capacity;
    mixer->synth_fifo_used -= 5;
    synth->eof_credits += 5;
    synth->eof_markers++;
    if (!synth->eof) {
        synth->eof = true;
        synth->eof_frame = synth->frames;
        error_report("W800_DSP_SYNTH_EOF frame=%"PRIu64" queued=%u",
                     synth->frames, mixer->synth_fifo_used);
    }
    return true;
}

static bool w800_dsp_synth_render(W800State *s, unsigned frames)
{
#ifdef __APPLE__
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer->synth;
    unsigned capacity = s->dsp_stream_capacity[1];
    while (mixer->synth_fifo_used >= 5 && capacity) {
        if (w800_dsp_synth_eof_record(s)) { continue; }
        uint16_t event[5];
        for (unsigned i = 0; i < 5; i++) {
            event[i] = s->dsp_stream_words[1][(synth->read_pos + i) % capacity];
        }
        uint64_t time = event[3] | ((uint32_t)event[4] << 16);
        if (time >= synth->frames + frames) { break; }
        unsigned status = event[0];
        if (synth->eof || status < 0x80 || status >= 0xf0 || event[1] > 127 || event[2] > 127) {
            if (!synth->unsupported_reported) {
                error_report("W800_DSP_MIDI_UNSUPPORTED event=%04x,%04x,%04x; retained in FIFO",
                             event[0], event[1], event[2]);
                synth->unsupported_reported = true;
            }
            return false;
        }
        unsigned offset = time > synth->frames ? time - synth->frames : 0;
        unsigned channel = status & 15;
        if ((status & 0xf0) == 0x90 && event[2]) {
            W800DspSynthVoice *slot = NULL, *oldest = NULL;
            for (unsigned i = 0; i < mixer->synth_voice_count; i++) {
                W800DspSynthVoice *candidate = &mixer->synth_voices[i];
                if (!candidate->active && !slot) { slot = candidate; }
                if (candidate->active && candidate->channel == channel && candidate->note == event[1]) {
                    slot = candidate;
                    break;
                }
                if (!oldest || candidate->phase < oldest->phase) { oldest = candidate; }
            }
            if (!slot) {
                slot = oldest;
                if (synth->midi(synth->unit, 0x80 | slot->channel, slot->note, 0, offset)) { return false; }
            }
            *slot = (W800DspSynthVoice) { .active = true, .channel = channel,
                .note = event[1], .velocity = event[2], .phase = synth->events };
        } else if ((status & 0xf0) == 0x80 || ((status & 0xf0) == 0x90 && !event[2]) ||
                   ((status & 0xf0) == 0xb0 && (event[1] == 120 || event[1] == 123))) {
            for (unsigned i = 0; i < mixer->synth_voice_count; i++) {
                W800DspSynthVoice *slot = &mixer->synth_voices[i];
                if (slot->channel == channel && (slot->note == event[1] || (status & 0xf0) == 0xb0)) {
                    slot->active = false;
                }
            }
        }
        OSStatus result = synth->midi(synth->unit, status, event[1], event[2], offset);
        if (result) {
            error_report("W800_DSP_MIDI_ERROR status=%d", (int)result);
            synth->failed = true;
            return false;
        }
        synth->read_pos = (synth->read_pos + 5) % capacity;
        mixer->synth_fifo_used -= 5;
        synth->credits += 5;
        synth->events++;
    }
    float left[W800_SYNTH_FRAMES], right[W800_SYNTH_FRAMES];
    struct {
        UInt32 count;
        AudioBuffer buffers[2];
    } buffers = {2, {{1, frames * sizeof(float), left},
                    {1, frames * sizeof(float), right}}};
    AudioTimeStamp timestamp = { .mSampleTime = synth->frames,
                                .mFlags = kAudioTimeStampSampleTimeValid };
    AudioUnitRenderActionFlags flags = 0;
    OSStatus result = synth->render(synth->unit, &flags, &timestamp, 0, frames,
                                   (AudioBufferList *)&buffers);
    if (result) {
        error_report("W800_DSP_RENDER_ERROR status=%d", (int)result);
        synth->failed = true;
        return false;
    }
    /* Keep the synthesizer's stereo channels in the host sink and apply
     * actual crosspoint routing, output EQ and gains. The separate acoustic
     * table format remains outside this compatible equalizer implementation.
     */
    for (unsigned i = 0; i < frames; i++) {
        for (unsigned side = 0; side < 2; side++) {
            double sample = (side ? right[i] : left[i]) * mixer->synth_settings[4];
            double value = w800_dsp_mixer_process_sample(mixer,
                mixer->synth_settings[5 + side], side, sample);
            int16_t pcm = (int16_t)MAX(-32768.0, MIN(32767.0, value));
            synth->pcm[i * 2 + side] = pcm;
            synth->nonzero += pcm != 0;
        }
    }
    synth->frames += frames;
    synth->rendered += frames;
    synth->pcm_bytes = frames * 4;
    synth->pcm_offset = 0;
    if (synth->eof && !mixer->synth_fifo_used &&
        (flags & kAudioUnitRenderAction_OutputIsSilence)) {
        synth->renderer_silent = true;
        error_report("W800_DSP_SYNTH_TAIL_DRAINED frames=%"PRIu64" flags=%x",
                     synth->frames, (unsigned)flags);
    }
    if (synth->nonzero && !synth->pcm_reported) {
        error_report("W800_DSP_PCM source=guest-midi-stream1 backend=AudioUnit-GM rate=%u events=%"PRIu64,
                     mixer->output_rate, synth->events);
        synth->pcm_reported = true;
    }
    return true;
#else
    return false;
#endif
}

static void w800_dsp_synth_callback(void *opaque, int available)
{
    W800State *s = opaque;
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer ? mixer->synth : NULL;
    if (synth) { synth->callbacks++; }
    if (!synth || !synth->running || synth->failed || !mixer->synth_writer) { return; }
    if (!synth->events && !mixer->synth_fifo_used && !synth->eof) { return; }
    if (synth->eof) {
        while (w800_dsp_synth_eof_record(s)) { }
    }
    if (synth->renderer_silent && synth->pcm_offset == synth->pcm_bytes) {
        /* audio_template.h allocates one extra resampler lookahead frame.
         * Full callback space (apart from that silent lookahead) means
         * the voice and sink have consumed the queued PCM. Keep the voice
         * active, without generating more data, until that space returns.
         */
        int buffer_bytes = AUD_get_buffer_size_out(synth->voice);
        if (buffer_bytes > 4 && available >= buffer_bytes - 4) {
            synth->pcm_drained = true;
        }
        w800_dsp_synth_credits(s);
        w800_dsp_synth_complete(s);
        return;
    }
    while (available >= 4) {
        if (synth->pcm_offset == synth->pcm_bytes) {
            if (synth->renderer_silent) { break; }
            unsigned frames = MIN(available / 4, W800_SYNTH_FRAMES);
            if (!w800_dsp_synth_render(s, frames)) { break; }
        }
        unsigned bytes = MIN(available, synth->pcm_bytes - synth->pcm_offset);
        unsigned written = AUD_write(synth->voice, (uint8_t *)synth->pcm + synth->pcm_offset, bytes);
        if (!written) { break; }
        synth->pcm_offset += written;
        synth->submitted += written / 4;
        available -= written;
    }
    w800_dsp_synth_credits(s);
}

static W800DspSynth *w800_dsp_synth_new(W800State *s, unsigned rate)
{
#ifdef __APPLE__
    W800DspSynth *synth = g_new0(W800DspSynth, 1);
    const char *stage = "load AudioToolbox";
    OSStatus status = 0;
    synth->framework = dlopen("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox", RTLD_LAZY | RTLD_LOCAL);
    if (!synth->framework) { goto fail; }
#define W800_AU_LOAD(field, symbol) do { \
    synth->field = dlsym(synth->framework, #symbol); \
    if (!synth->field) { goto fail; } \
} while (0)
    W800_AU_LOAD(find, AudioComponentFindNext);
    W800_AU_LOAD(create, AudioComponentInstanceNew);
    W800_AU_LOAD(dispose, AudioComponentInstanceDispose);
    W800_AU_LOAD(set, AudioUnitSetProperty);
    W800_AU_LOAD(initialize, AudioUnitInitialize);
    W800_AU_LOAD(uninitialize, AudioUnitUninitialize);
    W800_AU_LOAD(render, AudioUnitRender);
    W800_AU_LOAD(reset, AudioUnitReset);
    W800_AU_LOAD(midi, MusicDeviceMIDIEvent);
#undef W800_AU_LOAD
    AudioComponentDescription description = { .componentType = kAudioUnitType_MusicDevice,
        .componentSubType = kAudioUnitSubType_DLSSynth, .componentManufacturer = kAudioUnitManufacturer_Apple };
    AudioComponent component = synth->find(NULL, &description);
    stage = "create General MIDI component";
    if (!component || (status = synth->create(component, &synth->unit))) { goto fail; }
    AudioStreamBasicDescription format = { .mSampleRate = rate,
        .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagsNativeFloatPacked | kAudioFormatFlagIsNonInterleaved,
        .mBytesPerPacket = 4, .mFramesPerPacket = 1, .mBytesPerFrame = 4,
        .mChannelsPerFrame = 2, .mBitsPerChannel = 32 };
    UInt32 max_frames = W800_SYNTH_FRAMES;
    stage = "set PCM format";
    status = synth->set(synth->unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 0,
                         &format, sizeof(format));
    if (status) { goto fail; }
    stage = "set render block size";
    status = synth->set(synth->unit, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0,
                         &max_frames, sizeof(max_frames));
    if (status) { goto fail; }
    stage = "initialize synthesizer";
    status = synth->initialize(synth->unit);
    if (status) { goto fail; }
    stage = "open QEMU audio voice";
    synth->backend = audio_be_by_name("w800audio", NULL);
    if (!synth->backend && !AUD_backend_check(&synth->backend, NULL)) { goto fail; }
    struct audsettings settings = { .freq = rate, .nchannels = 2,
        .fmt = AUDIO_FORMAT_S16, .endianness = 0 };
    synth->voice = AUD_open_out(synth->backend, NULL, "W800 DSP MIDI", s,
                               w800_dsp_synth_callback, &settings);
    if (!synth->voice) { goto fail; }
    error_report("W800_DSP_SYNTH_BACKEND AudioUnit-GM rate=%u sink=%s", rate,
                 audio_be_get_id(synth->backend));
    return synth;
fail:
    error_report("W800_DSP_SYNTH_BACKEND unavailable stage=%s status=%d; no PCM or consumption credits generated",
                 stage, (int)status);
    w800_dsp_synth_free(synth);
    return NULL;
#else
    error_report("W800_DSP_SYNTH_BACKEND unavailable on this host");
    return NULL;
#endif
}

static void w800_dsp_synth_rewind(W800State *s, bool flush_fifo)
{
    W800DspMixer *mixer = s->dsp_mixer;
    W800DspSynth *synth = mixer ? mixer->synth : NULL;
    if (!synth) { return; }
    w800_dsp_synth_activity(s, false);
#ifdef __APPLE__
    synth->reset(synth->unit, kAudioUnitScope_Global, 0);
#endif
    synth->frames = 0;
    synth->credits += synth->eof_credits;
    synth->eof_credits = 0;
    synth->completion_requested = false;
    synth->eof = synth->renderer_silent = synth->pcm_drained = synth->completion_sent = false;
    synth->eof_frame = synth->eof_markers = 0;
    synth->pcm_bytes = synth->pcm_offset = 0;
    for (unsigned output = 0; output < 5; output++) {
        w800_dsp_equalizer_reset(&mixer->equalizer[output]);
    }
    memset(mixer->synth_voices, 0, mixer->synth_voice_count * sizeof(*mixer->synth_voices));
    if (flush_fifo) {
        synth->read_pos = synth->credits = 0;
        mixer->synth_fifo_used = mixer->synth_fifo_write = 0;
    }
}
