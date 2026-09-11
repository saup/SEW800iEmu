/* Host BGRA to native YUV422EMP memory (Y0,U,Y1,V).
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Native decoder44a85e00..44a85e94 uses Y-16 and chroma-128, with
 * BT.601 limited-range conversion. No drawing or firmware state here.
 */
#ifndef W800_CAMERA_PIXELS_H
#define W800_CAMERA_PIXELS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

static inline uint8_t w800_camera_clip(int value)
{
    return value < 0 ? 0 : value > 255 ? 255 : value;
}

static inline bool w800_camera_bgra_yuyv(
    const uint8_t *source, size_t source_bytes,
    unsigned sw, unsigned sh, unsigned stride,
    uint8_t *destination, size_t destination_bytes,
    unsigned width, unsigned height)
{
    if (!source || !destination || !sw || !sh || sw > 640 || sh > 480 ||
        !width || !height || width > 640 || height > 480 || (width & 1) ||
        stride < sw * 4 || stride > 640 * 4 ||
        source_bytes < (size_t)stride * sh ||
        destination_bytes < (size_t)width * height * 2) { return false; }
    for (unsigned y = 0; y < height; y++) {
        unsigned sy = (2 * y + 1) * sh / (2 * height);
        for (unsigned x = 0; x < width; x += 2) {
            int u = 0, v = 0;
            unsigned offset = (y * width + x) * 2;
            for (unsigned n = 0; n < 2; n++) {
                unsigned sx = (2 * (x + n) + 1) * sw / (2 * width);
                const uint8_t *pixel = source + sy * stride + sx * 4;
                int b = pixel[0], g = pixel[1], r = pixel[2];
                destination[offset + n * 2] =
                    w800_camera_clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16);
                u += -38 * r - 74 * g + 112 * b;
                v += 112 * r - 94 * g - 18 * b;
            }
            destination[offset + 1] = w800_camera_clip(((u + 256) >> 9) + 128);
            destination[offset + 3] = w800_camera_clip(((v + 256) >> 9) + 128);
        }
    }
    return true;
}
#endif
