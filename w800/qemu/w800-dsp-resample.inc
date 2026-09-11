/* Stateful 2:1/3:1 PCM interpolation with the guest's half-stored FIR table.
 * GPL-2.0-or-later. Native descriptors specify32/48 coefficients and32
 * input-history frames. Symmetry and Q15 rounding are compatible HLE,
 * inferred from those dimensions and coefficient gain; not C55x execution.
 */
typedef struct W800DspResampler {
    int16_t coefficients[96];
    int16_t history[32][2];
    unsigned position, interpolation;
    bool configured;
} W800DspResampler;

static bool w800_dsp_resampler_init(W800DspResampler *filter,
                                    const int16_t *coefficients,
                                    unsigned count, unsigned interpolation)
{
    if ((interpolation != 2 && interpolation != 3) || count != interpolation * 16) {
        return false;
    }
    memset(filter, 0, sizeof(*filter));
    for (unsigned i = 0; i < count; i++) {
        filter->coefficients[i] = coefficients[i];
        filter->coefficients[count * 2 - 1 - i] = coefficients[i];
    }
    filter->interpolation = interpolation;
    filter->configured = true;
    return true;
}

static unsigned w800_dsp_resampler_process(W800DspResampler *filter,
                                           const int16_t *input, unsigned frames,
                                           unsigned channels, int16_t *output)
{
    if (!filter->configured || !channels || channels > 2) { return 0; }
    for (unsigned frame = 0; frame < frames; frame++) {
        for (unsigned side = 0; side < channels; side++) {
            filter->history[filter->position][side] = input[frame * channels + side];
        }
        for (unsigned phase = 0; phase < filter->interpolation; phase++) {
            for (unsigned side = 0; side < channels; side++) {
                int64_t sum = 0;
                for (unsigned tap = 0; tap < 32; tap++) {
                    sum += (int64_t)filter->coefficients[phase + filter->interpolation * tap] *
                           filter->history[(filter->position + 32 - tap) % 32][side];
                }
                int64_t sample = (sum + 16384) >> 15;
                output[(frame * filter->interpolation + phase) * channels + side] =
                    sample < -32768 ? -32768 : sample > 32767 ? 32767 : sample;
            }
        }
        filter->position = (filter->position + 1) % 32;
    }
    return frames * filter->interpolation;
}
