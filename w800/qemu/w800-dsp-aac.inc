/* Original channel18 decoder resource lifecycle and stream4 input FIFO.
 * ARM4488d660 emits0e04; callback4488d5d8 maps0e05 to native message17d.
 * This owns real bounded input storage. Configuration and decoding remain
 * separate operations; allocating the FIFO does not produce audio samples.
 * GPL-2.0-or-later.
 */
#include "w800-dsp-resample.inc"

typedef struct W800DspAAC {
    unsigned used, write, read, credits, eof_credits;
    unsigned rate, channels, codec, packet_frames, output_rate;
    uint16_t configuration[27];
    uint16_t threshold;
    bool writer;
    W800DspResampler resampler, resampler_second;
    AudioBackend *backend;
    SWVoiceOut *voice;
    bool running, failed, reported;
    bool underflow;
    bool eof, decoder_drained, pcm_drained, completion_sent;
    unsigned pcm_bytes, pcm_offset, packet_bytes;
    uint64_t packets, decoded, submitted, nonzero;
    uint8_t next_packet[2048], retained_packet[2048];
    uint8_t mp3_data[2048];
    uint16_t mp3_block_bytes[256], mp3_block_words[256];
    unsigned mp3_bytes, mp3_blocks, mp3_held_words;
    int16_t pcm[1024 * 3 * 2];
#ifdef __APPLE__
    void *framework;
    AudioConverterRef decoder;
    __typeof__(&AudioConverterNew) create;
    __typeof__(&AudioConverterDispose) dispose;
    __typeof__(&AudioConverterSetProperty) set;
    __typeof__(&AudioConverterFillComplexBuffer) decode;
    __typeof__(&AudioConverterReset) reset;
    AudioStreamPacketDescription packet_description;
    bool packet_supplied;
#endif
} W800DspAAC;

static void w800_dsp_aac_reset(W800State *s)
{
    if (s->dsp_aac && s->dsp_aac->voice) {
        AUD_close_out(s->dsp_aac->backend, s->dsp_aac->voice);
        error_report("W800_DSP_AAC_END packets=%"PRIu64" decoded=%"PRIu64
                     " submitted=%"PRIu64" nonzero=%"PRIu64,
                     s->dsp_aac->packets, s->dsp_aac->decoded,
                     s->dsp_aac->submitted, s->dsp_aac->nonzero);
    }
#ifdef __APPLE__
    if (s->dsp_aac) {
        if (s->dsp_aac->decoder) { s->dsp_aac->dispose(s->dsp_aac->decoder); }
        if (s->dsp_aac->framework) { dlclose(s->dsp_aac->framework); }
    }
#endif
    g_clear_pointer(&s->dsp_aac, g_free);
    s->dsp_stream_capacity[4] = 0;
    memset(s->dsp_stream_words[4], 0, sizeof(s->dsp_stream_words[4]));
}

