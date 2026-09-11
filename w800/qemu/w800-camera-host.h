/* Local host capture only; frames are consumed by emulated camera hardware. */
#ifndef W800_CAMERA_HOST_H
#define W800_CAMERA_HOST_H
#include <stddef.h>
#include <stdint.h>

enum W800HostCameraStatus {
    W800_HOST_CAMERA_STOPPED = 0,
    W800_HOST_CAMERA_PENDING = 1,
    W800_HOST_CAMERA_RUNNING = 2,
    W800_HOST_CAMERA_DENIED = 3,
    W800_HOST_CAMERA_UNAVAILABLE = 4,
    W800_HOST_CAMERA_ERROR = 5,
};

/* Host capture buffers are up/downscaled to fit 640×480 BGRA storage.
 * We accept both BGRA and common bi-planar YUV camera surfaces and convert them.
 */
#define W800_HOST_CAMERA_CAPACITY (640u * 480u * 4u)
#ifdef __APPLE__
void w800_host_camera_start(void);
void w800_host_camera_stop(void);
int w800_host_camera_status(void);
/* Returns 1 for a copied frame, 0 without a live frame (including failure),
 * -1 for insufficient capacity or a null output argument.
 * A repeated sequence is the same frame, never a new capture completion.
 */
int w800_host_camera_copy(uint8_t *destination, size_t capacity,
                          uint32_t *width, uint32_t *height, uint32_t *stride,
                          uint64_t *sequence);
#else
static inline void w800_host_camera_start(void) { }
static inline void w800_host_camera_stop(void) { }
static inline int w800_host_camera_status(void) { return W800_HOST_CAMERA_UNAVAILABLE; }
static inline int w800_host_camera_copy(uint8_t *destination, size_t capacity,
                                        uint32_t *width, uint32_t *height,
                                        uint32_t *stride, uint64_t *sequence)
{ return 0; }
#endif
#endif
