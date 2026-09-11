/* DB2010 DSP host memory port, inferred from original MAIN449ff6f4/449ff764
 * and original ITCM79c/800/818. GPL-2.0-or-later.
 * This implements host transport and narrow kernel service HLE, not a C55x
 * instruction core. Guest MIDI events use compatible General MIDI synthesis
 * through QEMU audio; other compressed-audio codecs remain incomplete.
 */
#define W800_DSP_REPLY_BUFFER 0x7fc000

#include "w800-dsp-equalizer.inc"

typedef struct W800DspMixerTable {
    uint8_t *words;
    unsigned count;
} W800DspMixerTable;

typedef struct W800DspSynthVoice {
    bool active;
    uint8_t channel, note, velocity;
    uint64_t phase;
} W800DspSynthVoice;

typedef struct W800DspMixer {
    W800DspMixerTable target[256];
    W800DspEqualizer equalizer[5];
    uint16_t output_settings[21];
    unsigned output_rate;
    uint16_t mode;
    struct {
        uint16_t selector, gain;
    } input[7], output[5];
    struct {
        uint16_t enabled, mode, gain;
    } crosspoint[7][5];
    uint16_t synth_settings[7];
    W800DspSynthVoice *synth_voices;
    unsigned synth_voice_count;
    unsigned synth_fifo_used, synth_fifo_write;
    uint16_t synth_threshold;
    bool synth_writer;
    struct W800DspSynth *synth;
} W800DspMixer;

static double w800_dsp_mixer_process_sample(W800DspMixer *mixer,
                                           unsigned input, unsigned side,
                                           double sample)
{
    double mixed = 0;
    if (!mixer || input >= 7 || side >= 2) { return 0; }
    for (unsigned output = 0; output < 5; output++) {
        if (mixer->output[output].selector && mixer->crosspoint[input][output].enabled) {
            double value = sample * (mixer->crosspoint[input][output].gain / 16384.0);
            W800DspEqualizer *eq = &mixer->equalizer[output];
            value = w800_dsp_equalizer_process(eq, side, value);
            if (eq->nonflat_mask && !eq->reported) {
                error_report("W800_DSP_EQUALIZER_PCM target=%u input=%u side=%u stages=%u nonflat=%03x",
                             output, input, side, eq->count, eq->nonflat_mask);
                eq->reported = true;
            }
            mixed += value * (mixer->output[output].gain / 16384.0);
        }
    }
    return mixed;
}

static uint16_t w800_dsp_word(W800State *s, unsigned address)
{
    return s->dsp_memory[address >> 1] >> ((address & 1) ? 0 : 16);
}

static void w800_dsp_store_word(W800State *s, unsigned address, uint16_t value)
{
    unsigned shift = (address & 1) ? 0 : 16;
    s->dsp_memory[address >> 1] =
        (s->dsp_memory[address >> 1] & ~(0xffffu << shift)) | ((uint32_t)value << shift);
}

static void w800_dsp_mailbox_word(W800State *s, unsigned offset, uint16_t value)
{
    unsigned shift = (offset & 2) * 8;
    s->dsp_regs[offset >> 2] =
        (s->dsp_regs[offset >> 2] & ~(0xffffu << shift)) | ((uint32_t)value << shift);
}

static void w800_dsp_notify(W800State *s, uint32_t events)
{
    s->dsp_regs[0x10 / 4] |= events;
    w800_irq_update(s);
}

static void w800_dsp_reply_announce(void *opaque)
{
    W800State *s = opaque;
    if (!s->dsp_reply_pending || s->dsp_reply_announced) {
        return;
    }
    if (s->dsp_regs[0x04 / 4] & 1) {
        timer_mod(s->dsp_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100000);
        return;
    }
    memset(s->dsp_regs + 0x80 / 4, 0, 0x40);
    if (s->dsp_reply_size <= 60) {
        unsigned words = s->dsp_reply_size / 2;
        w800_dsp_mailbox_word(s, 0x80, ((words + 1) << 11) | 10);
        for (unsigned i = 0; i < words; i++) {
            w800_dsp_mailbox_word(s, 0x82 + i * 2,
                                 lduw_le_p(s->dsp_reply + i * 2));
        }
        w800_dsp_mailbox_word(s, 0x82 + words * 2, s->dsp_reply_channel);
        s->dsp_reply_pending = false;
        s->dsp_regs[0x04 / 4] = 1;
        w800_dsp_notify(s, 2);
        return;
    }
    /* Original ARM44736198 parses this six-halfword long-message header.
     * HLE owns a real block in DSP host-port memory, with the same allocated
     * length as the request. Guest4490d028 subsequently reads this block.
     */
    const uint16_t header[] = {
        (6 << 11) | 20, 2, s->dsp_reply_channel, s->dsp_reply_size / 2,
        W800_DSP_REPLY_BUFFER >> 16, W800_DSP_REPLY_BUFFER & 0xffff, 8
    };
    for (unsigned i = 0; i < ARRAY_SIZE(header); i++) {
        w800_dsp_mailbox_word(s, 0x80 + i * 2, header[i]);
    }
    s->dsp_regs[0x04 / 4] = 1;
    s->dsp_reply_announced = true;
    w800_dsp_notify(s, 2);
}

