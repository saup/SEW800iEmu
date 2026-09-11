/* Read-only system codec availability probe. Opens no audio output and reads
 * no media; creating a converter is not evidence of successful packet decode.
 */
#include <AudioToolbox/AudioToolbox.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>

int main(int argc, char **argv)
{
    const struct { const char *name; AudioFormatID format; double rate; UInt32 frames; } codecs[] = {
        {"AMR-NB",kAudioFormatAMR,8000,160},
        {"AMR-WB",kAudioFormatAMR_WB,16000,320},
        {"AAC-LC",kAudioFormatMPEG4AAC,16000,1024},
        {"MP3",kAudioFormatMPEGLayer3,22050,576},
    };
    bool encode = argc > 1;
    (void)argv;
    printf("[\n");
    for (unsigned i = 0; i < sizeof(codecs)/sizeof(*codecs); i++) {
        UInt32 bytes = 0;
        AudioFormatID format = codecs[i].format;
        OSStatus query = AudioFormatGetPropertyInfo(encode ? kAudioFormatProperty_Encoders : kAudioFormatProperty_Decoders,
                                                    sizeof(format),&format,&bytes);
        AudioStreamBasicDescription input = {
            .mSampleRate=codecs[i].rate,.mFormatID=format,
            .mFramesPerPacket=codecs[i].frames,.mChannelsPerFrame=1,
        };
        AudioStreamBasicDescription output = {
            .mSampleRate=codecs[i].rate,.mFormatID=kAudioFormatLinearPCM,
            .mFormatFlags=kAudioFormatFlagIsSignedInteger|kAudioFormatFlagIsPacked,
            .mBytesPerPacket=2,.mFramesPerPacket=1,.mBytesPerFrame=2,
            .mChannelsPerFrame=1,.mBitsPerChannel=16,
        };
        AudioConverterRef converter = NULL;
        OSStatus created = AudioConverterNew(encode ? &output : &input, encode ? &input : &output, &converter);
        AudioConverterPrimeInfo prime = {0};
        UInt32 prime_bytes = sizeof(prime);
        OSStatus prime_status = converter ? AudioConverterGetProperty(converter,
            kAudioConverterPrimeInfo, &prime_bytes, &prime) : created;
        printf("  {\"codec\":\"%s\",\"format\":\"%c%c%c%c\",\"query_status\":%d,"
               "\"codec_count\":%u,\"create_status\":%d,\"converter\":%s,\"prime_status\":%d,\"leading_frames\":%u,\"trailing_frames\":%u}%s\n",
               codecs[i].name,(format>>24)&255,(format>>16)&255,(format>>8)&255,format&255,
               (int)query,(unsigned)(bytes/sizeof(AudioClassDescription)),(int)created,
               converter?"true":"false",(int)prime_status,(unsigned)prime.leadingFrames,
               (unsigned)prime.trailingFrames,i+1<sizeof(codecs)/sizeof(*codecs)?",":"");
        if (converter) { AudioConverterDispose(converter); }
    }
    printf("]\n");
    return 0;
}
