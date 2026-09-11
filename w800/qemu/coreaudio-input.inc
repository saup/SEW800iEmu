/* CoreAudio microphone input for the original guest recording device.
 * No media-file input and no change to the output backend. GPL-2.0-or-later.
 */
#include "coreaudio-input-buffer.h"

typedef struct coreaudioVoiceIn {
    HWVoiceIn hw;
    AudioDeviceID device;
    AudioDeviceIOProcID ioproc;
    pthread_mutex_t mutex;
    CoreaudioInputBuffer ring;
    bool enabled;
    uint64_t callbacks, start_calls, stop_calls, staged_discarded;
} coreaudioVoiceIn;

static OSStatus coreaudio_input_callback(AudioDeviceID device,
    const AudioTimeStamp *now, const AudioBufferList *input,
    const AudioTimeStamp *input_time, AudioBufferList *output,
    const AudioTimeStamp *output_time, void *opaque)
{
    coreaudioVoiceIn *core = opaque;
    pthread_mutex_lock(&core->mutex);
    if (core->enabled && input) {
        core->callbacks++;
        coreaudio_input_push(&core->ring, input);
    }
    pthread_mutex_unlock(&core->mutex);
    return noErr;
}

static int coreaudio_init_in(HWVoiceIn *hw, audsettings *requested, void *opaque)
{
    coreaudioVoiceIn *core = (coreaudioVoiceIn *)hw;
    Audiodev *dev = opaque;
    AudiodevCoreaudioPerDirectionOptions *options = dev->u.coreaudio.in;
    AudioObjectPropertyAddress property = {
        kAudioHardwarePropertyDefaultInputDevice,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain,
    };
    UInt32 size = sizeof(core->device);
    OSStatus status = AudioObjectGetPropertyData(kAudioObjectSystemObject,
        &property, 0, NULL, &size, &core->device);
    if (status || core->device == kAudioObjectUnknown) {
        dolog("Microphone default-device query failed: %d\n", (int)status);
        return -1;
    }
    AudioStreamBasicDescription format = { 0 };
    property.mSelector = kAudioDevicePropertyStreamFormat;
    property.mScope = kAudioDevicePropertyScopeInput;
    size = sizeof(format);
    status = AudioObjectGetPropertyData(core->device, &property, 0, NULL, &size, &format);
    /* Use the existing physical device format; never change the host's sample
     * rate or gain. QEMU's input mixer performs the guest-rate conversion.
     */
    if (status || format.mFormatID != kAudioFormatLinearPCM ||
        !(format.mFormatFlags & kAudioFormatFlagIsFloat) ||
        format.mBitsPerChannel != 32 || format.mSampleRate < 8000 ||
        format.mSampleRate > 384000 || !format.mChannelsPerFrame ||
        format.mChannelsPerFrame > 2 || format.mBytesPerFrame !=
            sizeof(float) * ((format.mFormatFlags & kAudioFormatFlagIsNonInterleaved) ?
                             1 : format.mChannelsPerFrame) ||
        (format.mFormatFlags & kAudioFormatFlagIsBigEndian)) {
        dolog("Unsupported microphone format: status=%d rate=%g channels=%u bits=%u flags=%u\n",
              (int)status, format.mSampleRate, (unsigned)format.mChannelsPerFrame,
              (unsigned)format.mBitsPerChannel, (unsigned)format.mFormatFlags);
        return -1;
    }
    audsettings actual = {
        .freq = (int)format.mSampleRate, .nchannels = format.mChannelsPerFrame,
        .fmt = AUDIO_FORMAT_F32, .endianness = 0,
    };
    audio_pcm_init_info(&hw->info, &actual);
    unsigned buffer_count = options->has_buffer_count ? options->buffer_count : 4;
    if (!buffer_count || buffer_count > 64) {
        dolog("Microphone buffer-count must be between 1 and 64\n");
        return -1;
    }
    int frames = audio_buffer_frames(
        qapi_AudiodevCoreaudioPerDirectionOptions_base(options), &actual, 11610);
    if (frames <= 0 || (uint64_t)frames > (uint64_t)actual.freq * 4 / buffer_count) {
        dolog("Microphone capture buffer must hold at most four seconds\n");
        return -1;
    }
    hw->samples = (size_t)frames * buffer_count;
    core->ring.capacity = hw->samples;
    core->ring.channels = actual.nchannels;
    core->ring.data = g_new(float, hw->samples * actual.nchannels);
    if (pthread_mutex_init(&core->mutex, NULL)) {
        g_clear_pointer(&core->ring.data, g_free);
        return -1;
    }
    status = AudioDeviceCreateIOProcID(core->device, coreaudio_input_callback,
                                     core, &core->ioproc);
    if (status) {
        dolog("Microphone callback registration failed: %d\n", (int)status);
        pthread_mutex_destroy(&core->mutex);
        g_clear_pointer(&core->ring.data, g_free);
        return -1;
    }
    dolog("MICROPHONE_OPEN rate=%d channels=%d format=Float32 capacity=%zu device=%u\n",
          actual.freq, actual.nchannels, hw->samples, (unsigned)core->device);
    return 0;
}