static unsigned w800_dsp_version_metadata(W800State *s)
{
    /* Uploaded C55x15c82 AMAR *abs(b2dc),XAR3 and15cae MOV0202,*AR2.
     * This is a deliberately narrow HLE of that evidenced kernel routine.
     * Unsupported kernels and other DSP operations remain pending.
     */
    if (w800_dsp_word(s, 0xae41) == 0xec31 &&
           w800_dsp_word(s, 0xae42) == 0xbe00 &&
           w800_dsp_word(s, 0xae43) == 0xb2dc &&
           w800_dsp_word(s, 0xae57) == 0xfb41 &&
           w800_dsp_word(s, 0xae58) == 0x0202 &&
           w800_dsp_word(s, 0xb32a) == 2) { return 0xb2dc; }
    /* The original video codec upload443a4cdc places the same routine at
     * byte15ced, an odd C55x instruction address. Its AMAR immediate is
     * b2a6 and channel2 is loaded from b2eb. Check actual uploaded bytes,
     * including the unaligned0202 store, before using that metadata.
     */
    /* Latin.mp3 uploads its matching MP3 variant from443add68. Its
     * metadata and channel move down by0200 words; the routine moves to
     * byte158c4. Both variants are checked against their actual uploads.
     */
    static const struct {
        unsigned pc, metadata, reply_pc, channel;
    } variants[] = {
        {0x15ced, 0xb2a6, 0x15d19, 0xb2eb},
        {0x158c4, 0xb0a6, 0x158f0, 0xb0eb},
        /* Rock Star's observed AMR_ASR upload uses a longer identity;
         * its actual channel load is a43100af03, not metadata+0045.
         */
        {0x153fc, 0xaeba, 0x15428, 0xaf03},
        /* Native SoundRecorder's Phone_Audio/EMLLIRA encoder upload.
         * Captured AMAR b30c and channel load a43100b37a at byte15981.
         */
        {0x1595d, 0xb30c, 0x15989, 0xb37a},
    };
    const uint8_t reply[] = {0xfb, 0x41, 0x02, 0x02};
    for (unsigned variant = 0; variant < ARRAY_SIZE(variants); variant++) {
        const uint8_t prefix[] = {0xec, 0x31, 0xbe, 0x00,
            variants[variant].metadata >> 8, variants[variant].metadata & 255};
        bool valid = w800_dsp_word(s, variants[variant].channel) == 2;
        for (unsigned i = 0; valid && i < sizeof(prefix); i++) {
            unsigned address = variants[variant].pc + i;
            uint8_t value = w800_dsp_word(s, address / 2) >> (address & 1 ? 0 : 8);
            valid = value == prefix[i];
        }
        for (unsigned i = 0; valid && i < sizeof(reply); i++) {
            unsigned address = variants[variant].reply_pc + i;
            uint8_t value = w800_dsp_word(s, address / 2) >> (address & 1 ? 0 : 8);
            valid = value == reply[i];
        }
        if (valid) { return variants[variant].metadata; }
    }
    return 0;
}

static bool w800_dsp_version_kernel_present(W800State *s)
{
    return w800_dsp_version_metadata(s) != 0;
}

static bool w800_dsp_protocol_available(W800State *s)
{
    /* The original ARM transport and mixer protocol persists across codec
     * kernel swaps. A successfully handled recognized-kernel transaction
     * establishes this device HLE endpoint; later uploads must not disable
     * physical transfers merely by relocating that version routine. The
     * version service itself still requires its exact uploaded code/data.
     */
    return s->dsp_protocol_initialized || w800_dsp_version_kernel_present(s);
}

static void w800_dsp_stage_reply(W800State *s, unsigned channel,
                                 const uint8_t *data, unsigned bytes,
                                 uint16_t opcode)
{
    memcpy(s->dsp_reply, data, bytes);
    stw_le_p(s->dsp_reply, opcode);
    for (unsigned i = 0; i < bytes / 2; i++) {
        w800_dsp_store_word(s, W800_DSP_REPLY_BUFFER + i,
                           lduw_le_p(s->dsp_reply + i * 2));
    }
    s->dsp_reply_size = bytes;
    s->dsp_reply_channel = channel;
    s->dsp_reply_pending = true;
    s->dsp_reply_announced = false;
    timer_mod(s->dsp_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100000);
}

#include "w800-dsp-synth.inc"
#include "w800-dsp-aac.inc"

