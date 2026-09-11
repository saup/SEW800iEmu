#!/bin/sh
# Build locally; no system QEMU binary is overwritten.
set -eu
W800_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$W800_ROOT")
export PATH="$PROJECT_ROOT/.venv/bin:$PATH"
QEMU_SOURCE="$W800_ROOT/reference/qemu-10.2.2"
QEMU_ARCHIVE="$W800_ROOT/reference/qemu-10.2.2.tar.xz"
W800_UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.4 Safari/605.1.15'
export GIT_HTTP_USER_AGENT="$W800_UA"
mkdir -p "$W800_ROOT/reference" "$W800_ROOT/build" "$W800_ROOT/reports"
if [ ! -f "$QEMU_ARCHIVE" ]; then
    curl -fL -A "$W800_UA" https://download.qemu.org/qemu-10.2.2.tar.xz -o "$QEMU_ARCHIVE.part"
    mv "$QEMU_ARCHIVE.part" "$QEMU_ARCHIVE"
fi
# Pin the archive actually used for this tested local build.
printf '%s  %s\n' '784b296ff29c1417aa72323abcb2d2ea9ab9771724f577dcd785c3b04f21e176' "$QEMU_ARCHIVE" | shasum -a 256 -c -
if [ ! -f "$QEMU_SOURCE/configure" ]; then
    tar -xf "$QEMU_ARCHIVE" -C "$W800_ROOT/reference"
fi
# This source file is generated from the pinned archive and our complete patch.
tar -xOf "$QEMU_ARCHIVE" qemu-10.2.2/hw/block/pflash_cfi01.c > "$QEMU_SOURCE/hw/block/pflash_cfi01.c"
patch -d "$QEMU_SOURCE" -p1 < "$W800_ROOT/qemu/cfi01-query-config.patch"
# Keep the opt-in host audio bridge reproducible from the pinned archive.
tar -xOf "$QEMU_ARCHIVE" qemu-10.2.2/audio/coreaudio.m > "$QEMU_SOURCE/audio/coreaudio.m"
tar -xOf "$QEMU_ARCHIVE" qemu-10.2.2/qapi/audio.json > "$QEMU_SOURCE/qapi/audio.json"
patch -d "$QEMU_SOURCE" -p1 < "$W800_ROOT/qemu/coreaudio-debug-grace.patch"
patch -d "$QEMU_SOURCE" -p1 < "$W800_ROOT/qemu/coreaudio-input.patch"
cp "$W800_ROOT/qemu/coreaudio-input.inc" "$QEMU_SOURCE/audio/coreaudio-input.inc"
cp "$W800_ROOT/qemu/coreaudio-input-buffer.h" "$QEMU_SOURCE/audio/coreaudio-input-buffer.h"
cp "$W800_ROOT/qemu/w800.c" "$QEMU_SOURCE/hw/arm/w800.c"
cp "$W800_ROOT/qemu/w800-timer-intc.inc" "$QEMU_SOURCE/hw/arm/w800-timer-intc.inc"
cp "$W800_ROOT/qemu/w800-nand.inc" "$QEMU_SOURCE/hw/arm/w800-nand.inc"
cp "$W800_ROOT/qemu/w800-i2c.inc" "$QEMU_SOURCE/hw/arm/w800-i2c.inc"
cp "$W800_ROOT/qemu/w800-power.inc" "$QEMU_SOURCE/hw/arm/w800-power.inc"
cp "$W800_ROOT/qemu/w800-keypad.inc" "$QEMU_SOURCE/hw/arm/w800-keypad.inc"
cp "$W800_ROOT/qemu/w800-lcd.inc" "$QEMU_SOURCE/hw/arm/w800-lcd.inc"
cp "$W800_ROOT/qemu/w800-camera.inc" "$QEMU_SOURCE/hw/arm/w800-camera.inc"
cp "$W800_ROOT/qemu/w800-camera-pixels.h" "$QEMU_SOURCE/hw/arm/w800-camera-pixels.h"
cp "$W800_ROOT/qemu/w800-camera-host.h" "$QEMU_SOURCE/hw/arm/w800-camera-host.h"
cp "$W800_ROOT/qemu/w800-camera-host.m" "$QEMU_SOURCE/hw/arm/w800-camera-host.m"
cp "$W800_ROOT/qemu/w800-rtc.inc" "$QEMU_SOURCE/hw/arm/w800-rtc.inc"
cp "$W800_ROOT/qemu/w800-dsp.inc" "$QEMU_SOURCE/hw/arm/w800-dsp.inc"
cp "$W800_ROOT/qemu/w800-dsp-synth.inc" "$QEMU_SOURCE/hw/arm/w800-dsp-synth.inc"
cp "$W800_ROOT/qemu/w800-dsp-aac.inc" "$QEMU_SOURCE/hw/arm/w800-dsp-aac.inc"
cp "$W800_ROOT/qemu/w800-dsp-resample.inc" "$QEMU_SOURCE/hw/arm/w800-dsp-resample.inc"
cp "$W800_ROOT/qemu/w800-dsp-equalizer.inc" "$QEMU_SOURCE/hw/arm/w800-dsp-equalizer.inc"
cp "$W800_ROOT/qemu/w800-uart.inc" "$QEMU_SOURCE/hw/arm/w800-uart.inc"
cp "$W800_ROOT/qemu/w800-sif.inc" "$QEMU_SOURCE/hw/arm/w800-sif.inc"
cp "$W800_ROOT/qemu/w800-bluetooth.inc" "$QEMU_SOURCE/hw/arm/w800-bluetooth.inc"
if ! grep -Fq "arm_ss.add(files('w800.c'))" "$QEMU_SOURCE/hw/arm/meson.build"; then
    printf "\narm_ss.add(files('w800.c'))\n" >> "$QEMU_SOURCE/hw/arm/meson.build"