static bool w800_dsp_aac_configure(W800DspAAC *aac, const uint8_t *data,
                                   unsigned bytes)
{
#ifdef __APPLE__
    /* ARM4488d6e4/4488d930: word1 is the rate enum, word2 encodes
     * mono/stereo, word3 selects the codec in this observed48-byte form.
     * DemoTour uses1/cc01 (AAC), Latin.mp3 uses2/cc02 (MPEG Layer3).
     * Latin's44100->44100 configuration has no FIR stages. Walkman Stop
     * uses22050->44100 with one2:1 FIR stage (native descriptor filter2).
     */
    static const unsigned rates[] = {8000, 11025, 12000, 16000, 22050,
                                     24000, 32000, 44100, 48000};
    static const unsigned aac_rates[] = {11, 10, 9, 8, 7, 6, 5, 4, 3};
    if ((bytes != 48 && bytes != 54) || aac->decoder ||
        lduw_le_p(data + 2) >= ARRAY_SIZE(rates) ||
        lduw_le_p(data + 4) > 1) { return false; }
    unsigned codec = lduw_le_p(data + 6);
    if (codec == 5) {
        /* Original4488d982 builds this54-byte AMR-NB configuration.
         * The final3/1/option/0/cc03 fields are retained verbatim and
         * accepted only in the actually observed form until their other
         * settings are established. Its two real FIR resources are still
         * required before starting the consumer.
         */
        static const uint16_t observed[] = {
            0x0e00,0,0,5,0,3,2,2,3,0,1,1,0,32,32,0,0,1,0xffff,2,
            0,0xffff,3,1,0,0,0xcc03,
        };
        if (bytes != sizeof(observed)) { return false; }
        for (unsigned i = 0; i < ARRAY_SIZE(observed); i++) {
            if (lduw_le_p(data + i * 2) != observed[i]) { return false; }
        }
    } else if ((codec != 1 && codec != 2) || bytes != 48 ||
               lduw_le_p(data + 46) != 0xcc00 + codec) { return false; }
    unsigned rate_index = lduw_le_p(data + 2);
    if (codec == 2) {
        bool direct = rate_index == 7 && !lduw_le_p(data + 12) &&
                      !lduw_le_p(data + 14) && !lduw_le_p(data + 38);
        bool twice = rate_index == 4 && lduw_le_p(data + 12) == 1 &&
                     lduw_le_p(data + 14) == 2 && lduw_le_p(data + 20) == 1 &&
                     lduw_le_p(data + 26) == 32 && !lduw_le_p(data + 32) &&
                     lduw_le_p(data + 38) == 1;
        if (lduw_le_p(data + 10) != 2 || (!direct && !twice)) { return false; }
    }
    unsigned channels = lduw_le_p(data + 4) + 1;
    unsigned packet_frames = codec == 5 ? 160 : codec == 1 ? 1024 : rate_index == 4 ? 576 : 1152;
    if (!aac->framework) {
        aac->framework = dlopen("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox",
                               RTLD_LAZY | RTLD_LOCAL);
        if (!aac->framework) { return false; }
        aac->create = dlsym(aac->framework, "AudioConverterNew");
        aac->dispose = dlsym(aac->framework, "AudioConverterDispose");
        aac->set = dlsym(aac->framework, "AudioConverterSetProperty");
        aac->decode = dlsym(aac->framework, "AudioConverterFillComplexBuffer");
        aac->reset = dlsym(aac->framework, "AudioConverterReset");
    }
    if (!aac->create || !aac->dispose || !aac->set || !aac->decode || !aac->reset) { return false; }
    AudioStreamBasicDescription input = {
        .mSampleRate = rates[rate_index],
        .mFormatID = codec == 5 ? kAudioFormatAMR :
                     codec == 1 ? kAudioFormatMPEG4AAC : kAudioFormatMPEGLayer3,
        .mFormatFlags = codec == 1 ? kMPEG4Object_AAC_LC : 0,
        .mFramesPerPacket = packet_frames,
        .mChannelsPerFrame = channels,
    };
    AudioStreamBasicDescription output = {
        .mSampleRate = rates[rate_index], .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
        .mBytesPerPacket = channels * 2, .mFramesPerPacket = 1,
        .mBytesPerFrame = channels * 2, .mChannelsPerFrame = channels,
        .mBitsPerChannel = 16,
    };
    OSStatus status = aac->create(&input, &output, &aac->decoder);
    if (status) {
        error_report("W800_DSP_AAC_CONFIG_ERROR create status=%d", (int)status);
        return false;
    }
    unsigned asc = (2 << 11) | (aac_rates[rate_index] << 7) | (channels << 3);
    /* AudioConverter requires the MPEG-4 ES/DecoderConfig descriptors
     * around AudioSpecificConfig; the two ASC bytes alone are rejected.
     * ES id0, audio object type40, audio stream15, unspecified bitrate.
     */
    const uint8_t cookie[] = {
        3, 22, 0, 0, 0, 4, 17, 0x40, 0x15,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        5, 2, asc >> 8, asc & 255,
    };
    if (codec == 1) {
        status = aac->set(aac->decoder, kAudioConverterDecompressionMagicCookie,
                          sizeof(cookie), cookie);
    }
    if (status) {
        error_report("W800_DSP_AAC_CONFIG_ERROR cookie status=%d", (int)status);
        aac->dispose(aac->decoder);
        aac->decoder = NULL;
        return false;
    }
    aac->rate = rates[rate_index];
    aac->channels = channels;
    aac->codec = codec;
    aac->packet_frames = packet_frames;
    aac->output_rate = codec == 2 ? 44100 : 48000;
    for (unsigned i = 0; i < bytes / 2; i++) {
        aac->configuration[i] = lduw_le_p(data + i * 2);
    }
    error_report("W800_DSP_AAC_CONFIG codec=%s rate=%u channels=%u backend=AudioConverter",
                 codec == 5 ? "AMR-NB" : codec == 1 ? "AAC-LC" : "MP3", aac->rate, channels);
    return true;
#else
    return false;
#endif
}

#ifdef __APPLE__
#define W800_AAC_NEED_INPUT ((OSStatus)0x77383030)
static OSStatus w800_dsp_aac_input(AudioConverterRef converter, UInt32 *packets,
                                   AudioBufferList *buffers,
                                   AudioStreamPacketDescription **description,
                                   void *opaque)
{
    W800DspAAC *aac = opaque;
    if (aac->eof) {
        *packets = 0;
        return 0;  /* Actual terminal stream record: flush decoder output. */
    }
    if (aac->packet_supplied) {
        /* The SDK specifies a nonzero callback result for temporarily
         * unavailable input; zero packets with success would signal EOF.
         */
        *packets = 0;
        return W800_AAC_NEED_INPUT;
    }
    memcpy(aac->retained_packet, aac->next_packet, aac->packet_bytes);
    buffers->mNumberBuffers = 1;
    buffers->mBuffers[0] = (AudioBuffer) {aac->channels, aac->packet_bytes,
                                        aac->retained_packet};
    aac->packet_description = (AudioStreamPacketDescription) {
        .mStartOffset = 0, .mVariableFramesInPacket = aac->packet_frames,
        .mDataByteSize = aac->packet_bytes,
    };
    if (description) { *description = &aac->packet_description; }
    *packets = 1;
    aac->packet_supplied = true;
    return 0;
}
#endif

static void w800_dsp_aac_credits(W800State *s)
{
    W800DspAAC *aac = s->dsp_aac;
    if (!aac || !aac->writer || !aac->credits || s->dsp_reply_pending ||
        (s->dsp_regs[0x04 / 4] & 1) ||
        (aac->credits < aac->threshold && !aac->underflow &&
         (aac->used || aac->mp3_held_words))) { return; }
    uint8_t reply[4];
    uint16_t opcode = aac->pcm_drained ? 0x0204 : 0x0504;
    stw_le_p(reply, opcode);
    stw_le_p(reply + 2, aac->credits);
    w800_dsp_stage_reply(s, 20, reply, sizeof(reply), opcode);
    aac->credits = 0;
}