static bool w800_dsp_audio_request(W800State *s, unsigned channel,
                                  const uint8_t *data, unsigned bytes)
{
    if (s->dsp_reply_pending || !w800_dsp_protocol_available(s)) {
        return false;
    }
    if (w800_dsp_aac_request(s, channel, data, bytes)) { return true; }
    if (channel == 20 && bytes == 2 && lduw_le_p(data) == 0x0701 &&
        s->dsp_mixer && s->dsp_mixer->synth && s->dsp_stream_capacity[1]) {
        W800DspMixer *mixer = s->dsp_mixer;
        W800DspSynth *synth = mixer->synth;
        /* Original448d5778 sends07 to the peer consumer. Its matching
         * receiver448d5240 discards the actual unread ring, resets both
         * indices, and sends02 with returned capacity (no wake callback).
         * Include consumed credits not yet announced, so every delivered
         * word is returned once and the ARM writer regains real capacity.
         */
        unsigned discarded = mixer->synth_fifo_used;
        uint8_t reply[4];
        stw_le_p(reply, 0x0201);
        stw_le_p(reply + 2, discarded + synth->credits + synth->eof_credits);
        memset(s->dsp_stream_words[1], 0, sizeof(s->dsp_stream_words[1]));
        mixer->synth_fifo_used = mixer->synth_fifo_write = synth->read_pos = 0;
        synth->credits = synth->eof_credits = 0;
        w800_dsp_stage_reply(s, 20, reply, sizeof(reply), 0x0201);
        error_report("W800_DSP_SYNTH_FLUSH discarded=%u returned=%u",
                     discarded, lduw_le_p(reply + 2));
        return true;
    }
    if (channel == 20 && bytes >= 4 && lduw_le_p(data) == 0x0801 &&
        s->dsp_mixer && s->dsp_mixer->synth_writer && s->dsp_stream_capacity[1]) {
        unsigned words = lduw_le_p(data + 2), capacity = s->dsp_stream_capacity[1];
        if (!words || words * 2 + 4 > bytes ||
            words > capacity - s->dsp_mixer->synth_fifo_used) { return false; }
        for (unsigned i = 0; i < words; i++) {
            s->dsp_stream_words[1][s->dsp_mixer->synth_fifo_write] = lduw_le_p(data + 4 + i * 2);
            s->dsp_mixer->synth_fifo_write = (s->dsp_mixer->synth_fifo_write + 1) % capacity;
        }
        s->dsp_mixer->synth_fifo_used += words;
        /* ARM44882aea/448d5714 supplied these actual parsed MIDI records.
         * No stream credits are replenished until a synthesizer actually
         * consumes them; copying to the FIFO is not sample production.
         */
        error_report("W800_DSP_SYNTH_EVENTS words=%u queued=%u first=%04x,%04x,%04x",
                     words, s->dsp_mixer->synth_fifo_used, lduw_le_p(data + 4),
                     words > 1 ? lduw_le_p(data + 6) : 0,
                     words > 2 ? lduw_le_p(data + 8) : 0);
        return true;
    }
    if (channel == 9 && bytes >= 16 && lduw_le_p(data) == 0x0600) {
        unsigned voices = lduw_le_p(data + 6);
        if (!voices || voices > 40 ||
            (s->dsp_mixer && s->dsp_mixer->synth_voices)) { return false; }
        /* Original synth initializer448825f0 clamps the third setting to
         * 1..40. Allocate that many real voice slots and retain every
         * delivered setting before0601. No note or sample is fabricated;
         * subsequent instrument/event consumers are required for synthesis.
         * Callback448832bc maps0601 to OS message1d4 and retains the reply
         * for the unchanged ARM448826c4 initialization continuation.
         */
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        for (unsigned i = 0; i < 7; i++) {
            s->dsp_mixer->synth_settings[i] = lduw_le_p(data + 2 + i * 2);
        }
        g_free(s->dsp_mixer->synth_voices);
        s->dsp_mixer->synth_voices = g_new0(W800DspSynthVoice, voices);
        s->dsp_mixer->synth_voice_count = voices;
        unsigned rate = s->dsp_mixer->output_rate;
        if (!rate) {
            const unsigned rates[] = {8000, 16000, 48000};
            unsigned mode = s->dsp_mixer->synth_settings[0];
            rate = mode < ARRAY_SIZE(rates) ? rates[mode] : 0;
        }
        s->dsp_mixer->synth = rate ? w800_dsp_synth_new(s, rate) : NULL;
        if (!s->dsp_mixer->synth) {
            g_clear_pointer(&s->dsp_mixer->synth_voices, g_free);
            s->dsp_mixer->synth_voice_count = 0;
            return false;
        }
        w800_dsp_stage_reply(s, channel, data, bytes, 0x0601);
        error_report("W800_DSP_SYNTH_INIT voices=%u settings=%04x,%04x,%04x,%04x,%04x,%04x,%04x reply=0601",
                     voices, s->dsp_mixer->synth_settings[0], s->dsp_mixer->synth_settings[1],
                     s->dsp_mixer->synth_settings[2], s->dsp_mixer->synth_settings[3],
                     s->dsp_mixer->synth_settings[4], s->dsp_mixer->synth_settings[5],
                     s->dsp_mixer->synth_settings[6]);
        return true;
    }
    if (channel == 9 && bytes >= 2 && s->dsp_mixer &&
        s->dsp_mixer->synth_voices && !(s->dsp_regs[0x04 / 4] & 1) &&
        ((lduw_le_p(data) == 0x0604 && !s->dsp_stream_capacity[1]) ||
         (lduw_le_p(data) == 0x0606 && s->dsp_stream_capacity[1]))) {
        bool open = lduw_le_p(data) == 0x0604;
        w800_dsp_synth_activity(s, false);
        if (open) { w800_dsp_synth_rewind(s, true); }
        /* ARM448827cc opens the event stream, then448d54b0 begins writing
         * with threshold750. ARM4488286c closes it. Allocate a bounded
         * 1024-word HLE FIFO and advertise its actual capacity through
         * logical20 stream1 before the0605 reply wakes the ARM writer.
         * These stream resources are device HLE allocations, not guessed
         * original allocator addresses or claims of an active codec clock.
         */
        memset(s->dsp_stream_words[1], 0, sizeof(s->dsp_stream_words[1]));
        s->dsp_stream_capacity[1] = open ? 1024 : 0;
        s->dsp_mixer->synth_fifo_used = s->dsp_mixer->synth_fifo_write = 0;
        s->dsp_mixer->synth_writer = false;
        memset(s->dsp_regs + 0x80 / 4, 0, 0x40);
        w800_dsp_mailbox_word(s, 0x80, open ? 0x180a : 0x100a);
        w800_dsp_mailbox_word(s, 0x82, open ? 0x0301 : 0x0601);
        w800_dsp_mailbox_word(s, 0x84, open ? 1024 : 20);
        if (open) { w800_dsp_mailbox_word(s, 0x86, 20); }
        s->dsp_regs[0x04 / 4] = 1;
        w800_dsp_notify(s, 2);
        w800_dsp_stage_reply(s, channel, data, bytes, open ? 0x0605 : 0x0607);
        error_report("W800_DSP_SYNTH_STREAM capacity=%u reply=%04x",
                     s->dsp_stream_capacity[1], open ? 0x0605 : 0x0607);
        return true;
    }
    if (channel == 9 && bytes >= 4 && lduw_le_p(data) == 0x0608 &&
        s->dsp_mixer && s->dsp_mixer->synth) {
        unsigned mode = lduw_le_p(data + 2);
        if (mode > 2) { return false; }
        /* ARM4488379c prefills events then4488383c starts with0.
         * ARM4488354c pauses with1 and sets the paused flag;448835ba
         * resumes with0 and clears that flag. ARM448847bc uses2 before
         * flushing stream1. Apply the real renderer transition first.
         */
        if (mode == 2) { w800_dsp_synth_rewind(s, false); }
        else { w800_dsp_synth_activity(s, mode == 0); }
        w800_dsp_stage_reply(s, channel, data, bytes, 0x0609);
        return true;
    }
    if (channel == 9 && bytes >= 2 && lduw_le_p(data) == 0x060a &&
        s->dsp_mixer && s->dsp_mixer->synth) {
        /* ARM44882992 sends this after starting/resuming and does not
         * wait. Callback44883358 wraps060b in the completion signal
         * handled at44883658, which closes the unpaused player. Thus
         * this requests eventual completion, not an immediate clock
         * query. Retain that subscription until actual EOF, the compatible
         * renderer's silence indication and QEMU PCM drain are complete.
         */
        s->dsp_mixer->synth->completion_requested = true;
        return true;
    }
    if (channel == 9 && bytes >= 2 && lduw_le_p(data) == 0x0602 &&
        s->dsp_mixer && s->dsp_mixer->synth_voices && !s->dsp_stream_capacity[1]) {
        /* ARM44882724 releases the synthesizer before unregistering its
         * channel. The actual voice allocation is released before0603.
         */
        g_clear_pointer(&s->dsp_mixer->synth_voices, g_free);
        w800_dsp_synth_free(s->dsp_mixer->synth);
        s->dsp_mixer->synth = NULL;
        s->dsp_mixer->synth_voice_count = 0;
        memset(s->dsp_mixer->synth_settings, 0, sizeof(s->dsp_mixer->synth_settings));
        w800_dsp_stage_reply(s, channel, data, bytes, 0x0603);
        error_report("W800_DSP_SYNTH_RELEASE reply=0603");
        return true;
    }
    if (channel == 11 && bytes >= 44 && lduw_le_p(data) == 0x0800) {
        /* ARM44a87058 sends21 exact settings from ARM44a87108. The last
         * halfword is the sample rate (8000/16000/44100 in observed modes).
         * ARM44a872d4 accepts0801 and posts OS message1b9; it reads no
         * additional response status. Preserve all other fields verbatim
         * until their bitfield meanings are established.
         */
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        for (unsigned i = 0; i < 21; i++) {
            s->dsp_mixer->output_settings[i] = lduw_le_p(data + 2 + i * 2);
        }
        s->dsp_mixer->output_rate = s->dsp_mixer->output_settings[20];
        w800_dsp_stage_reply(s, channel, data, bytes, 0x0801);
        error_report("W800_DSP_OUTPUT_CONFIG words=21 rate=%u reply=0801",
                     s->dsp_mixer->output_rate);
        return true;
    }
    if (channel != 12 || bytes < 4) { return false; }
    unsigned opcode = lduw_le_p(data), item = lduw_le_p(data + 2);
    if (opcode == 0x0900 && item >= 1 && item <= 4) {
        /* ARM446a0fec maps public modes0..3 to DSP modes1..4;4469f730
         * sends and retains this setting. Mode storage alone creates no
         * samples or synthetic channel activity.
         */
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        s->dsp_mixer->mode = item;
        w800_dsp_stage_reply(s, channel, data, bytes, opcode + 1);
        error_report("W800_DSP_MIXER_MODE value=%u reply=0901", item);
        return true;
    }
    if ((opcode == 0x0906 && bytes >= 10) || (opcode == 0x0908 && bytes >= 8)) {
        unsigned input = lduw_le_p(data + 4), output = item;
        if (input >= 7 || output >= 5) { return false; }
        /* ARM4469f184 connects an input/output crosspoint with0906;
         * ARM4469f9a8 disconnects it. ARM4469f77c mutes with0908 gain0;
         * ARM4469f860 restores the saved volume through Q14 converter
         * 446a1018. The original ARM matrix at4c0620cc has seven rows
         * of five4-byte crosspoint records. The wire order is output,input.
         */
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        unsigned value = lduw_le_p(data + 6);
        if (opcode == 0x0906) {
            s->dsp_mixer->crosspoint[input][output].enabled = value;
            s->dsp_mixer->crosspoint[input][output].mode = lduw_le_p(data + 8);
        } else {
            s->dsp_mixer->crosspoint[input][output].gain = value;
        }
        w800_dsp_stage_reply(s, channel, data, bytes, opcode + 1);
        error_report("W800_DSP_MIXER_CROSSPOINT command=%04x input=%u output=%u value=%04x reply=%04x",
                     opcode, input, output, value, opcode + 1);
        return true;
    }
    if ((opcode == 0x0902 && item < 7 && bytes >= 6) ||
        (opcode == 0x0904 && item < 5 && bytes >= 6) ||
        (opcode == 0x090a && item < 5 && bytes >= 6)) {
        /* ARM446a2144 kind0/1 emits0902/0904 with target and opaque
         * assignment selector. ARM4469eeb0 releases with selector0.
         * ARM4469f754/446a1018 supplies090a with a Q14 linear gain.
         */
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        unsigned value = lduw_le_p(data + 4);
        if (opcode == 0x0902) { s->dsp_mixer->input[item].selector = value; }
        if (opcode == 0x0904) {
            s->dsp_mixer->output[item].selector = value;
            w800_dsp_equalizer_reset(&s->dsp_mixer->equalizer[item]);
        }
        if (opcode == 0x090a) { s->dsp_mixer->output[item].gain = value; }
        w800_dsp_stage_reply(s, channel, data, bytes, opcode + 1);
        error_report("W800_DSP_MIXER_STATE command=%04x target=%u value=%04x reply=%04x",
                     opcode, item, value, opcode + 1);
        return true;
    }
    if ((opcode == 0x0912 && item < 7 && bytes >= 34) ||
        (opcode == 0x0914 && item < 5 && bytes >= 44)) {
        /* Original builders446a25bc/446a2650 initialize only command and
         * target. Never echo the uninitialized query tail. The original
         * free-slot scans4469eecc/4469ef40 use only the final halfword's
         * zero/nonzero occupancy. This reflects real retained assignments;
         * unsupported detailed status fields currently remain zero.
         */
        unsigned size = opcode == 0x0912 ? 34 : 44;
        uint8_t reply[44] = {0};
        unsigned selector = !s->dsp_mixer ? 0 : opcode == 0x0912 ?
            s->dsp_mixer->input[item].selector : s->dsp_mixer->output[item].selector;
        stw_le_p(reply + 2, item);
        stw_le_p(reply + size - 2, selector != 0);
        w800_dsp_stage_reply(s, channel, reply, size, opcode + 1);
        error_report("W800_DSP_MIXER_STATUS command=%04x target=%u occupied=%u reply=%04x",
                     opcode, item, selector != 0, opcode + 1);
        return true;
    }
    if (opcode == 0x090e && item < 5 && bytes >= 6) {
        unsigned enabled = lduw_le_p(data + 4);
        unsigned count = 0, stage = 0, exponent = 0;
        int16_t coefficient[5] = {0};
        /* Original4469fbe4/446a24e8 install an equalizer cascade with five
         * coefficients per stage;4469fd64 disables it. Generator4469ce60
         * enforces b2=(16384+a1+a2)>>exponent-b0-b1, and4469cf1e scales
         * only the numerator when increasing that exponent. The helper
         * implements that transfer with retained per-stage IIR history.
         * The target indexes the same five output records allocated by
         * 4469ef98, with EQ enable at record+0x13; it is not an input index.
         */
        if (enabled > 1 || (enabled && bytes < 22)) { return false; }
        if (enabled) {
            count = lduw_le_p(data + 6);
            stage = lduw_le_p(data + 8);
            exponent = lduw_le_p(data + 10);
            for (unsigned i = 0; i < 5; i++) {
                coefficient[i] = lduw_le_p(data + 12 + i * 2);
            }
        }
        if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
        W800DspEqualizer *eq = &s->dsp_mixer->equalizer[item];
        if (!w800_dsp_equalizer_configure(eq, enabled, count, stage, exponent, coefficient)) {
            return false;
        }
        /* Callback446a276e maps090f to OS1c1. Caller446a25a8 frees the
         * response without reading any additional status field.
         */
        w800_dsp_stage_reply(s, channel, data, bytes, 0x090f);
        error_report("W800_DSP_MIXER_EQUALIZER target=%u enabled=%u stages=%u stage=%u mask=%03x exponent=%u coefficients=%d,%d,%d,%d,%d reply=090f",
                     item, enabled, count, stage,
                     eq->enabled_mask, exponent, coefficient[0], coefficient[1],
                     coefficient[2], coefficient[3], coefficient[4]);
        return true;
    }
    if (opcode != 0x090c || bytes < 6) { return false; }
    unsigned target = lduw_le_p(data + 2), selector = lduw_le_p(data + 4);
    unsigned count = 0;
    if (target > 255 || selector > 1) { return false; }
    /* Original af_mixer.c ARM4469f48c passes signed table[0]+2 words
     * to ARM446a22bc. Command090c has target/selector followed by those
     * words; the remainder of its DMA allocation is padding. Selector0
     * disables the target's table, proven by ARM4469f660..4469f67a.
     */
    if (selector) {
        if (bytes < 10 || (int16_t)lduw_le_p(data + 6) < 0) { return false; }
        count = lduw_le_p(data + 6) + 2;
        if (count * 2 + 6 > bytes) { return false; }
    }
    if (!s->dsp_mixer) { s->dsp_mixer = g_new0(W800DspMixer, 1); }
    W800DspMixerTable *table = &s->dsp_mixer->target[target];
    uint8_t *words = count ? g_malloc(count * 2) : NULL;
    if (words) { memcpy(words, data + 6, count * 2); }
    g_free(table->words);
    table->words = words;
    table->count = count;
    /* Preserve the guest allocation, replacing its opcode only. Original
     * callback446a26e8 maps090d to OS message1bc, and446a25a8 frees it.
     * This acknowledges storage of the actual coefficients; it does not
     * assert that a decoder, mixer sample clock or PCM output is running.
     */
    w800_dsp_stage_reply(s, channel, data, bytes, 0x090d);
    error_report("W800_DSP_MIXER_TABLE target=%u selector=%u words=%u first=%04x last=%04x reply=090d",
                 target, selector, count, count ? lduw_le_p(words) : 0,
                 count ? lduw_le_p(words + (count - 1) * 2) : 0);
    return true;
}

