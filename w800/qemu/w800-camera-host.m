/* AVFoundation source for DB2010 CAMIF. Compiled with ARC.
 * No capture starts during enumeration or machine construction.
 */
#import <AVFoundation/AVFoundation.h>
#import <CoreVideo/CoreVideo.h>
#include <pthread.h>
#include <stdbool.h>
#include <string.h>
#include "w800-camera-host.h"

static pthread_mutex_t camera_lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t camera_pixels[W800_HOST_CAMERA_CAPACITY];
static uint32_t camera_width, camera_height, camera_stride;
static uint64_t camera_sequence, camera_generation;
static bool camera_wanted;
static int camera_status;
static AVCaptureSession *camera_session;
/* Session objects and observers are owned only by camera_queue. */
static uint64_t camera_session_generation;
static NSMutableArray *camera_observers;
static dispatch_queue_t camera_queue;

@interface W800CaptureSink : NSObject <AVCaptureVideoDataOutputSampleBufferDelegate>
@property(nonatomic) uint64_t generation;
@end

static W800CaptureSink *camera_sink;

@implementation W800CaptureSink
- (void)captureOutput:(AVCaptureOutput *)output
 didOutputSampleBuffer:(CMSampleBufferRef)sample
       fromConnection:(AVCaptureConnection *)connection
{
    (void)output;
    (void)connection;
    CVPixelBufferRef image = CMSampleBufferGetImageBuffer(sample);
    if (!image) { return; }
    OSType format = CVPixelBufferGetPixelFormatType(image);
    if (!(format == kCVPixelFormatType_32BGRA ||
          format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange ||
          format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange ||
          format == kCVPixelFormatType_420YpCbCr8Planar ||
          format == kCVPixelFormatType_420YpCbCr8PlanarFullRange)) { return; }

    const size_t source_width = CVPixelBufferGetWidth(image);
    const size_t source_height = CVPixelBufferGetHeight(image);
    if (!source_width || !source_height) { return; }

    uint32_t target_width = (uint32_t)source_width;
    uint32_t target_height = (uint32_t)source_height;
    if (target_width > 640 || target_height > 480) {
        if (target_width * 480 > target_height * 640) {
            target_width = 640;
            target_height = (uint32_t)((uint64_t)source_height * target_width / source_width);
        } else {
            target_height = 480;
            target_width = (uint32_t)((uint64_t)source_width * target_height / source_height);
        }
    }
    if (target_width > 640) { target_width = 640; }
    if (target_height > 480) { target_height = 480; }
    if ((target_width & 1) && target_width > 1) { target_width--; }
    if ((target_height & 1) && target_height > 1) { target_height--; }
    if (!target_width || !target_height) { return; }

    if (CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly) != kCVReturnSuccess) { return; }

    size_t bytes = (size_t)target_width * target_height * 4;
    bool wrote_frame = false;

    if (bytes <= sizeof(camera_pixels)) {
        if (camera_wanted && self.generation == camera_generation &&
            (camera_status == W800_HOST_CAMERA_PENDING ||
             camera_status == W800_HOST_CAMERA_RUNNING)) {
            if (format == kCVPixelFormatType_32BGRA) {
                const uint8_t *source = CVPixelBufferGetBaseAddress(image);
                const size_t source_stride = CVPixelBufferGetBytesPerRow(image);
                for (size_t y = 0; y < target_height; y++) {
                    size_t source_y = (size_t)((uint64_t)y * source_height / target_height);
                    const uint8_t *source_row = source + source_y * source_stride;
                    uint8_t *destination_row = camera_pixels + y * target_width * 4;
                    for (size_t x = 0; x < target_width; x++) {
                        size_t source_x = (size_t)((uint64_t)x * source_width / target_width);
                        const uint8_t *source_pixel = source_row + source_x * 4;
                        uint8_t *destination_pixel = destination_row + x * 4;
                        memcpy(destination_pixel, source_pixel, 4);
                    }
                }
                wrote_frame = true;
            } else if (CVPixelBufferIsPlanar(image)) {
                size_t plane_count = CVPixelBufferGetPlaneCount(image);
                if (plane_count >= 2) {
                    const uint8_t *y_plane = CVPixelBufferGetBaseAddressOfPlane(image, 0);
                    const uint8_t *uv_plane = CVPixelBufferGetBaseAddressOfPlane(image, 1);
                    const size_t y_stride = CVPixelBufferGetBytesPerRowOfPlane(image, 0);
                    const size_t uv_stride = CVPixelBufferGetBytesPerRowOfPlane(image, 1);
                    if (y_plane && uv_plane) {
                        for (size_t y = 0; y < target_height; y++) {
                            size_t source_y = (size_t)((uint64_t)y * source_height / target_height);
                            size_t uv_source_y = source_y / 2;
                            const uint8_t *y_row = y_plane + source_y * y_stride;
                            const uint8_t *uv_row = uv_plane + uv_source_y * uv_stride;
                            uint8_t *destination_row = camera_pixels + y * target_width * 4;
                            for (size_t x = 0; x < target_width; x++) {
                                size_t source_x = (size_t)((uint64_t)x * source_width / target_width);
                                int y = y_row[source_x];
                                size_t uv_offset = (source_x / 2) * 2;
                                int u = uv_row[uv_offset];
                                int v = uv_row[uv_offset + 1];
                                int c = y - 16;
                                if (format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange) { c = y; }
                                int d = u - 128;
                                int e = v - 128;
                                int r, g, b;
                                if (format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange) {
                                    r = (int)((c * 1000 + 1436 * e + 512) >> 10);
                                    g = (int)((c * 1000 - 352 * d - 731 * e + 512) >> 10);
                                    b = (int)((c * 1000 + 1814 * d + 512) >> 10);
                                } else {
                                    r = (298 * c + 409 * e + 128) >> 8;
                                    g = (298 * c - 100 * d - 208 * e + 128) >> 8;
                                    b = (298 * c + 516 * d + 128) >> 8;
                                }
                                uint8_t *destination_pixel = destination_row + x * 4;
                                destination_pixel[0] = b < 0 ? 0 : (b > 255 ? 255 : (uint8_t)b);
                                destination_pixel[1] = g < 0 ? 0 : (g > 255 ? 255 : (uint8_t)g);
                                destination_pixel[2] = r < 0 ? 0 : (r > 255 ? 255 : (uint8_t)r);
                                destination_pixel[3] = 0xff;
                            }
                        }
                        wrote_frame = true;
                    }
                }
            }
        }
    }

    pthread_mutex_lock(&camera_lock);
    if (wrote_frame &&
        camera_wanted && self.generation == camera_generation &&
        (camera_status == W800_HOST_CAMERA_PENDING ||
         camera_status == W800_HOST_CAMERA_RUNNING)) {
        camera_width = target_width;
        camera_height = target_height;
        camera_stride = target_width * 4;
        camera_sequence++;
        camera_status = W800_HOST_CAMERA_RUNNING;
    }
    pthread_mutex_unlock(&camera_lock);
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
}
@end