static void w800_dsp_aac_complete(W800State *s)
{
    W800DspAAC *aac = s->dsp_aac;
    if (!aac || !aac->running || aac->failed || aac->completion_sent ||
        !aac->eof || !aac->decoder_drained || !aac->pcm_drained ||
        aac->used || aac->credits || aac->eof_credits ||
        aac->mp3_bytes || aac->mp3_held_words || s->dsp_reply_pending ||
        (s->dsp_regs[0] & 1) || (s->dsp_regs[0x04 / 4] & 1)) { return; }
    /* Original4488cb64 consumes status2 as natural end, shuts the writer
     * down, and returns the video UI to its native preview. Only report
     * this after compressed input, decoder/FIR tail, and QEMU PCM drain.
     */
    const uint8_t reply[] = {0x06, 0x0e, 2, 0};
    w800_dsp_stage_reply(s, 18, reply, sizeof(reply), 0x0e06);
    aac->completion_sent = true;
    aac->running = false;
    AUD_set_active_out(aac->voice, false);
    error_report("W800_DSP_AAC_COMPLETE packets=%"PRIu64" decoded=%"PRIu64
                 " submitted=%"PRIu64" reply=0e06 status=2",
                 aac->packets, aac->decoded, aac->submitted);
}

static void w800_dsp_mp3_consumed(W800DspAAC *aac, unsigned bytes);

static void w800_dsp_aac_eof_records(W800State *s)
{
    W800DspAAC *aac = s->dsp_aac;
    unsigned capacity = s->dsp_stream_capacity[4];
    while (capacity && aac->used >= 6) {
        uint16_t header[6];
        for (unsigned i = 0; i < 6; i++) {
            header[i] = s->dsp_stream_words[4][(aac->read + i) % capacity];
        }
        /* Native Latin uses the continuation flag0 in its terminal
         * record; AAC records retain flag1. Neither is compressed data.
         */
        if (header[0] != 2 || (header[1] != 1 && (aac->codec != 2 || header[1])) ||
            header[2] != 1 || header[5]) { break; }
        if (aac->codec == 2 && aac->mp3_bytes) {
            /* Original4488de16 zeroes the read buffer;4488dcea..dcf0
             * rounds its byte count up to words before4488dd92 packs it.
             * Stop's actual153391 MPEG bytes arrive as153392 bytes, with
             * exactly one terminal00 padding byte. Only after complete
             * MPEG frames have consumed all other bytes and a validated
             * EOF is queued can that final alignment byte be released.
             * A nonzero byte or an incomplete frame remains owned.
             */
            if (aac->mp3_bytes != 1 || aac->mp3_data[0] || !aac->mp3_blocks) {
                break;
            }
            w800_dsp_mp3_consumed(aac, 1);
            error_report("W800_DSP_MP3_ALIGNMENT bytes=1 value=00 terminal=1");
        }
        aac->read = (aac->read + 6) % capacity;
        aac->used -= 6;
        aac->eof_credits += 6;
        if (!aac->eof) {
            aac->eof = true;
            error_report("W800_DSP_AAC_EOF packets=%"PRIu64" decoded=%"PRIu64,
                         aac->packets, aac->decoded);
        }
    }
}

static bool w800_dsp_mp3_fill(W800State *s)
{
    W800DspAAC *aac = s->dsp_aac;
    unsigned capacity = s->dsp_stream_capacity[4];
    while (capacity && aac->used >= 6) {
        uint16_t header[6];
        for (unsigned i = 0; i < 6; i++) {
            header[i] = s->dsp_stream_words[4][(aac->read + i) % capacity];
        }
        if (header[0] == 2 && header[1] <= 1 && header[2] == 1 && !header[5]) { break; }
        unsigned words = header[5];
        if (header[0] != 1 || header[1] > 1 || header[2] != 1 ||
            !words || words > capacity - 6) {
            error_report("W800_DSP_MP3_UNSUPPORTED_RECORD kind=%u flags=%u,%u words=%u",
                         header[0], header[1], header[2], words);
            aac->failed = true;
            return false;
        }
        if (aac->used < words + 6 || words * 2 > sizeof(aac->mp3_data) - aac->mp3_bytes ||
            aac->mp3_blocks == ARRAY_SIZE(aac->mp3_block_bytes)) { break; }
        /* Captured ARM4488ddcc records hold250 words of continuous MP3
         * bytes. Frame boundaries cross records; flag1 is set on the first
         * chunk, then0. Hold each record's credits until every byte in it
         * has actually been supplied to the MPEG decoder.
         */
        for (unsigned i = 0; i < words; i++) {
            uint16_t word = s->dsp_stream_words[4][(aac->read + 6 + i) % capacity];
            aac->mp3_data[aac->mp3_bytes++] = word >> 8;
            aac->mp3_data[aac->mp3_bytes++] = word;
        }
        aac->mp3_block_bytes[aac->mp3_blocks] = words * 2;
        aac->mp3_block_words[aac->mp3_blocks++] = words + 6;
        aac->mp3_held_words += words + 6;
        aac->read = (aac->read + words + 6) % capacity;
        aac->used -= words + 6;
    }
    return true;
}