static bool w800_dsp_queue_request(W800State *s, unsigned channel,
                                   const uint8_t *data, unsigned bytes)
{
    if (!bytes || bytes > sizeof(s->dsp_pending_requests[0])) { return false; }
    if (w800_dsp_audio_request(s, channel, data, bytes)) { return true; }
    if (s->dsp_pending_count == ARRAY_SIZE(s->dsp_pending_requests)) {
        if (!s->dsp_queue_full_reported) {
            error_report("W800_DSP_QUEUE_FULL pending=%u; unsupported services retain their requests",
                         s->dsp_pending_count);
            s->dsp_queue_full_reported = true;
        }
        return false;
    }
    unsigned index = s->dsp_pending_count++;
    memcpy(s->dsp_pending_requests[index], data, bytes);
    s->dsp_pending_lengths[index] = bytes;
    s->dsp_pending_channels[index] = channel;
    error_report("W800_DSP_SERVICE_PENDING channel=%u opcode=%04x bytes=%u queued=%u",
                 channel, bytes >= 2 ? lduw_le_p(data) : data[0], bytes,
                 s->dsp_pending_count);
    return true;
}

static void w800_dsp_service_pending(W800State *s)
{
    if (s->dsp_reply_pending) { return; }
    for (unsigned i = 0; i < s->dsp_pending_count; i++) {
        if (!w800_dsp_audio_request(s, s->dsp_pending_channels[i],
                                   s->dsp_pending_requests[i],
                                   s->dsp_pending_lengths[i])) { continue; }
        unsigned remaining = --s->dsp_pending_count - i;
        memmove(s->dsp_pending_requests[i], s->dsp_pending_requests[i + 1],
                remaining * sizeof(s->dsp_pending_requests[0]));
        memmove(s->dsp_pending_lengths + i, s->dsp_pending_lengths + i + 1,
                remaining * sizeof(s->dsp_pending_lengths[0]));
        memmove(s->dsp_pending_channels + i, s->dsp_pending_channels + i + 1,
                remaining * sizeof(s->dsp_pending_channels[0]));
        s->dsp_queue_full_reported = false;
        return;
    }
}

