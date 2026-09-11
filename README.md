# W800EmuToShare — Sony Ericsson W800i Emulator

Runs the **original Sony Ericsson W800i firmware (R1L002)** unmodified on a
custom **QEMU machine model of the DB2010 platform** (ARM926EJ-S, ARM/Thumb).
This is not a UI recreation: the phone window shows only firmware-rendered
LCD pixels, and input goes through the emulated keypad matrix into the
original firmware's interrupt handlers.

**Host platform: macOS.** The audio path uses CoreAudio, the camera bridge
uses AVFoundation, and the GUI is PySide6 (Qt). QEMU is built locally into
`w800/build/`; nothing is installed into the system.

---

## What it does

- Boots the original W800i firmware filesystem and starts the original
  application services under a debugger adapter (component-startup mode).
- Presents the original **Start-up menu**: **Start phone** opens the
  original 12-icon phone menu and standby screen; **Music only** opens the
  original Walkman browser. The original orange Default theme is applied by
  the firmware's own ThemeBook.
- Renders the real 176×220 R61500 LCD output (RGB565 GRAM, DMA-driven,
  partial redraws) inside a photographed phone faceplate.
- Sends physical keypad events into the emulated 6×5 matrix (IRQ60):
  arrows/Enter, F1/F2 soft keys, Esc = Back, Backspace = Clear, W/F3 =
  Walkman, Page Up/Down = volume, digits/`*`/`#`, F4 = camera, plus a power
  key. Original multi-tap and T9 text entry work.