static bool w800_dsp_mp3_packet(W800DspAAC *aac)
{
    if (aac->mp3_bytes < 4) { return false; }
    const uint8_t *packet = aac->mp3_data;
    static const unsigned bitrate[2][16] = {
        {0,8,16,24,32,40,48,56,64,80,96,112,128,144,160,0},
        {0,32,40,48,56,64,80,96,112,128,160,192,224,256,320,0},
    };
    unsigned version = (packet[1] >> 3) & 3;
    unsigned mpeg1 = version == 3;
    unsigned kbps = bitrate[mpeg1][packet[2] >> 4];
    static const unsigned rates[] = {44100, 48000, 32000, 0};
    unsigned rate = rates[(packet[2] >> 2) & 3] >> (mpeg1 ? 0 : 1);
    unsigned channels = (packet[3] >> 6) == 3 ? 1 : 2;
    unsigned bytes = rate ? (mpeg1 ? 144000 : 72000) * kbps / rate + ((packet[2] >> 1) & 1) : 0;
    if (packet[0] != 0xff || (packet[1] & 0xe6) != 0xe2 ||
        (version != 2 && version != 3) || rate != aac->rate ||
        !kbps || channels != aac->channels || bytes > sizeof(aac->next_packet)) {
        error_report("W800_DSP_MP3_UNSUPPORTED_FRAME prefix=%02x%02x%02x%02x bytes=%u; retained",
                     packet[0], packet[1], packet[2], packet[3], aac->mp3_bytes);
        aac->failed = true;
        return false;
    }
    if (aac->mp3_bytes < bytes) { return false; }
    aac->packet_bytes = bytes;
    memcpy(aac->next_packet, packet, bytes);
    return true;
}

static void w800_dsp_mp3_consumed(W800DspAAC *aac, unsigned bytes)
{
    aac->mp3_bytes -= bytes;
    memmove(aac->mp3_data, aac->mp3_data + bytes, aac->mp3_bytes);
    while (bytes && aac->mp3_blocks) {
        unsigned part = MIN(bytes, aac->mp3_block_bytes[0]);
        bytes -= part;
        aac->mp3_block_bytes[0] -= part;
        if (aac->mp3_block_bytes[0]) { continue; }
        aac->credits += aac->mp3_block_words[0];
        aac->mp3_held_words -= aac->mp3_block_words[0];
        aac->mp3_blocks--;
        memmove(aac->mp3_block_bytes, aac->mp3_block_bytes + 1,
                aac->mp3_blocks * sizeof(*aac->mp3_block_bytes));
        memmove(aac->mp3_block_words, aac->mp3_block_words + 1,
                aac->mp3_blocks * sizeof(*aac->mp3_block_words));
    }
}