fi
if ! grep -Fq 'w800_camera_host = static_library' "$QEMU_SOURCE/hw/arm/meson.build"; then
    cat >> "$QEMU_SOURCE/hw/arm/meson.build" <<'W800_CAMERA_MESON'

if host_os == 'darwin'
  w800_camera_frameworks = dependency('appleframeworks', modules: ['AVFoundation', 'CoreMedia', 'CoreVideo', 'Foundation'])
  w800_camera_host = static_library('w800-camera-host', 'w800-camera-host.m',
    objc_args: ['-fobjc-arc'], dependencies: w800_camera_frameworks)
  arm_ss.add(declare_dependency(link_with: w800_camera_host,
    dependencies: w800_camera_frameworks))
endif
W800_CAMERA_MESON
fi
if ! grep -Fq 'CONFIG_PFLASH_CFI01=y' "$QEMU_SOURCE/configs/devices/arm-softmmu/default.mak"; then
    printf '\nCONFIG_PFLASH_CFI01=y\n' >> "$QEMU_SOURCE/configs/devices/arm-softmmu/default.mak"
fi
cd "$W800_ROOT/build"
if [ ! -f build.ninja ]; then
    sh "$QEMU_SOURCE/configure" --target-list=arm-softmmu \
        --disable-docs --disable-werror --disable-guest-agent --disable-tools \
        --disable-user --disable-cocoa --disable-sdl --disable-gtk \
        --disable-opengl --disable-vnc --disable-slirp --disable-capstone \
        --disable-plugins --without-default-devices \
        --python="$PROJECT_ROOT/.venv/bin/python" \
        --ninja="$PROJECT_ROOT/.venv/bin/ninja" > "$W800_ROOT/reports/configure.log" 2>&1
fi
"$PROJECT_ROOT/.venv/bin/ninja" -j 8 qemu-system-arm
cd "$PROJECT_ROOT"
"$PROJECT_ROOT/.venv/bin/python" -m w800.tools.package_qemu_app