static void w800_dsp_transport(W800State *s)
{
    if (!s->lcd) { return; }
    w800_dsp_service_pending(s);
    w800_dsp_synth_complete(s);
    w800_dsp_aac_complete(s);
    if ((s->dsp_regs[0] & 1) && !(s->dsp_regs[0x04 / 4] & 1) &&
        w800_dsp_protocol_available(s)) {
        unsigned header = s->dsp_regs[0x40 / 4] & 0xffff;
        unsigned command = s->dsp_regs[0x40 / 4] >> 16;
        unsigned stream = command & 0xff;
        unsigned count = s->dsp_regs[0x44 / 4] & 0xffff;
        unsigned channel = s->dsp_regs[0x44 / 4] >> 16;
        bool handled = false;
        bool response = true;
        /* Functional DSP stream HLE inferred from original ARM448d543c
         * (open),448d5514(close),448d526c(response dispatcher). Opening
         * allocates real bounded stream storage; its advertised capacity
         * is that allocation. No samples or logging bytes are invented.
         */
        if (header == 0x180a && (command >> 8) == 3 && channel == 20 &&
            stream < 6 && count >= 2 && count <= 1024 &&
            !s->dsp_stream_capacity[stream]) {
            memset(s->dsp_stream_words[stream], 0, count * sizeof(uint16_t));
            s->dsp_stream_capacity[stream] = count;
            handled = true;
        } else if (header == 0x100a && (command >> 8) == 6 && count == 20 &&
                   stream < 6 && s->dsp_stream_capacity[stream]) {
            memset(s->dsp_stream_words[stream], 0,
                   s->dsp_stream_capacity[stream] * sizeof(uint16_t));
            s->dsp_stream_capacity[stream] = 0;
            handled = true;
        } else if (header == 0x180a && command == 0x0401 && channel == 20 &&
                   s->dsp_mixer && s->dsp_stream_capacity[1] &&
                   count < s->dsp_stream_capacity[1]) {
            /* Peer writer-start request: original448d54b0 sets ARM state3
             * before sending0401. Echoing0401 would incorrectly switch
             * ARM to reader state2 at448d52b0. Store the threshold and
             * acknowledge only physical ownership; future consumption
             * returns actual credits with0201/0501.
             */
            s->dsp_mixer->synth_threshold = count;
            s->dsp_mixer->synth_writer = true;
            handled = true;
            response = false;
        } else if (header == 0x100a && command == 0x0101 && count == 20 &&
                   s->dsp_mixer && s->dsp_stream_capacity[1]) {
            s->dsp_mixer->synth_writer = false;
            w800_dsp_synth_activity(s, false);
            handled = true;
        }
        if (handled) {
            if (response) {
                memcpy(s->dsp_regs + 0x80 / 4, s->dsp_regs + 0x40 / 4, 0x40);
                s->dsp_regs[0x04 / 4] = 1;
            }
            s->dsp_regs[0] &= ~1;
            w800_dsp_notify(s, response ? 3 : 1);
            qemu_log_mask(LOG_UNIMP, "W800_DSP_STREAM_HLE command=%04x stream=%u capacity=%u\n",
                          command, stream, s->dsp_stream_capacity[stream]);
        }
        if (!handled && (header & 0x7ff) == 10 && (header >> 11) >= 2) {
            unsigned halfwords = header >> 11;
            uint8_t packet[64];
            for (unsigned i = 0; i < halfwords; i++) {
                unsigned offset = 0x42 + i * 2;
                stw_le_p(packet + i * 2,
                         s->dsp_regs[offset >> 2] >> ((offset & 2) * 8));
            }
            unsigned logical = lduw_le_p(packet + (halfwords - 1) * 2);
            if (w800_dsp_queue_request(s, logical, packet, (halfwords - 1) * 2)) {
                /* Only physical mailbox ownership is acknowledged. No
                 * service result, RX packet or decoded sample is produced.
                 */
                s->dsp_regs[0] &= ~1;
                w800_dsp_notify(s, 1);
            }
        }
    }
    for (unsigned ch = 0; ch < 0x100; ch += 32) {
        uint8_t *dma = s->lcd->dma + ch;
        uint32_t source = ldl_le_p(dma), dest = ldl_le_p(dma + 4);
        uint32_t config = ldl_le_p(dma + 0xc), control = ldl_le_p(dma + 0x10);
        unsigned bytes = (config & 0xfff) * 4;
        if (!(control & 1) || ((config >> 18) & 7) != 2 ||
            ((config >> 21) & 7) != 2 || !bytes ||
            bytes > sizeof(s->dsp_pending_requests[0])) {
            continue;
        }
        if (dest == 0xfa040020 && (s->dsp_regs[0] & 1) &&
            s->dsp_regs[0x40 / 4] == 0x00032014 &&
            w800_dsp_protocol_available(s)) {
            /* Original ARM44736468 builds type3,logical,address-hi,lo
             * for4473674c addressed uploads. Transfer size comes from the
             * real PL080 channel. Copy every delivered halfword into DSP
             * host-port memory before releasing either owner. This is a
             * memory transaction, not a synthesizer service completion.
             */
            unsigned address = (s->dsp_regs[0x44 / 4] & 0xffff0000) |
                               (s->dsp_regs[0x48 / 4] & 0xffff);
            uint8_t packet[16380];
            if (address >= 0x800000 || bytes / 2 > 0x800000 - address ||
                address_space_read(&address_space_memory, source,
                    MEMTXATTRS_UNSPECIFIED, packet, bytes) != MEMTX_OK) { continue; }
            for (unsigned i = 0; i < bytes / 2; i++) {
                w800_dsp_store_word(s, address + i, lduw_le_p(packet + i * 2));
            }
            stl_le_p(dma, source + bytes);
            stl_le_p(dma + 0xc, config & ~0xfff);
            stl_le_p(dma + 0x10, control & ~1);
            if (config & 0x80000000) { s->dma_tc_raw |= 1u << (ch / 32); }
            s->dsp_regs[0] &= ~1;
            w800_dsp_notify(s, 1 | 8);
            error_report("W800_DSP_ADDRESSED_UPLOAD channel=%u address=%06x bytes=%u",
                         s->dsp_regs[0x44 / 4] & 0xffff, address, bytes);
        } else if (dest == 0xfa040020 && (s->dsp_regs[0] & 1) &&
            s->dsp_regs[0x40 / 4] == 0x00021814 &&
            (s->dsp_regs[0x44 / 4] & 0xffff) == 2 &&
            (s->dsp_regs[0x44 / 4] >> 16) * 2 == bytes &&
            bytes <= 256 &&
            !s->dsp_reply_pending && w800_dsp_version_kernel_present(s)) {
            uint8_t request[256];
            if (address_space_read(&address_space_memory, source,
                    MEMTXATTRS_UNSPECIFIED, request, bytes) != MEMTX_OK ||
                lduw_le_p(request) != 0x0201) { continue; }
            memcpy(s->dsp_reply, request, bytes);
            bool terminated = false;
            unsigned metadata = w800_dsp_version_metadata(s);
            for (unsigned i = 0; i + 2 < bytes / 2; i++) {
                uint16_t value = w800_dsp_word(s, metadata + i);
                if (value > 0x7f) { break; }
                stw_le_p(s->dsp_reply + (i + 1) * 2, value);
                if (!value) { terminated = true; break; }
            }
            if (!terminated) { continue; }
            stw_le_p(s->dsp_reply, 0x0202);
            for (unsigned i = 0; i < bytes / 2; i++) {
                w800_dsp_store_word(s, W800_DSP_REPLY_BUFFER + i,
                                   lduw_le_p(s->dsp_reply + i * 2));
            }
            s->dsp_reply_size = bytes;
            s->dsp_reply_channel = 2;
            s->dsp_reply_pending = true;
            s->dsp_reply_announced = false;
            s->dsp_protocol_initialized = true;
            stl_le_p(dma, source + bytes);
            stl_le_p(dma + 0xc, config & ~0xfff);
            stl_le_p(dma + 0x10, control & ~1);
            if (config & 0x80000000) { s->dma_tc_raw |= 1u << (ch / 32); }
            s->dsp_regs[0] &= ~1;
            w800_dsp_notify(s, 1 | 8);
            timer_mod(s->dsp_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100000);
            qemu_log_mask(LOG_UNIMP, "W800_DSP_VERSION_HLE request=0201 reply=0202 bytes=%u\n", bytes);
        } else if (dest == 0xfa040020 && (s->dsp_regs[0] & 1) &&
                   s->dsp_regs[0x40 / 4] == 0x00021814 &&
                   (s->dsp_regs[0x44 / 4] >> 16) * 2 == bytes &&
                   w800_dsp_protocol_available(s)) {
            uint8_t packet[16380];
            if (address_space_read(&address_space_memory, source,
                    MEMTXATTRS_UNSPECIFIED, packet, bytes) != MEMTX_OK ||
                !w800_dsp_queue_request(s, s->dsp_regs[0x44 / 4] & 0xffff,
                                       packet, bytes)) { continue; }
            stl_le_p(dma, source + bytes);
            stl_le_p(dma + 0xc, config & ~0xfff);
            stl_le_p(dma + 0x10, control & ~1);
            if (config & 0x80000000) { s->dma_tc_raw |= 1u << (ch / 32); }
            s->dsp_regs[0] &= ~1;
            w800_dsp_notify(s, 1 | 8);
        } else if (source == 0xfa040020 && s->dsp_reply_pending &&
                   (s->dsp_regs[0x14 / 4] & 1) &&
                   s->dsp_regs[0x18 / 4] == W800_DSP_REPLY_BUFFER &&
                   s->dsp_regs[0x1c / 4] * 4 == bytes &&
                   bytes == s->dsp_reply_size) {
            if (address_space_write(&address_space_memory, dest,
                    MEMTXATTRS_UNSPECIFIED, s->dsp_reply, bytes) != MEMTX_OK) {
                continue;
            }
            stl_le_p(dma + 4, dest + bytes);
            stl_le_p(dma + 0xc, config & ~0xfff);
            stl_le_p(dma + 0x10, control & ~1);
            if (config & 0x80000000) { s->dma_tc_raw |= 1u << (ch / 32); }
            s->dsp_regs[0x14 / 4] &= ~1;
            s->dsp_regs[0x1c / 4] = 0;
            s->dsp_reply_pending = false;
            w800_dsp_notify(s, 0x10);
            w800_dsp_service_pending(s);
        }
    }
}