static bool w800_dsp_aac_render_packet(W800State *s)
{
#ifdef __APPLE__
    W800DspAAC *aac = s->dsp_aac;
    unsigned capacity = s->dsp_stream_capacity[4];
    uint16_t header[6];
    unsigned payload_words = 0;
    if (aac->decoder_drained || !capacity) { return false; }
    if (aac->codec == 2) {
        if (!w800_dsp_mp3_fill(s)) { return false; }
        w800_dsp_aac_eof_records(s);
        if (!aac->eof && !w800_dsp_mp3_packet(aac)) { return false; }
        goto decode;
    }
    w800_dsp_aac_eof_records(s);
    if (aac->used < 6 && !aac->eof) { return false; }
    if (aac->eof) { goto decode; }
    for (unsigned i = 0; i < 6; i++) {
        header[i] = s->dsp_stream_words[4][(aac->read + i) % capacity];
    }
    payload_words = header[5];
    if (header[0] != 1 || header[1] != 1 || header[2] != 1 ||
        payload_words < 5 || payload_words > capacity - 6) { goto unsupported; }
    if (aac->used < payload_words + 6) { return false; }
    uint8_t packet[2048];
    for (unsigned i = 0; i < payload_words; i++) {
        uint16_t word = s->dsp_stream_words[4][(aac->read + 6 + i) % capacity];
        packet[i * 2] = word >> 8;
        packet[i * 2 + 1] = word;
    }
    /* Original4488ef30 inserts an8-byte firmware prefix. Its length bits
     * differ from standard ADTS; byte6 is a rolling marker. Reconstructed
     * bytes[8:length] match actual MP4 AAC samples through stsc/stco/stsz.
     */
    if (aac->codec == 1) {
        unsigned bytes = (packet[4] << 5) | (packet[5] >> 3);
        if (packet[0] != 0xff || packet[1] != 0xf1 || packet[2] != 0x60 ||
            packet[3] != (aac->channels << 6) || (packet[5] & 7) != 7 ||
            packet[7] || bytes <= 8 || (bytes + 1) / 2 != payload_words ||
            (bytes & 1 && packet[bytes])) { goto unsupported; }
        aac->packet_bytes = bytes - 8;
        memcpy(aac->next_packet, packet + 8, aac->packet_bytes);
    } else if (aac->codec == 5) {
        /* Original4488e3d4 expands AMR ToC/speech bits into fixed34-byte
         * slots;4488ed38 returns one slot per20ms. Actual Rock Star
         * records contain17 words, ToC3c and244 speech bits, then unused
         * slot bytes. The original bit-count table is4417fcf8. Only the
         * indicated speech bytes belong to the AudioConverter packet.
         * Clear the RTP continuation bit when passing one standalone
         * frame; preserve frame type and quality. FT15 is the native
         * fcfc gap/no-data form, not fabricated input or an EOF marker.
         */
        static const unsigned bits[] = {95,103,118,134,148,159,204,244,39};
        unsigned type = (packet[0] >> 3) & 15;
        if (payload_words != 17 || (packet[0] & 3) ||
            (type >= ARRAY_SIZE(bits) && type != 15)) { goto unsupported; }
        aac->packet_bytes = type == 15 ? 1 : 1 + (bits[type] + 7) / 8;
        memcpy(aac->next_packet, packet, aac->packet_bytes);
        aac->next_packet[0] &= 0x7f;
    }
decode:
    aac->packet_supplied = false;
    int16_t decoded[1152 * 2], filtered[1024 * 3 * 2];
    AudioBufferList output = { .mNumberBuffers = 1,
        .mBuffers = {{aac->channels, sizeof(decoded), decoded}} };
    UInt32 frames = aac->packet_frames;
    OSStatus status = aac->decode(aac->decoder, w800_dsp_aac_input, aac,
                                 &frames, &output, NULL);
    if (status && status != W800_AAC_NEED_INPUT) {
        error_report("W800_DSP_AAC_DECODE_ERROR status=%d bytes=%u", (int)status, aac->packet_bytes);
        aac->failed = true;
        return false;
    }
    if (aac->packet_supplied) {
        if (aac->codec == 2) {
            w800_dsp_mp3_consumed(aac, aac->packet_bytes);
        } else {
            unsigned consumed = payload_words + 6;
            aac->read = (aac->read + consumed) % capacity;
            aac->used -= consumed;
            aac->credits += consumed;
        }
        aac->packets++;
    }
    aac->decoded += frames;
    if (!frames) {
        if (!aac->eof) { return false; }
        aac->decoder_drained = true;
        if (!aac->resampler.configured) { return false; }
        /* These64/96-tap2x/3x filters retain31 input-history frames.
         * Feed the actual remaining history through, rather than dropping
         * its tail or inventing a timed completion delay.
         */
        frames = 31;
        memset(decoded, 0, sizeof(decoded));
    }
    unsigned resampled = frames;
    if (aac->codec == 5) {
        /* A160-frame AMR unit becomes320 intermediate and960 output
         * frames. At true decoder EOF, first propagate the31-frame
         * first-stage history, then feed31 zero intermediate frames to
         * drain stage2. This yields279 actual tail output frames.
         */
        int16_t intermediate[160 * 2 + 31];
        unsigned middle = w800_dsp_resampler_process(&aac->resampler,
                                                     decoded, frames, 1, intermediate);
        if (aac->decoder_drained) {
            memset(intermediate + middle, 0, 31 * sizeof(*intermediate));
            middle += 31;
        }
        resampled = w800_dsp_resampler_process(&aac->resampler_second,
                                               intermediate, middle, 1, filtered);
    } else if (aac->resampler.configured) {
        resampled = w800_dsp_resampler_process(&aac->resampler,
                                              decoded, frames, aac->channels, filtered);
    } else {
        memcpy(filtered, decoded, frames * aac->channels * sizeof(*decoded));
    }
    double gains[2] = {0, 0};
    W800DspMixer *mixer = s->dsp_mixer;
    for (unsigned side = 0; mixer && side < 2; side++) {
        unsigned input = aac->configuration[20 + (aac->channels == 1 ? 0 : side)];
        if (input >= 7) { continue; }
        for (unsigned output_pin = 0; output_pin < 5; output_pin++) {
            if (mixer->output[output_pin].selector && mixer->crosspoint[input][output_pin].enabled) {
                gains[side] += (mixer->crosspoint[input][output_pin].gain / 16384.0) *
                               (mixer->output[output_pin].gain / 16384.0);
            }
        }
    }
    for (unsigned i = 0; i < resampled; i++) {
        for (unsigned side = 0; side < 2; side++) {
            unsigned input = aac->configuration[20 + (aac->channels == 1 ? 0 : side)];
            double value = w800_dsp_mixer_process_sample(mixer, input, side,
                filtered[i * aac->channels + (side % aac->channels)]);
            aac->pcm[i * 2 + side] = MAX(-32768.0, MIN(32767.0, value));
            aac->nonzero += aac->pcm[i * 2 + side] != 0;
        }
    }
    aac->pcm_bytes = resampled * 4;
    aac->pcm_offset = 0;
    if (!aac->reported && aac->nonzero) {
        error_report("W800_DSP_AAC_PCM source=guest-stream4 rate=%u packets=%"PRIu64
                     " gains=%g,%g codec=%u", aac->output_rate, aac->packets,
                     gains[0], gains[1], aac->codec);
        aac->reported = true;
    }
    return true;
unsupported:
    error_report("W800_DSP_AAC_UNSUPPORTED_HEADER kind=%u flags=%u,%u words=%u; retained in FIFO",
                 header[0], header[1], header[2], header[5]);
    aac->failed = true;
#endif
    return false;
}

