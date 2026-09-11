/* Original ARM equalizer coefficient protocol; compatible arithmetic HLE.
 * GPL-2.0-or-later. This is not a C55x instruction or accumulator model.
 *
 * ARM446a24e8 sends five signed Q14 words b0,b1,b2,a1,a2 and a numerator
 * exponent. ARM4469ce60 enforces unity DC for interior stages, while
 * 4469cf22..66 divides only b0..b2 when incrementing the exponent. Thus:
 * H(z) = 2^exponent (b0+b1 z^-1+b2 z^-2) / (16384+a1 z^-1+a2 z^-2).
 * Cascades retain the original increasing stage order. Double precision
 * avoids intermediate PCM16 clipping; only the existing final sink clips.
 */
#include <math.h>
#define W800_EQ_STAGES 9
#define W800_EQ_CHANNELS 2
#define W800_EQ_STATE_LIMIT 0x1p40

typedef struct W800DspEqualizerStage {
    int16_t coefficient[5];
    uint16_t exponent;
    bool identity;
    double b[3], a[2];
    double history[W800_EQ_CHANNELS][2];
} W800DspEqualizerStage;

typedef struct W800DspEqualizer {
    uint16_t count, enabled_mask, nonflat_mask;
    bool reported;
    W800DspEqualizerStage stage[W800_EQ_STAGES];
    uint64_t processed[W800_EQ_CHANNELS], limited;
} W800DspEqualizer;

static void w800_dsp_equalizer_reset(W800DspEqualizer *eq)
{
    for (unsigned i = 0; i < W800_EQ_STAGES; i++) {
        memset(eq->stage[i].history, 0, sizeof(eq->stage[i].history));
    }
}

static bool w800_dsp_equalizer_configure(W800DspEqualizer *eq, bool enabled,
                                         unsigned count, unsigned index,
                                         unsigned exponent, const int16_t *c)
{
    if (!enabled) {
        /* The disabled native builder initializes only opcode/target/enable;
         * its allocated tail is not configuration and must not be validated.
         */
        memset(eq, 0, sizeof(*eq));
        return true;
    }
    if (!c || !count || count > W800_EQ_STAGES || index >= count || exponent > 15) {
        return false;
    }
    /* Exact integer Schur conditions for denominator 1+a1*z^-1+a2*z^-2.
     * Reject unstable/marginal configurations rather than acknowledging a
     * filter that would later be bypassed. Observed native presets are stable.
     */
    if (c[4] <= -16384 || c[4] >= 16384 ||
        16384 + (int)c[3] + c[4] <= 0 ||
        16384 - (int)c[3] + c[4] <= 0) {
        return false;
    }
    if (eq->count != count) {
        memset(eq, 0, sizeof(*eq));
    }
    eq->count = count;
    W800DspEqualizerStage *stage = &eq->stage[index];
    memset(stage, 0, sizeof(*stage));
    memcpy(stage->coefficient, c, sizeof(stage->coefficient));
    stage->exponent = exponent;
    int64_t scale = 1LL << exponent;
    stage->identity = c[0] * scale == 16384 &&
                      c[1] * scale == c[3] && c[2] * scale == c[4];
    eq->nonflat_mask = (eq->nonflat_mask & ~(1u << index)) |
                      (stage->identity ? 0 : 1u << index);
    eq->reported = false;
    for (unsigned i = 0; i < 3; i++) {
        stage->b[i] = ldexp((double)c[i], (int)exponent - 14);
    }
    stage->a[0] = c[3] / 16384.0;
    stage->a[1] = c[4] / 16384.0;
    eq->enabled_mask |= 1u << index;
    return true;
}

static double w800_dsp_equalizer_bound(W800DspEqualizer *eq, double value)
{
    /* Large guard headroom bounds corrupted streams/overflow without
     * applying an invented limiter to ordinary native equalizer levels.
     * The limit is a host HLE bound, not a claimed C55x accumulator width.
     */
    if (!isfinite(value)) {
        eq->limited++;
        return 0;
    }
    if (fabs(value) > W800_EQ_STATE_LIMIT) {
        eq->limited++;
        return copysign(W800_EQ_STATE_LIMIT, value);
    }
    return value;
}

static double w800_dsp_equalizer_process(W800DspEqualizer *eq,
                                         unsigned side, double sample)
{
    if (!eq->enabled_mask || side >= W800_EQ_CHANNELS) { return sample; }
    sample = w800_dsp_equalizer_bound(eq, sample);
    eq->processed[side]++;
    for (unsigned i = 0; i < eq->count; i++) {
        W800DspEqualizerStage *stage = &eq->stage[i];
        if (!(eq->enabled_mask & (1u << i)) || stage->identity) { continue; }
        double *state = stage->history[side];
        double output = w800_dsp_equalizer_bound(eq, stage->b[0] * sample + state[0]);
        double first = stage->b[1] * sample - stage->a[0] * output + state[1];
        double second = stage->b[2] * sample - stage->a[1] * output;
        state[0] = w800_dsp_equalizer_bound(eq, first);
        state[1] = w800_dsp_equalizer_bound(eq, second);
        sample = output;
    }
    return sample;
}