static void camera_initialize_queue(void)
{
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        camera_queue = dispatch_queue_create("local.w800.camera", DISPATCH_QUEUE_SERIAL);
    });
}

static bool camera_current(uint64_t generation)
{
    pthread_mutex_lock(&camera_lock);
    bool current = camera_wanted && generation == camera_generation;
    pthread_mutex_unlock(&camera_lock);
    return current;
}

static void camera_set_error(uint64_t generation, int status)
{
    pthread_mutex_lock(&camera_lock);
    if (camera_wanted && generation == camera_generation) {
        camera_status = status;
        camera_width = camera_height = camera_stride = 0;
        memset(camera_pixels, 0, sizeof(camera_pixels));
    }
    pthread_mutex_unlock(&camera_lock);
}

/* An old queued stop must not close a session opened by a newer start. */
static void camera_close_through(uint64_t generation)
{
    if (!camera_session || camera_session_generation > generation) { return; }
    for (id observer in camera_observers) {
        [[NSNotificationCenter defaultCenter] removeObserver:observer];
    }
    camera_observers = nil;
    [camera_session stopRunning];
    camera_session = nil;
    camera_sink = nil;
}

static void camera_watch_session(AVCaptureSession *session, uint64_t generation)
{
    camera_observers = [[NSMutableArray alloc] init];
    for (NSNotificationName name in @[AVCaptureSessionRuntimeErrorNotification,
                                      AVCaptureSessionDidStopRunningNotification]) {
        id observer = [[NSNotificationCenter defaultCenter]
            addObserverForName:name object:session queue:nil usingBlock:^(NSNotification *notification) {
                (void)notification;
                dispatch_async(camera_queue, ^{
                    if (camera_current(generation) && camera_session &&
                        camera_session_generation == generation) {
                        camera_set_error(generation, W800_HOST_CAMERA_ERROR);
                        camera_close_through(generation);
                    }
                });
            }];
        [camera_observers addObject:observer];
    }
}