static bool w800_dsp_aac_presentation_due(W800State *s, int available)
{
    W800DspAAC *aac = s->dsp_aac;
    unsigned capacity = s->dsp_stream_capacity[4];
    uint16_t header[6];
    if (aac->codec != 1 || !capacity || aac->used < 6) { return true; }
    for (unsigned i = 0; i < 6; i++) {
        header[i] = s->dsp_stream_words[4][(aac->read + i) % capacity];
    }
    /* Incomplete/unsupported/terminal records retain their existing parser
     * paths. Only a complete normal AAC record has a fresh presentation
     * timestamp: ARM4488dd3e stores its high half before its low half.
     */
    if (header[0] != 1 || header[1] != 1 || header[2] != 1 ||
        header[5] < 5 || header[5] > capacity - 6 ||
        aac->used < header[5] + 6) { return true; }
    uint32_t presentation = (uint32_t)header[3] << 16 | header[4];
    uint32_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000;
    int32_t until = presentation - now;
    int bytes = MAX(0, AUD_get_buffer_size_out(aac->voice));
    unsigned queued = bytes - MIN(bytes, MAX(0, available));
    /* QEMU audio_run_out subtracts both hardware pending and software mixed
     * frames from callback availability. Capacity minus that availability
     * therefore gives the queued output position, with at most one output
     * frame of rate-conversion rounding. Clamp its byte bounds and keep the
     * peripheral clock identical to the microsecond clock returned in0e01.
     * A future record stays owned and earns no consumption credit. No audio
     * is invented to fill time, and terminal metadata is never scheduled.
     */
    uint64_t queued_us = (uint64_t)queued * 1000000 / (aac->output_rate * 4);
    return until <= 0 || (uint64_t)until <= queued_us;
}

static void w800_dsp_aac_callback(void *opaque, int available)
{
    W800State *s = opaque;
    W800DspAAC *aac = s->dsp_aac;
    if (!aac || (!aac->running && !aac->underflow) ||
        !aac->writer || aac->failed) { return; }
    if (aac->running && aac->pcm_offset == aac->pcm_bytes) {
        w800_dsp_aac_eof_records(s);
    }
    if (aac->decoder_drained && aac->pcm_offset == aac->pcm_bytes) {
        int buffer_bytes = AUD_get_buffer_size_out(aac->voice);
        if (buffer_bytes > 4 && available >= buffer_bytes - 4) {
            aac->pcm_drained = true;
            aac->credits += aac->eof_credits;
            aac->eof_credits = 0;
        }
        w800_dsp_aac_credits(s);
        w800_dsp_aac_complete(s);
        return;
    }
    while (available >= 4) {
        if (aac->pcm_offset == aac->pcm_bytes &&
            (!aac->running || !w800_dsp_aac_presentation_due(s, available) ||
             !w800_dsp_aac_render_packet(s))) { break; }
        unsigned bytes = MIN(available & ~3, aac->pcm_bytes - aac->pcm_offset);
        unsigned written = AUD_write(aac->voice, (uint8_t *)aac->pcm + aac->pcm_offset, bytes);
        if (!written) { break; }
        aac->pcm_offset += written;
        aac->submitted += written / 4;
        available -= written;
    }
    /* Original4488cb90 status1 moves AAC running state3 to rebuffering4.
     * Input starvation precedes both local PCM submission and host drain.
     * Report that state before releasing the final consumed input credits:
     * 0504 wakes the native writer, which may request3ba7 refill immediately.
     * Delaying status1 until PCM submission placed it64ms after that sync
     * in the captured native failure, leaving subsequent data unserviced.
     * An occupied reply retains its ownership. Once status1 is consumed,
     * callbacks drain retained PCM and credits while new decoding waits for
     * native selector0. Native pause/stop/close still controls the voice.
     */
    if (aac->codec == 1 && !aac->underflow && aac->packets && !aac->failed &&
        !aac->eof && !aac->decoder_drained && !aac->completion_sent &&
        !aac->used && !aac->eof_credits &&
        !aac->mp3_bytes && !aac->mp3_held_words) {
        if (s->dsp_reply_pending || (s->dsp_regs[0] & 1) ||
            (s->dsp_regs[0x04 / 4] & 1)) { return; }
        const uint8_t reply[] = {0x06, 0x0e, 1, 0};
        w800_dsp_stage_reply(s, 18, reply, sizeof(reply), 0x0e06);
        aac->underflow = true;
        aac->running = false;
        error_report("W800_DSP_AAC_UNDERRUN packets=%"PRIu64" decoded=%"PRIu64
                     " submitted=%"PRIu64" pending_pcm=%u credits=%u reply=0e06 status=1",
                     aac->packets, aac->decoded, aac->submitted,
                     aac->pcm_bytes - aac->pcm_offset, aac->credits);
    }
    w800_dsp_aac_credits(s);
}

