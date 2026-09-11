/* Read-only offline codec accounting probe over a captured guest AMR stream.
 * Produces no audio output. Input is the standard AMR fixture reconstructed
 * from actual native FIFO payloads, with its six-byte file signature removed.
 */
#include <AudioToolbox/AudioToolbox.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define NEED_INPUT ((OSStatus)0x77383030)
typedef struct { unsigned char bytes[4096]; size_t length,offset; unsigned given, limit;
 AudioStreamPacketDescription description; } Input;
static OSStatus supply(AudioConverterRef c,UInt32*n,AudioBufferList*b,AudioStreamPacketDescription**d,void*o){
 (void)c; Input*i=o;
 if(i->offset==i->length){*n=0;return 0;}
 if(i->given==i->limit){*n=0;return NEED_INPUT;}
 i->description=(AudioStreamPacketDescription){0,160,32};
 b->mNumberBuffers=1;b->mBuffers[0]=(AudioBuffer){1,32,i->bytes+i->offset};
 i->offset+=32;i->given++;*n=1;*d=&i->description;return 0;
}
int main(int argc,char**argv){
 if(argc!=5)return 2;
 FILE*f=fopen(argv[1],"rb");if(!f)return 3;
 Input i={.limit=(unsigned)atoi(argv[2])};fseek(f,6,SEEK_SET);i.length=fread(i.bytes,1,sizeof(i.bytes),f);fclose(f);
 AudioStreamBasicDescription in={.mSampleRate=8000,.mFormatID=kAudioFormatAMR,.mFramesPerPacket=160,.mChannelsPerFrame=1};
 AudioStreamBasicDescription out={.mSampleRate=8000,.mFormatID=kAudioFormatLinearPCM,.mFormatFlags=kAudioFormatFlagIsSignedInteger|kAudioFormatFlagIsPacked,.mBytesPerPacket=2,.mFramesPerPacket=1,.mBytesPerFrame=2,.mChannelsPerFrame=1,.mBitsPerChannel=16};
 AudioConverterRef c=NULL;OSStatus s=AudioConverterNew(&in,&out,&c);if(s)return 4;
 UInt32 method=(UInt32)atoi(argv[3]);s=AudioConverterSetProperty(c,kAudioConverterPrimeMethod,sizeof(method),&method);
 printf("prime_method=%u set_status=%d\n",method,(int)s);
 FILE*pcm=fopen(argv[4],"wb");if(!pcm)return 5;
 unsigned total=0;
 for(unsigned j=0;j<100;j++){
  short samples[4096];AudioBufferList b={.mNumberBuffers=1,.mBuffers={{1,sizeof(samples),samples}}};UInt32 frames=160;i.given=0;
  s=AudioConverterFillComplexBuffer(c,supply,&i,&frames,&b,NULL);total+=frames;fwrite(samples,2,frames,pcm);
  printf("call=%u consumed=%u offset=%zu status=%d frames=%u total=%u\n",j,i.given,i.offset,(int)s,frames,total);
  if(i.offset==i.length&&!frames)break;
 }
 AudioConverterPrimeInfo prime={0};UInt32 size=sizeof(prime);s=AudioConverterGetProperty(c,kAudioConverterPrimeInfo,&size,&prime);
 printf("prime_info_status=%d leading=%u trailing=%u\n",(int)s,prime.leadingFrames,prime.trailingFrames);
 fclose(pcm);AudioConverterDispose(c);return 0;
}