static size_t coreaudio_read(HWVoiceIn *hw, void *buffer, size_t bytes)
{
    coreaudioVoiceIn *core = (coreaudioVoiceIn *)hw;
    pthread_mutex_lock(&core->mutex);
    size_t read = coreaudio_input_pop(&core->ring, buffer, bytes);
    pthread_mutex_unlock(&core->mutex);
    return read;
}

static void coreaudio_enable_in(HWVoiceIn *hw, bool enable)
{
    coreaudioVoiceIn *core = (coreaudioVoiceIn *)hw;
    if (enable == core->enabled) { return; }
    if (enable) {
        pthread_mutex_lock(&core->mutex);
        core->enabled = true;
        pthread_mutex_unlock(&core->mutex);
        OSStatus status = AudioDeviceStart(core->device, core->ioproc);
        core->start_calls++;
        if (status) {
            pthread_mutex_lock(&core->mutex);
            core->enabled = false;
            pthread_mutex_unlock(&core->mutex);
            dolog("MICROPHONE_START_ERROR status=%d; capture unavailable\n", (int)status);
        }
    } else {
        pthread_mutex_lock(&core->mutex);
        core->enabled = false;
        pthread_mutex_unlock(&core->mutex);
        OSStatus status = AudioDeviceStop(core->device, core->ioproc);
        core->stop_calls++;
        if (status) { dolog("Microphone stop failed: %d\n", (int)status); }
        pthread_mutex_lock(&core->mutex);
        coreaudio_input_discard(&core->ring);
        pthread_mutex_unlock(&core->mutex);
        /* QEMU's generic staging buffer is owned by the emulation thread.
         * Native activation resets the SW acquisition index separately.
         */
        core->staged_discarded += hw->pending_emul / hw->info.bytes_per_frame;
        hw->pending_emul = hw->pos_emul = 0;
    }
}

static void coreaudio_fini_in(HWVoiceIn *hw)
{
    coreaudioVoiceIn *core = (coreaudioVoiceIn *)hw;
    coreaudio_enable_in(hw, false);
    OSStatus status = AudioDeviceDestroyIOProcID(core->device, core->ioproc);
    if (status) { dolog("Microphone callback destruction failed: %d\n", (int)status); }
    dolog("MICROPHONE_CLOSE captured=%"PRIu64" delivered=%"PRIu64
          " dropped=%"PRIu64" discarded=%"PRIu64" staged_discarded=%"PRIu64
          " pending=%zu callbacks=%"PRIu64
          " starts=%"PRIu64" stops=%"PRIu64"\n",
          core->ring.captured, core->ring.delivered, core->ring.dropped,
          core->ring.discarded, core->staged_discarded,
          core->ring.used, core->callbacks, core->start_calls, core->stop_calls);
    pthread_mutex_destroy(&core->mutex);
    g_clear_pointer(&core->ring.data, g_free);
}