static bool w800_dsp_aac_activity(W800State *s, unsigned operation)
{
#ifdef __APPLE__
    W800DspAAC *aac = s->dsp_aac;
    if (!aac || operation > 2) { return false; }
    if (operation == 0 && (!aac->decoder ||
        ((aac->codec == 1 || aac->configuration[6]) &&
         !aac->resampler.configured) ||
        (aac->codec == 5 && !aac->resampler_second.configured))) { return false; }
    if (operation == 0 && !aac->voice) {
        aac->backend = audio_be_by_name("w800audio", NULL);
        if (!aac->backend && !AUD_backend_check(&aac->backend, NULL)) { return false; }
        struct audsettings settings = { .freq = aac->output_rate, .nchannels = 2,
            .fmt = AUDIO_FORMAT_S16, .endianness = 0 };
        aac->voice = AUD_open_out(aac->backend, NULL,
                                 aac->codec == 5 ? "W800 DSP AMR" :
                                 aac->codec == 1 ? "W800 DSP AAC" : "W800 DSP MP3", s,
                                 w800_dsp_aac_callback, &settings);
        if (!aac->voice) { return false; }
    }
    if (operation == 1) {
        if (aac->decoder && aac->reset(aac->decoder)) { return false; }
        memset(aac->resampler.history, 0, sizeof(aac->resampler.history));
        aac->resampler.position = 0;
        memset(aac->resampler_second.history, 0, sizeof(aac->resampler_second.history));
        aac->resampler_second.position = 0;
        if (s->dsp_mixer) {
            for (unsigned output = 0; output < ARRAY_SIZE(s->dsp_mixer->equalizer); output++) {
                w800_dsp_equalizer_reset(&s->dsp_mixer->equalizer[output]);
            }
        }
        aac->pcm_bytes = aac->pcm_offset = 0;
        aac->failed = false;
        aac->eof = aac->decoder_drained = aac->pcm_drained = aac->completion_sent = false;
    }
    aac->underflow = false;
    aac->running = operation == 0;
    if (aac->voice) { AUD_set_active_out(aac->voice, aac->running); }
    error_report("W800_DSP_AAC_ACTIVITY operation=%u running=%u submitted=%"PRIu64,
                 operation, aac->running, aac->submitted);
    return true;
#else
    return false;
#endif
}