static void w800_dsp_reset(W800State *s)
{
    memset(s->dsp_regs, 0, sizeof(s->dsp_regs));
    memset(s->dsp_memory, 0, 0x1000000);
    s->dsp_reads = s->dsp_writes = 0;
    s->dsp_reply_size = 0;
    s->dsp_reply_channel = 0;
    s->dsp_reply_pending = false;
    s->dsp_reply_announced = false;
    s->dsp_protocol_initialized = false;
    if (s->dsp_mixer) {
        for (unsigned i = 0; i < ARRAY_SIZE(s->dsp_mixer->target); i++) {
            g_free(s->dsp_mixer->target[i].words);
        }
        g_free(s->dsp_mixer->synth_voices);
        w800_dsp_synth_free(s->dsp_mixer->synth);
        g_clear_pointer(&s->dsp_mixer, g_free);
    }
    w800_dsp_aac_reset(s);
    s->dma_tc_raw = s->dma_tc_mask = 0;
    memset(s->dsp_stream_capacity, 0, sizeof(s->dsp_stream_capacity));
    memset(s->dsp_stream_words, 0, sizeof(s->dsp_stream_words));
    memset(s->dsp_pending_requests, 0, sizeof(s->dsp_pending_requests));
    memset(s->dsp_pending_lengths, 0, sizeof(s->dsp_pending_lengths));
    memset(s->dsp_pending_channels, 0, sizeof(s->dsp_pending_channels));
    s->dsp_pending_count = 0;
    s->dsp_queue_full_reported = false;
    if (s->dsp_timer) { timer_del(s->dsp_timer); }
}

