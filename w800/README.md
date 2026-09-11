# Sony Ericsson W800i — original firmware in QEMU

The original ARM/Thumb firmware runs on a custom QEMU DB2010 machine. The native
phone window now shows the **original Start-up menu**. **Start phone** opens the
original 12-icon phone menu; **Music only** opens the original Walkman browser.
The original orange Default theme is applied by the firmware's ThemeBook.
The photographed phone buttons send physical
keypad events and the screen contains only actual firmware-rendered LCD pixels.

This is experimental component startup. The original filesystem mounts, then a
debugger adapter starts the original application services and runs their scheduler. An
emulator hook redirects only the Start phone UI action to the original MainMenu
launcher, so it does not enter the Insert SIM dialogue. MIDI previews, the tested
MP3 ringtone and AAC video soundtrack reach QEMU host audio; radio, full normal
boot and individual applications remain
incomplete. Original SecLD/KGEN and
SIM results are unchanged; this is not a fix for their normal startup path.

## Run

Double-click `../Launch W800i.command`, or run from the project root:

```sh
.venv/bin/python -m w800 ui
```

The window starts the UI automatically. Start/Stop UI controls its owned QEMU
process; Restart UI creates a fresh session and returns to the startup selector.
After choosing Start phone, the original standby screen opens automatically;
its Menu soft key opens the phone menu, and Back returns to standby.
Music only opens Walkman, with Back returning to the selector. Pause/Resume
preserves the current screen. The photograph's buttons and keyboard bindings
send actual QEMU input to the guest keypad matrix:
arrows/Enter, F1/F2 soft keys, Esc back, Backspace clear, W or F3 Walkman,
Page Up/Down phone volume, and number/*/# keys. Text entry uses the original number-key editor: hold `*` to
switch T9/multi-tap, choose English under Writing language to use T9, and
Backspace sends the phone's Clear key. Host letters
are not substituted into guest text fields. A firmware fault or unresolved call stops the session with a
message; Restart UI recovers by discarding that private VM. Closing during
startup or a call waits for worker cleanup without blocking the window.

**Enlarge screen…** opens a separate view of the same live firmware display.
Choose a 1×–6× size or resize the window freely. The original 176×220 screen
always keeps its 4:5 aspect ratio, with margins when the window has a different
shape. Keyboard controls work in either phone window; the photograph's buttons
remain available. Closing the enlarged view leaves the emulator running.

The **Virtual phone** tab beside **Running phone files** accepts a custom
network name (up to 32 printable characters supported by the phone font).
**Apply and show standby** displays it through the original operator-name
widget; the radio remains offline. The standby **Menu** soft key opens the
regular phone menu. **Clear** restores the original operator label and keeps
standby after Start phone has been chosen. This is currently
a local name setting, not a cellular registration or SMS transport service.

**Game boost** can be toggled during a running session, including while paused.
It removes display/key diagnostic pauses and samples the real display while
QEMU keeps executing, using 50-ms scheduler windows and 16-ms display polling.
It preserves phone timers, audio speed, keypad contact timing and saved files.
The switch stays selected across Restart UI within the same app window.
QEMU already executes without a CPU-frequency cap, so this is an overhead
optimization rather than a simulated MHz multiplier. A QuadraPop run improved
from approximately 16 to 21.5 visible screen changes per second; results depend
on the game and host load. See [Game boost measurements](reports/game-boost.md).

The GUI samples video every 40 ms, matching the bundled movie's 25 fps cadence
more closely than its former 100 ms loop (about 22 versus 9 displayed frames
per second in the measured run). The CoreAudio backend retains queued PCM
across brief debugger stops instead of repeatedly stopping the host device.
An identical MIDI workload improved from 24.96 to 11.83 seconds; its note
timestamps match the source MIDI and the actual host rate is 44,100 Hz.
Sustained pause and native Stop still halt playback. The scheduled 20 ms
debugger grace is not a hard bound on host HAL stop latency.
See [playback measurements](reports/midi-pacing-grace/README.md) and
[pause/resume verification](reports/coreaudio-debug-grace.md).

The older normal-boot debugger window remains available with
`.venv/bin/python -m w800 gui`. Its original power sequence still encounters the
device-validation failure before menus. Optional ROM/OTP inputs apply to that
window and the `boot` command, not the component UI session.

The panel shows only the real 176×220 QEMU display. The supplied photo was
straightened and cropped; its provenance is in `assets/README.md`. No imitation
menu or photographed screen is substituted for guest pixels. The LCD model now
accounts for both R61500 source-dot order and BGR conversion, fixing the earlier
red/blue swap. The original Themes browser lists four themes and selecting
Default completes its original activation callback. Runtime evidence is in
`reports/original-ui-orange-startup.png`, `original-ui-themes.png`,
`reports/original-ui-main-menu.png`, `original-ui-settings.png`,
`original-ui-walkman.png`, `original-ui-gui.png` and `original-ui-validation.json`.

The component adapter reads all 52 original descriptors and asks the original
manager to start the remaining services enabled for the original mode. All 51
mode-1 descriptors report started. Customization belongs to mode `0x80`, so it
is excluded according to the original descriptor mask.
Task creation is distinct from completed initialization. Connection Manager
starts before Handsfree, which acquires its interface during initialization.
An offline status endpoint answers only the original read-only status query
using native message allocation, sender correlation, and ownership transfer.
It returns the original unavailable status; radio activation/deactivation and
device validation remain separate. This resolves the observed late status wait.
See `reports/offline-status-integration.md` for the full-services idle test.

Phonebook startup now issues its original activation and loading requests.
An absent-SIM adapter sends the unavailable fixed-dialling reply already built
by the original phonebook task. Contacts' own error branch then opens phone
memory. Original multi-tap/T9, Clear, saving a name/number, and reopening it
have been exercised. File browsing, theme selection, and note editing also
have interaction tests. Original QuadraPop and PuzzleSlider install through the
native JAR installer and appear in Games. QuadraPop's menu, falling-note board,
movement and rotation have been exercised; PuzzleSlider's tile move and undo
also work, with its original file-access permission dialogs still present.
World Clock installs into Organizer → Applications during Start phone. Its
original globe, city selection, Exit and relaunch are verified. See
[World Clock validation](reports/worldclock-validation.md).
Further media/service dependencies remain under investigation.
The original WAP browser now initializes in hosted mode. The GUI can fetch
HTTP/HTTPS pages through the host and display them in the original browser.
See [host connectivity status](reports/host-connectivity.md) for supported
navigation and remaining limits.

The original **Greeting.mid** preview now produces sound through the emulated
DSP: the firmware parses MIDI events, writes them through its mailbox/DMA stream,
and the device model synthesizes PCM for QEMU audio. The normal phone window
uses CoreAudio. Full playback with normal 40 ms UI advances and LCD captures
matches the source's 40.080-second note schedule within the measured wall-time
interval of 40.030–40.225 seconds. All 3,212 guest MIDI events were consumed.
The GUI does not decode extracted ringtones or play host media files.

Synthesis uses compatible macOS General MIDI timbres; the original C55x
instrument bank and SysEx remain pending. This renderer adds an 11.79-second
release tail after the last source note. It rendered and submitted 2,489,877
frames at 48 kHz; CoreAudio delivered 2,287,574 frames at its verified 44.1 kHz
device rate, agreeing within one converted frame. The final partial host buffer
now drains, followed by one original completion callback and the firmware's own
stop, stream close, and release. Pause/resume and physical Back also pass.
All 1,379 short debugger stops cancelled their host-stop grace, leaving one HAL
start and one final stop. Three host callbacks required 1,066 silence frames
in total, including startup/end padding; this does not establish gap-free
output. See [current MIDI timing and sample accounting](reports/midi-audio-control-startup/README.md)
and the [earlier pause/resume verification](reports/midi-natural-coreaudio/README.md).
Guest volume, FIFO credits, pause, and flush are modeled. After the display has idled,
the original firmware consumes the first Back press to wake it; a second Back
stops playback and releases the synth, verified after 20 seconds of playback.

**Latin.mp3** now plays through the same original DSP stream, using MPEG
Layer III decoding and guest mixer gains. Its native Back/stop sequence and
natural completion both pass. A full capture contains all 754,560 stereo PCM16
frames at 44.1 kHz from 655 MPEG frames, including the decoder's final buffered
samples. MP3 support also covers the native Walkman library's 22.05 kHz MPEG-2
Layer III tracks, with the guest's 2× FIR conversion to 44.1 kHz. **Stop** and
**Musical Medley** play; Stop also completes naturally and the original Walkman
advances to Musical Medley. See [Stop EOF verification](reports/audio-stop-eof.md).
Physical pause/resume, volume, next/previous track,
and Back with background playback are verified. Original Normal, Mega Bass and
Voice equalizer presets now apply the guest's actual filter coefficients and
keep playing through changes. See [equalizer verification and limits](reports/audio-equalizer.md).
Other MP3 rate configurations remain pending. See [Walkman verification](reports/audio-walkman.md),
[MP3 evidence](reports/audio-mp3.md)
and the [complete Latin capture](reports/guest-latin-natural.wav).

Original Demo Tour video renders moving frames and sends raw AAC packets through
the emulated DSP stream. The DSP uses AudioConverter, guest FIR coefficients and
mixer gains to produce QEMU PCM. Native Stop, replay and Back return to the
firmware preview. A WAV capture also completed naturally, but a clean CoreAudio
run stalls around 19 seconds with missing AAC input and no EOF. The original
firmware drops late packets; its reader and host-output timing are still under
investigation. See [clean playback diagnostics](reports/video-realtime-clocks/result.json).
These results do not establish support for every codec or media file.
Rock Star now plays its original8 kHz mono AMR stream through the emulated
DSP decoder, both guest filters and QEMU PCM. Private WAV verification covers
pause/resume, Back/replay and natural completion after1309 actual AMR frames;
the native Videos playlist then advances to Demo Tour. The compatible raw
system decoder omits5 ms of leading silence; remaining samples match the
independent file decoder. Full CoreAudio movie smoothness remains under test.
See [AMR playback and sample accounting](reports/audio-rock-star.md).
Original Timer expiry also reaches guest MIDI synthesis. Audio Control starts
before MMI so its notification interface is available; the phone's native
Silent mode mutes Timer sound, and turning Silent mode off restores it.
Physical OK stops and releases the alert. Ringtone preview and settings retained
across native pages are covered in [sound settings evidence](reports/sound-settings.md).
See [audio evidence and limits](reports/audio-hardware-blocker.md),
[full natural-end capture](reports/guest-midi-natural.wav),
[natural-end verification](reports/audio-natural-verification.json),
and [CoreAudio trace](reports/audio-speakers.json).
Private probes default to silent output; explicitly select `--wav /path/capture.wav`
or `--audio-output coreaudio` with `python -m w800.tools.trace_audio_hardware`.

### Files in the running phone

The phone window includes a **Running phone files** panel. Choose **Refresh** to
list `/tpa/user/other`, enter an absolute phone folder, or double-click a folder.
Select a file to preview its text/hexadecimal bytes or export it to your computer.
**Upload file…** writes into the displayed folder and verifies the complete file
through the original firmware's read API. Replacing an existing file requires
the explicit Replace confirmation. Transfers are limited to 32 MiB per file;
previews show up to 64 KiB. The panel supports existing directories; recursive
folder upload/export and folder creation are not yet exposed.

Uploads use the **same running VM** as the phone screen. After a verified write,
the original content-change event refreshes an already-open File manager folder
without navigation or restart. To add a game/application, choose **Start phone**,
upload its JAR, select it in the panel, and choose **Install selected JAR on phone**.
Use the physical phone keys for its original destination, confirmation and
permission dialogs. The panel does not answer installer/runtime permissions.
Installed titles appear in the original Games or Applications folder.

File operations share the phone worker queue. Progress and LCD frames continue
during transfers; phone input is briefly disabled while a transfer owns the
session. Stop/close cancels the operation. Pausing disables file access until
Resume. **Stop UI and Restart UI discard all live uploads and installations**;
export anything you want to keep first. Host source files remain unchanged.
See [live filesystem and installation evidence](reports/live-filesystem.md).

### Separate filesystem inspection

Click **Browse firmware files…** in the normal-boot debugger window, double-click
`../Launch W800i Files.command`, or open the browser directly:

```sh
.venv/bin/python -m w800 files
```

Double-click folders to navigate and files to preview their text or hexadecimal
bytes. **Export file…** saves the complete selected file to your computer. The
path field accepts absolute phone directories; Root and Up navigate back.
For example, `/tpa/preset/system/menu/menu.ml` contains the original menu XML.
Previews are limited to 128 KiB; exports support files up to 64 MiB.

The browser boots a separate disposable QEMU session to the original mounted-FS
checkpoint and uses the firmware's own directory and file functions. It shows
that inspection session, not unsaved changes in the phone window. Browsing is
read-only, and boot-time storage changes are discarded on close. It currently
supports the prepared W800 R1L002 MAIN only and does not depend on a mask-ROM
input. This separate inspection feature does not use the UI component session.
Runtime validation and a screenshot are in `reports/filesystem-browser-tests.txt`
and `reports/filesystem-browser.png`.
Internal `/ifs` files use the firmware's underlying full-path API; all 404
mounted files have been read successfully in the content audit. See
`reports/restart-fix-and-filesystem-audit.md` for this correction and the
verified original power-key restart sequence.

## Reproduce

The local build needs a C toolchain, GLib, pkg-config and the workspace Python
environment. It does not replace system QEMU. Source/download references are in
`reference/SOURCES.md`.

```sh
.venv/bin/python -m pip install -r w800/requirements-build.txt
.venv/bin/python -m pip install -r w800/requirements.txt
sh w800/build_qemu.sh
.venv/bin/python -m w800.firmware \
  w800/firmware/W800_R1L002_MAIN_EU_EMEA_RED49.bin \
  w800/firmware/W800_R1L002_FS_EMEA1_RED49.bin
.venv/bin/python -m w800.gdfs
.venv/bin/python -m w800 boot --exploratory --seconds 6 --mmio-limit 200000
.venv/bin/python -m w800.tools.trace_messages --platform
.venv/bin/python -m unittest discover -s w800/tests -v
```

Normal runs use `firmware/prepared/flash-gdfs.bin`. The converter preserves the
original MAIN/FS bytes and all 982 GDFS backup payloads, adds the original
driver's storage metadata in the otherwise erased GDFS region, and produces a
separate manifest. Downloaded inputs and `flash.bin` remain untouched. NAND
starts erased; NOR/NAND mutations survive QEMU `system_reset` within that
process. Stopping the process discards them; host input files are never updated.
Customization ZIP files are retained but not installed.

The experimental `python -m w800.tools.try_customization` command installs the
20 supplied `/tpa/preset/custom` files into a temporary VM using the original
file APIs and verifies their readback before resuming startup. Its current run
still reaches the radio-validation failure; it does not enable menus. The
experiment sets/restores CPU arguments for file operations and discards all
guest storage changes on exit. It does not change the normal GUI image.

Execution currently starts at MAIN's vector 0x44020000. External EROM files
located in the TopSony archive have been audited but are not integrated into
startup. This does not reproduce the physical boot-ROM chain. Hashes
identify the downloaded bytes; RSA signatures are not authenticated by the
parser. No guest instruction is patched to bypass a check.

## Implemented paths and evidence

| Component | Implementation and validation |
| --- | --- |
| CPU, RAM, TCM, MMU | QEMU ARM926 TCG, guest instructions and MMU permissions; inferred DB2010 TCM/range-table hardware |
| NOR | Intel CFI identifier/query reads, 64-byte writes, 1 MiB read-while-write partitions, 255×128 KiB main blocks and four top 32 KiB parameter blocks; mixed erase boundaries and data retention across reset tested |
| NAND | 32 MiB, 512+16-byte pages; ID/read/program/erase/status and original 8A copy-back sequence |
| Filesystems | Original firmware mounts `/`, `/tpa/system/bg_images/cache`, `/ifs`, `/system`, `/smsdata` |
| GDFS | Seven active 128 KiB banks plus spare; all 982 unit payloads preserved; original complete 84-byte read matches backup |
| LCD | Original R61500_B PDI command/DMA stream, RGB565 GRAM, 176×220 QEMU console and IRQ41; full and partial screen redraws tested |
| Original UI | Original manager starts 51 application descriptors; physical startup choices open MainMenu and Walkman; orange theme, theme selection, file browsing, saved Notes and native GUI lifecycle tested |
| Keypad | 6×5 matrix plus encoded joystick/power column, IRQ60, QMP input; 18 original press/release events verified across nine controls |
| Power | Vincenne identity, event/status registers and physical key edges; calibrated 3.9 V/type-1 battery inputs; original IRQ25 handler and event subscriber receive both power-key edges |
| Card detection | Empty Memory Stick socket pullup; original status replies unblock filesystem startup |
| RTC | Virtual-time oscillator, fraction/calendar, validity flags, battery-domain retention and daily/absolute alarm IRQ43; nine device checks plus original Alarm popup, MIDI output and dismissal verified |
| DSP and MIDI audio | Original uploads, mixed-width mailbox, bidirectional DMA, IRQ9/77, uploaded version metadata, mixer state, and guest MIDI FIFO → compatible GM synthesis → QEMU PCM/CoreAudio; playback, real credits/flush, natural EOF/tail/drain, and native stop/release verified; SysEx and original C55x bank remain pending |

The low-voltage warning was traced to automatic ADC registers returning zero.
The replacement inputs derive from supplied GDFS voltage and thermistor
calibration. The original HAL returns 3899 mV and recognizes a fitted type-1
battery. An explicitly isolated original interpolation test returns 25°C;
normal periodic temperature sampling is interrupted by later startup shutdown.
The earlier manual ADC command `0x6a` now returns the same physical battery
input. Original cold-start arithmetic reports 3960 mV; the later calibrated
HAL reports 3899 mV. Both execute their original voltage checks.

The offline radio model remains standalone. The current radio activation
failure is an explicit ARM service reply after device validation. The separate
DSP version timeout is nonfatal, and implementing that reply alone would not
establish menu startup. See `reports/offline-service-boundary.md`.

Research and reproducible evidence are under `reports/`: `lcd-research.md`,
`keypad-research.md`, `gdfs-layout.md`, `power-research.md`,
`power-calibration.md`, `dsp-research.md`, and their JSON captures/tests.
The root architecture/results notes track changing startup blockers.
Platform printf capture records raw format pointers and arguments plus a
best-effort host decoding; these are debugger observations, not a UART model.

A direct development run is available with:

```sh
w800/build/qemu-system-arm -M w800,exploratory=on,mmio-limit=1000000 \
  -bios w800/firmware/prepared/flash-gdfs.bin \
  -display none -serial none -monitor stdio -S
```

## Internal ROM and optional hardware inputs

Normal boot through all original startup services remains unresolved. The default development
profile still has provisional flash protection metadata and SoC identification.
Correcting these exposes an original call to `0xffff12a3` before MMI creation.
The emulator currently has no code backing that address.

The original startup registers a separate 16 KiB region at `0xffff0000`.
MAIN/FS contain no directly addressed segment there, and their early copy table
does not populate it. Container accounting found no separate hidden file member.
This supports a dependency on code already resident in the chip, but does not
exclude compressed or unlabeled copies within the update payloads. No verified
embedded image has been located. See `reports/maskrom-container-audit.md` and
`reports/maskrom-sources.md` for evidence and search limits.

The subsequent TopSony source search recovered W800/DB2010 external EROM
packages in `firmware/topsony/`. Their code still references separate high-ROM
routines; their startup copy tables initialize small RAM areas. These are
preserved research inputs, not integrated into the current MAIN-entry boot.
See `reports/erom-topsony-audit.md`. The TopSony W800 GDFS backup is identical
to the original backup already in use.

QEMU and the photo panel can now load a separately identified raw ROM image:

```sh
.venv/bin/python -m w800 gui --mask-rom /path/to/db2010-rom.bin --chip-id 0x8040
```

The loader accepts 16–64 KiB in 4 KiB increments, maps exactly the supplied
bytes read-only at `0xffff0000`, and records their SHA-256 in backend snapshots.
These are supported input bounds, not an assertion of physical ROM capacity.
The image may be extracted from a verified embedded copy or supplied separately;
the loader does not discover, decompress or authenticate it. Chip IDs `0x8000`
and `0x8040` select the corresponding DB2010 profile. The selected chip ID and
eight-byte factory/user protection-field geometry are active with `--mask-rom`.

An optional `--nor-otp /path/to/nor-otp.bin` accepts exactly 18 bytes: the
little-endian 16-bit protection words `0x80` through `0x88` (lock word, eight
factory bytes, eight user bytes). It requires `--mask-rom`. Without this input,
those words read as erased. This models only the first protection field;
programming and any further fields remain unimplemented. ROM and OTP inputs
are staged privately, hashed, and never written back to their source files.
Providing these files is not evidence that they match the firmware/GDFS or
that original menu startup succeeds.

Unbacked accesses in the high ROM window now pause with
`W800_MASK_ROM_UNAVAILABLE`, including in exploratory mode, instead of silently
continuing through a zero-filled fallback. The historical
`reports/rom-api-experiment.patch` is retained as evidence; do not apply it on
top of the current source. No ROM API success stub or device identity was
fabricated. All 63 current tests pass, including exact ROM/OTP readback,
read-only/reset behavior, input validation and an isolated three-instruction
ROM execution diagnostic. That diagnostic is not an original-firmware boot.

An isolated attempt to enable NOR protection metadata without ROM backing
reached an earlier ETX unknown-chip assertion. The default provisional profile
was restored; physical protection-field metadata remains a documented gap.
The final cold-boot trace reaches the logo and the existing radio activation
failure. A warm reset preserves guest-written flash but has not reached MMI.
See `reports/flash-profile-startup-regression.md` and
`reports/reset-boot-audit.json` for the separate observations.

Advanced LCD
modes/general PDI programs, alarm IRQs, full DSP/radio/audio, OTP identity and the
missing EROM chain also remain incomplete. Existing Creative ZEN/X-Fi emulator
code is separate. QEMU and the machine source use their stated GPL licenses;
firmware and photography retain their respective ownership.

## Host UART terminal

Open **View → UART terminal** (or the UART terminal tab on the right). The
panel connects to UART0 automatically when the phone starts. Type `AT` or
`AT+CGMI` and press Enter or Send; responses come from the original firmware.
Pause disables sending, and stopping/restarting closes the old connection.
Clear output clears the display. Disconnect frees UART0 for an external terminal.

For an external terminal, disconnect the GUI panel first, then list endpoints:

```
.venv/bin/python -m w800.uart_terminal --list
.venv/bin/python -m w800.uart_terminal --uart 0
```

When multiple phones are running, pass `--session` with the directory printed
by `--list`. UART0 is the default native accessory/AT port: type `AT` and
press Enter for the firmware’s `OK` response. Ctrl-] disconnects. UART1 and
UART4 have raw sockets but are not opened by the current firmware startup;
UART3 remains assigned to Bluetooth. This is the original AT command service,
not a general-purpose shell. Restart the phone to load the updated hardware.

For firmware messages captured at debugger logger checkpoints, use
`tail -f /printed/session/path/firmware-debug.log`. Those messages are separate
from actual UART bytes. Temporary sockets/logs disappear when the VM closes.

See [host connectivity status](reports/host-connectivity.md) for tests and
limitations. HTTP GET documents use the host content bridge; cellular data
service remains offline.

### Hosted web pages

Internet Services opens a local start page in the original browser, without
contacting the old operator portal. Choose **More → Enter address → New address**, enter
a URL with the phone keypad, then choose **Go to**. Physical multi-tap entry of
`example.com` was verified against a real HTTP response. The optional
**Virtual phone → Open in phone browser** control also accepts a URL. The host fetches the page asynchronously and the
original firmware renders it from the running phone filesystem. This keeps the
original 176×220 browser UI. Modern JavaScript applications are outside the
capabilities of this handset browser. Native relative document links are tested.
The original account services create a Host Internet profile automatically after
Start phone, so browsing does not ask you to create one.

This supplies page content through the host; it does not simulate a registered
cellular data connection. Files appear as `web-*.html` in the phone's Other
folder, within the disposable emulator session.