static bool w800_dsp_aac_request(W800State *s, unsigned channel,
                                 const uint8_t *data, unsigned bytes)
{
    if (bytes < 2) { return false; }
    unsigned opcode = lduw_le_p(data);
    W800DspAAC *aac = s->dsp_aac;
    unsigned capacity = s->dsp_stream_capacity[4];
    if (channel == 18 && opcode == 0x0e04 && bytes == 4 &&
        w800_dsp_aac_activity(s, lduw_le_p(data + 2))) {
        const uint8_t reply[] = {0x05, 0x0e, 1, 0};
        w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0e05);
        return true;
    }
    if (channel == 20 && opcode == 0x0704 && bytes == 2 && aac && capacity) {
        unsigned returned = aac->used + aac->credits + aac->eof_credits + aac->mp3_held_words;
        aac->used = aac->write = aac->read = aac->credits = aac->eof_credits = 0;
        aac->mp3_bytes = aac->mp3_blocks = aac->mp3_held_words = 0;
        uint8_t reply[4];
        stw_le_p(reply, 0x0204);
        stw_le_p(reply + 2, returned);
        w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0204);
        error_report("W800_DSP_AAC_FLUSH returned=%u", returned);
        return true;
    }
    if (channel == 20 && opcode == 0x0104 && bytes == 2 && aac && capacity) {
        /* Shared ARM448d5240 stop-writer protocol, same as stream1. */
        aac->writer = aac->running = false;
        if (aac->voice) { AUD_set_active_out(aac->voice, false); }
        w800_dsp_stage_reply(s, channel, data, bytes, opcode);
        return true;
    }
    if (channel == 18 && opcode == 0x0e02 && aac && bytes >= 8 &&
        ((aac->rate == 16000 && aac->configuration[5] == 3 && aac->configuration[7] == 3) ||
         (aac->rate == 22050 && aac->configuration[5] == 2 && aac->configuration[7] == 2)) &&
        aac->configuration[6] == 1 &&
        aac->configuration[10] == 1 && aac->configuration[13] == 32 &&
        aac->configuration[16] == 0 && aac->configuration[19] == 1 &&
        lduw_le_p(data + 2) == 0 && lduw_le_p(data + 4) == 0 &&
        lduw_le_p(data + 6) == aac->configuration[7] * 16 &&
        bytes >= 8 + lduw_le_p(data + 6) * 2) {
        /* Original446a2d6c selects one2:1 or3:1 stage. ARM4488daa6 uploads
         *32/48 coefficients to slot0; padded DMA bytes are not coefficients.
         * Acknowledge only after constructing its retained FIR resource.
         */
        int16_t coefficients[48];
        unsigned count = lduw_le_p(data + 6), interpolation = aac->configuration[7];
        for (unsigned i = 0; i < count; i++) {
            coefficients[i] = (int16_t)lduw_le_p(data + 8 + i * 2);
        }
        if (!w800_dsp_resampler_init(&aac->resampler, coefficients, count, interpolation)) {
            return false;
        }
        const uint8_t reply[] = {0x03, 0x0e};
        w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0e03);
        error_report("W800_DSP_AAC_FILTER slot=0 half_taps=%u history=32 ratio=%u:1",
                     count, interpolation);
        return true;
    }
    if (channel == 18 && opcode == 0x0e02 && aac && aac->codec == 5 && bytes >= 8 &&
        !lduw_le_p(data + 2) && lduw_le_p(data + 4) < 2) {
        unsigned slot = lduw_le_p(data + 4), count = lduw_le_p(data + 6);
        unsigned interpolation = aac->configuration[7 + slot];
        if (count != interpolation * 16 || bytes < 8 + count * 2) { return false; }
        int16_t coefficients[48];
        for (unsigned i = 0; i < count; i++) {
            coefficients[i] = (int16_t)lduw_le_p(data + 8 + i * 2);
        }
        W800DspResampler *filter = slot ? &aac->resampler_second : &aac->resampler;
        if (!w800_dsp_resampler_init(filter, coefficients, count, interpolation)) { return false; }
        const uint8_t reply[] = {0x03, 0x0e};
        w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0e03);
        error_report("W800_DSP_AMR_FILTER slot=%u half_taps=%u history=32 ratio=%u:1",
                     slot, count, interpolation);
        return true;
    }
    if (channel == 18 && opcode == 0x0e00 && aac &&
        w800_dsp_aac_configure(aac, data, bytes)) {
        /* Native4488da10 checks result1 and reads a high-word-first
         * microsecond clock for its ARM/DSP time-base offset. This uses
         * the actual device virtual clock; no decoded frame is reported.
         */
        uint8_t reply[8];
        uint32_t usec = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000;
        stw_le_p(reply, 0x0e01);
        stw_le_p(reply + 2, 1);
        stw_le_p(reply + 4, usec >> 16);
        stw_le_p(reply + 6, usec);
        w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0e01);
        return true;
    }
    if (channel == 20 && opcode == 0x0404 && bytes == 4 && aac &&
        capacity && lduw_le_p(data + 2) < capacity) {
        /* Peer writer notification, not a reader-start request to echo. */
        aac->threshold = lduw_le_p(data + 2);
        aac->writer = true;
        return true;
    }
    if (channel == 20 && opcode == 0x0804 && bytes >= 4 &&
        aac && aac->writer && capacity) {
        unsigned words = lduw_le_p(data + 2);
        if (!words || words * 2 + 4 > bytes || words > capacity - aac->used - aac->mp3_held_words) {
            return false;
        }
        for (unsigned i = 0; i < words; i++) {
            s->dsp_stream_words[4][aac->write] = lduw_le_p(data + 4 + i * 2);
            aac->write = (aac->write + 1) % capacity;
        }
        aac->used += words;
        if (words >= 6) {
            /* Read-only transport evidence: ARM4488dd3e writes a high-word-
             * first presentation timestamp. Kind2 can retain stale fields;
             * logging them does not make them valid terminal metadata.
             */
            uint32_t usec = (uint32_t)lduw_le_p(data + 10) << 16 |
                            lduw_le_p(data + 12);
            error_report("W800_DSP_AAC_INPUT words=%u queued=%u first=%04x"
                         " flags=%u,%u pts_us=%u payload_words=%u"
                         " virtual_us=%"PRIu64" packets=%"PRIu64
                         " decoded=%"PRIu64" submitted=%"PRIu64,
                         words, aac->used, lduw_le_p(data + 4),
                         lduw_le_p(data + 6), lduw_le_p(data + 8), usec,
                         lduw_le_p(data + 14),
                         (uint64_t)(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000),
                         aac->packets, aac->decoded, aac->submitted);
        } else {
            error_report("W800_DSP_AAC_INPUT words=%u queued=%u first=%04x",
                         words, aac->used, lduw_le_p(data + 4));
        }
        /* Credits will be returned only when a decoder consumes input. */
        return true;
    }
    if (channel != 18 || opcode != 0x0e04 || bytes != 4 ||
        s->dsp_reply_pending || (s->dsp_regs[0x04 / 4] & 1)) {
        return false;
    }
    unsigned operation = lduw_le_p(data + 2);
    if ((operation != 3 || aac || capacity) &&
        (operation != 4 || !aac || !capacity)) { return false; }
    bool open = operation == 3;
    if (open) {
        /* ARM4488beec/449cabb8 opens the decoder before4488dbec queries
         * stream4's advertised capacity and creates its matching buffers.
         */
        s->dsp_aac = g_new0(W800DspAAC, 1);
        memset(s->dsp_stream_words[4], 0, sizeof(s->dsp_stream_words[4]));
        s->dsp_stream_capacity[4] = ARRAY_SIZE(s->dsp_stream_words[4]);
    } else {
        /* ARM449cafa8 releases the decoder after stopping its writer. */
        w800_dsp_aac_reset(s);
    }
    memset(s->dsp_regs + 0x80 / 4, 0, 0x40);
    w800_dsp_mailbox_word(s, 0x80, open ? 0x180a : 0x100a);
    w800_dsp_mailbox_word(s, 0x82, open ? 0x0304 : 0x0604);
    w800_dsp_mailbox_word(s, 0x84, open ? s->dsp_stream_capacity[4] : 20);
    if (open) { w800_dsp_mailbox_word(s, 0x86, 20); }
    s->dsp_regs[0x04 / 4] = 1;
    w800_dsp_notify(s, 2);
    /* Native0e05 word1 is the result of this actual resource operation,
     * checked by4488d6b8. The pending response waits for RX ownership.
     */
    const uint8_t reply[] = {0x05, 0x0e, 1, 0};
    w800_dsp_stage_reply(s, channel, reply, sizeof(reply), 0x0e05);
    error_report("W800_DSP_AAC_RESOURCE operation=%u capacity=%u reply=0e05",
                 operation, s->dsp_stream_capacity[4]);
    return true;
}