/* Called only on camera_queue; AVFoundation work never blocks QEMU's BQL. */
static void camera_open(uint64_t generation)
{
    @autoreleasepool {
        if (!camera_current(generation)) { return; }
        /* A delayed stop may still be queued behind this newer open. */
        camera_close_through(generation);
        AVCaptureDevice *device = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
        if (!device) {
            camera_set_error(generation, W800_HOST_CAMERA_UNAVAILABLE);
            return;
        }
        NSError *error = nil;
        AVCaptureDeviceInput *input = [AVCaptureDeviceInput deviceInputWithDevice:device error:&error];
        AVCaptureSession *session = [[AVCaptureSession alloc] init];
        AVCaptureVideoDataOutput *output = [[AVCaptureVideoDataOutput alloc] init];
        if (!input || ![session canAddInput:input] ||
            ![session canSetSessionPreset:AVCaptureSessionPreset640x480]) {
            camera_set_error(generation, W800_HOST_CAMERA_ERROR);
            return;
        }
        [session beginConfiguration];
        session.sessionPreset = AVCaptureSessionPreset640x480;
        [session addInput:input];
        output.alwaysDiscardsLateVideoFrames = YES;
        if (![session canAddOutput:output]) {
            [session commitConfiguration];
            camera_set_error(generation, W800_HOST_CAMERA_ERROR);
            return;
        }
        W800CaptureSink *sink = [[W800CaptureSink alloc] init];
        sink.generation = generation;
        [output setSampleBufferDelegate:sink queue:camera_queue];
        [session addOutput:output];
        /* Pin delivery to a format the sink converts. Devices such as the
         * built-in MacBook camera emit packed 4:2:2 (2vuy); requesting BGRA
         * lets AVFoundation convert every native format to the one path the
         * sink handles, instead of silently dropping unsupported frames. */
        if ([output.availableVideoCVPixelFormatTypes
                containsObject:@(kCVPixelFormatType_32BGRA)]) {
            output.videoSettings = @{(id)kCVPixelBufferPixelFormatTypeKey:
                                         @(kCVPixelFormatType_32BGRA)};
        }
        [session commitConfiguration];
        if (!camera_current(generation)) { return; }
        camera_session = session;
        camera_session_generation = generation;
        camera_sink = sink;
        camera_watch_session(session, generation);
        [session startRunning];
        if (![session isRunning]) {
            camera_set_error(generation, W800_HOST_CAMERA_ERROR);
            camera_close_through(generation);
        }
        if (!camera_current(generation)) {
            camera_close_through(generation);
        }
    }
}

void w800_host_camera_start(void)
{
    camera_initialize_queue();
    pthread_mutex_lock(&camera_lock);
    if (camera_wanted) {
        pthread_mutex_unlock(&camera_lock);
        return;
    }
    camera_wanted = true;
    camera_status = W800_HOST_CAMERA_PENDING;
    uint64_t generation = ++camera_generation;
    pthread_mutex_unlock(&camera_lock);
    dispatch_async(camera_queue, ^{
        @autoreleasepool {
            if (!camera_current(generation)) { return; }
            AVAuthorizationStatus permission = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
            if (permission == AVAuthorizationStatusAuthorized) {
                camera_open(generation);
            } else if (permission == AVAuthorizationStatusNotDetermined) {
                [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL granted) {
                    dispatch_async(camera_queue, ^{
                        if (granted) { camera_open(generation); }
                        else { camera_set_error(generation, W800_HOST_CAMERA_DENIED); }
                    });
                }];
            } else {
                camera_set_error(generation, W800_HOST_CAMERA_DENIED);
            }
        }
    });
}

void w800_host_camera_stop(void)
{
    camera_initialize_queue();
    pthread_mutex_lock(&camera_lock);
    camera_wanted = false;
    uint64_t generation = camera_generation++;
    camera_status = W800_HOST_CAMERA_STOPPED;
    camera_width = camera_height = camera_stride = 0;
    memset(camera_pixels, 0, sizeof(camera_pixels));
    pthread_mutex_unlock(&camera_lock);
    dispatch_async(camera_queue, ^{
        @autoreleasepool { camera_close_through(generation); }
    });
}

int w800_host_camera_status(void)
{
    pthread_mutex_lock(&camera_lock);
    int status = camera_status;
    pthread_mutex_unlock(&camera_lock);
    return status;
}

int w800_host_camera_copy(uint8_t *destination, size_t capacity,
                          uint32_t *width, uint32_t *height, uint32_t *stride,
                          uint64_t *sequence)
{
    pthread_mutex_lock(&camera_lock);
    int result = 0;
    size_t bytes = camera_stride * camera_height;
    if (camera_wanted && camera_status == W800_HOST_CAMERA_RUNNING && bytes) {
        if (!destination || capacity < bytes || !width || !height || !stride || !sequence) {
            result = -1;
        } else {
            memcpy(destination, camera_pixels, bytes);
            *width = camera_width;
            *height = camera_height;
            *stride = camera_stride;
            *sequence = camera_sequence;
            result = 1;
        }
    }
    pthread_mutex_unlock(&camera_lock);
    return result;
}