static uint64_t w800_dsp_read(void *opaque, hwaddr offset, unsigned size)
{
    W800State *s = opaque;
    uint32_t mask = size == 4 ? UINT32_MAX : (1u << (size * 8)) - 1;
    uint32_t value = (s->dsp_regs[offset >> 2] >> ((offset & 3) * 8)) & mask;
    if ((offset >> 2) == 0x10 / 4) {
        /* Original ITCM688 drains events by reading this register until0. */
        s->dsp_regs[0x10 / 4] &= ~(mask << ((offset & 3) * 8));
        w800_irq_update(s);
    }
    return value;
}

static void w800_dsp_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    W800State *s = opaque;
    unsigned slot = offset >> 2;
    unsigned shift = (offset & 3) * 8;
    uint32_t mask = size == 4 ? UINT32_MAX : ((1u << (size * 8)) - 1) << shift;
    uint32_t previous = s->dsp_regs[slot];
    s->dsp_regs[slot] = (s->dsp_regs[slot] & ~mask) | ((value << shift) & mask);
    if (slot == 0x04 / 4) {
        /* ITCM818 acknowledges RX with 0xfffffffe. Clear the selected
         * readiness bits; acknowledgment must not create pending RX data.
         */
        s->dsp_regs[slot] = previous & (s->dsp_regs[slot] | ~mask);
    }
    if (slot == 0 && !(previous & 1) && (s->dsp_regs[slot] & 1)) {
        /* ITCM800 commits the original 32-halfword TX mailbox after its
         * ITCM79c writer fills 0x40..0x7f. Retain ownership until a DSP
         * consumer really reads it. Only the implemented kernel HLE
         * operations may generate completions while C55x execution is absent.
         * RX storage at 0x80..0xbf starts empty and never advertises data.
         */
        qemu_log_mask(LOG_UNIMP,
                      "W800_DSP_TX pc=0x%08x header=0x%08x\n",
                      s->cpu->env.regs[15], s->dsp_regs[0x40 / 4]);
        w800_dsp_transport(s);
    }
    if (slot == 0x14 / 4) { w800_dsp_transport(s); }
    if (slot == 0x04 / 4 && (previous & 1) && !(s->dsp_regs[slot] & 1)) {
        w800_dsp_transport(s);
    }
    if (slot == 0x24 / 4) {
        /* A 23-bit DSP halfword address selects a pair of 16-bit words.
         * Guest writes use the odd address; reads accept the even address.
         * Upper half of DATA is the first DSP word, lower half the second.
         */
        uint32_t index = (s->dsp_regs[0x28 / 4] & 0x7fffff) >> 1;
        if (s->dsp_regs[slot] == 1) {
            s->dsp_memory[index] = s->dsp_regs[0x2c / 4];
            s->dsp_writes++;
            s->dsp_regs[slot] = 0;
        } else if (s->dsp_regs[slot] == 2) {
            s->dsp_regs[0x2c / 4] = s->dsp_memory[index];
            s->dsp_reads++;
            s->dsp_regs[slot] = 0;
        }
        /* Unsupported commands remain visible; do not falsely acknowledge. */
    }
}

static const MemoryRegionOps w800_dsp_ops = {
    .read = w800_dsp_read, .write = w800_dsp_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};