- Plays original media through the emulated DSP: **MIDI** (General MIDI
  synthesis → PCM → CoreAudio), **MP3** (MPEG Layer III incl. 22.05 kHz
  MPEG-2 with the guest's 2× FIR conversion), **AAC** (via AudioConverter),
  and **8 kHz AMR**. Equalizer presets (Normal, Mega Bass, Voice) apply the
  guest's real filter coefficients. Pause/resume, volume, track change and
  background playback are verified.
- **Running phone files** panel: browse `/tpa/user/...`, preview text/hex,
  export to host, upload files (≤32 MiB), and **install JAR applications**
  through the original installer (QuadraPop, PuzzleSlider, World Clock
  verified).
- **Separate filesystem browser** (`python -m w800 files`): read-only
  inspection of the mounted firmware filesystem in a disposable VM.
- **UART terminal**: connects to UART0 for the original AT command service
  (`AT`, `AT+CGMI`, …). External socket access via `w800.uart_terminal`.
- **Hosted web**: the original WAP/browser renders pages fetched through a
  host bridge (HTTP/HTTPS), stored as `web-*.html` in the guest filesystem.
- **Virtual phone** tab: custom operator/network name on the standby screen
  (local label only — radio stays offline).
- **Game boost** toggle: removes diagnostic pauses and samples the live
  display at 16 ms for smoother gameplay (≈16 → 21.5 visible fps measured in
  QuadraPop).
- Optional inputs: `--mask-rom` (16–64 KiB image mapped at `0xffff0000`) and
  `--nor-otp` (18-byte protection words) for the research `gui`/`boot` paths.

### Emulated hardware

| Component | Model |
| --- | --- |
| CPU/MMU | QEMU ARM926 TCG, TCM, DB2010 range table |
| NOR flash | Intel CFI pflash, 32 MiB, 64-byte writes, RWW partitions, 255×128 KiB + 4×32 KiB blocks |
| NAND | 32 MiB, 512+16-byte pages, copy-back |
| LCD | R61500_B PDI, RGB565 GRAM, IRQ41 |
| Keypad | 6×5 matrix + joystick/power column, IRQ60 |
| Power | Vincenne PMU, calibrated 3.9 V battery, IRQ25 |
| RTC | Oscillator/calendar, alarm IRQ43 |
| DSP | Mailbox/DMA, IRQ9/77, MIDI/MP3/AAC/AMR decode → PCM |
| UART | UART0 AT service; UART1/4 raw sockets; UART3 Bluetooth |
| Other | I²C, SIF, camera bridge (host camera via AVFoundation), card detect |

### What is *not* working yet

Cellular radio registration, full normal cold-boot to menus (the component
startup path is used instead), the original C55x instrument bank / MIDI
SysEx, OTP identity, and the missing EROM/mask-ROM boot chain. See
`w800/README.md` and `w800/reports/` for the detailed engineering log.

---

## Requirements

- **macOS** with Apple Silicon or Intel (built and tested on macOS 26 /
  Darwin 27)
- **Xcode Command Line Tools** (`xcode-select --install`) — C toolchain,
  `patch`, `shasum`
- **Homebrew** packages: `glib`, `pkg-config` — QEMU build dependencies
  ```sh
  brew install glib pkg-config
  ```
- **Python 3.12+** (developed on 3.14) — `python3` on PATH
- ~4 GB disk for the QEMU source tree + build; ~100 MB for firmware/prepared
  images

Python dependencies (pinned):

- Runtime (`w800/requirements.txt`): `PySide6==6.11.2`, `capstone==5.0.9`
- Build (`w800/requirements-build.txt`): `meson==1.12.0`, `ninja==1.13.2`

---

## Deployment

### 1. Create the virtual environment

From the project root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r w800/requirements-build.txt
.venv/bin/python -m pip install -r w800/requirements.txt
```

All launcher scripts and the QEMU build assume the interpreter at
`.venv/bin/python` relative to the project root.

### 2. Build the custom QEMU

```sh
sh w800/build_qemu.sh
```

The script:

1. Downloads `qemu-10.2.2.tar.xz` from download.qemu.org into
   `w800/reference/` and verifies its pinned SHA-256
   (`784b296f…21e176`).
2. Extracts it, applies the W800 patches (`cfi01-query-config`,
   `coreaudio-debug-grace`, `coreaudio-input`), and copies the W800 machine
   sources (`qemu/w800.c` + `w800-*.inc` device models) into the tree.
3. Configures a minimal `arm-softmmu` build (no Cocoa/SDL/GTK/VNC/slirp —
    the window is rendered by the PySide6 host, not QEMU).
4. Builds `w800/build/qemu-system-arm` with ninja and packages the
   standalone `w800/build/W800 QEMU.app` bundle.

Re-running is incremental; delete `w800/build/build.ninja` to force
reconfigure.

### 3. Prepare the flash image

Two steps convert the downloaded firmware files into the flat NOR image the
emulator boots (`-bios flash-gdfs.bin`):

```sh
# Step 1: MAIN + FS BABE containers -> flat 32 MiB NOR image
.venv/bin/python -m w800.firmware \
  w800/firmware/W800_R1L002_MAIN_EU_EMEA_RED49.bin \
  w800/firmware/W800_R1L002_FS_EMEA1_RED49.bin

# Step 2: inject GDFS backup as formatted NOR banks
.venv/bin/python -m w800.gdfs
```

See *Flash format conversion* below for what these do. Inputs in
`w800/firmware/` are never modified; outputs land in
`w800/firmware/prepared/`.

### 4. Run

```sh
.venv/bin/python -m w800 ui
```

or double-click **`Launch W800i.command`**. Other entry points:

| Command | Purpose |
| --- | --- |
| `python -m w800 ui` | Main phone window (component startup; the app) |
| `python -m w800 files` | Read-only firmware filesystem browser |
| `python -m w800 gui` | Legacy normal-boot debugger window (accepts `--mask-rom`, `--nor-otp`, `--chip-id`) |
| `python -m w800 boot` | Headless boot run; writes `reports/{strict,exploratory}-boot.json` + trace log |

---

## Firmware files

All inputs live in `w800/firmware/`. Sources and hashes are documented in
`w800/reference/SOURCES.md` (firmware.center, CID 49 RED, R1L002).

| File | Required | Role |
| --- | --- | --- |
| `W800_R1L002_MAIN_EU_EMEA_RED49.bin` (~18.5 MB) | yes | BABE v3/v4 main firmware image |
| `W800_R1L002_FS_EMEA1_RED49.bin` (~10.6 MB) | yes | BABE filesystem image (`/tpa`, `/ifs` content) |
| `W800_GDFS.bin` (~64 KB) | yes | GDFS unit backup → reconstructed into NOR banks |
| `W800i_CDA102425_38_EMEA_1.zip` | retained, not installed | customization pack (experimental `tools/try_customization`) |
| `firmware/topsony/` EROM packages | no | preserved research inputs, not in the boot path |
| external mask ROM (16–64 KiB) | no | `--mask-rom`, mapped at `0xffff0000` |
| external NOR OTP (18 bytes) | no | `--nor-otp` lock/factory/user words, needs `--mask-rom` |

## Flash format conversion

**Step 1 — `w800/firmware.py` (BABE → flat NOR).** Parses addressed BABE
v3/v4 images (header `0xBABE`, DB2010 platform `0x00100000`): extracts each
`(address, size, payload)` block, validates bounds/overlaps, and writes the
payloads into an erased (`0xFF`) 32 MiB image based at `0x44000000`.
Output: `prepared/flash.bin` + `manifest.json` (SHA-256 of inputs and
output, segment map, entry `0x44020000`). RSA signatures identify the
images but are **not** authenticated; no guest instruction is patched.

**Step 2 — `w800/gdfs.py` (GDFS backup → NOR banks).** Parses the GDFS
backup's `(bank, unit, length, payload)` records (982 units), then encodes
them in the **inferred v0 layout** the original GDFS driver expects: seven
active 128 KiB banks plus one erased spare, payloads packed from the start
of each bank with 16-byte descriptors allocated top-down, at region base
`0x45f00000`. Refuses to overwrite a non-erased region. Output:
`prepared/flash-gdfs.bin` + `flash-gdfs.manifest.json` — this is the image
every GUI/CLI session loads.

At runtime the emulator presents this as NOR. Guest writes mutate only the
in-process image: NAND starts erased, NOR/NAND changes survive
`system_reset` inside the same VM, and everything is discarded when the
process exits. Host files are never written back.

---

## Repository layout

```
Launch W800i*.command    macOS double-click launchers (.venv/bin/python -m w800 ui)
w800/
  __main__.py            CLI entry: ui | files | gui | boot
  backend.py             QEMU process ownership, private Unix-socket QMP
  ui_gui.py              main phone window (PySide6)
  gui.py                 legacy normal-boot debugger window
  filesystem_gui.py      read-only firmware FS browser
  firmware.py            BABE parser + flash.bin builder
  gdfs.py                GDFS backup -> NOR bank encoder
  guest_ui.py, java_services.py, phonebook_services.py, ...  guest adapters
  host_web.py, browser_host.py, virtual_network.py           host connectivity
  uart_terminal.py, uart_gui.py                              AT/UART access
  qemu/                  w800.c machine + device .inc models, QEMU patches
  firmware/              input images + prepared/ outputs
  build_qemu.sh          pinned, checksummed QEMU 10.2.2 build
  build/                 local build tree; qemu-system-arm; W800 QEMU.app
  tools/                 research/probe utilities (audio traces, audits, …)
  tests/                 60+ unittest modules
  reports/               measurement + validation evidence
  reference/             QEMU tarball cache, SOURCES.md, r61500.pdf
```

## Testing

```sh
.venv/bin/python -m unittest discover -s w800/tests -v
```

Headless smoke boot:

```sh
.venv/bin/python -m w800 boot --exploratory --seconds 6 --mmio-limit 200000
```

## Licensing

QEMU and the W800 machine source are GPL-2.0(-or-later) as stated in their
files. Sony Ericsson firmware images and the phone photography remain the
property of their respective owners; they are inputs, not covered by this
project's license.
