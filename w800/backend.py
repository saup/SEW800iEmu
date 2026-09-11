"""QEMU process ownership and private Unix-socket QMP control."""
from pathlib import Path
import hashlib
import json
import re
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent

KEY_QCODES = {
    **{key: key for key in '0123456789'},
    '*': 'asterisk', '#': 'h',
    'soft_left': 'f1', 'soft_right': 'f2', 'walkman': 'f3',
    'back': 'esc', 'clear': 'backspace', 'select': 'ret',
    'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
    'volume_up': 'volumeup', 'volume_down': 'volumedown',
    # Dedicated side camera key: CAMSNAP (row5,col3) in the DB2010 matrix.
    'camera': 'f4',
    'power': 'power',
}

class QemuBackend:
    def __init__(self, trace=False, exploratory=False, debug=False, mmio_limit=10000, bus_test=False,
                 flash_path=None, mask_rom_path=None, chip_id=0x8040, nor_otp_path=None,
                 audio_output='none', audio_capture_path=None,
                 audio_debug_stop_grace_us=20000, host_camera=False, uart=False,
                 erom_path=None, flash=False, usb=False):
        if not isinstance(host_camera, bool):
            raise ValueError('Host camera must be a boolean')
        if not isinstance(audio_debug_stop_grace_us, int) or not 0 <= audio_debug_stop_grace_us <= 1000000:
            raise ValueError('Audio debugger stop grace must be 0–1000000 microseconds')
        if not 1000 <= mmio_limit <= 1000000:
            raise ValueError('mmio_limit must be between 1000 and 1000000')
        if not isinstance(chip_id, int) or chip_id not in (0x8000, 0x8040):
            raise ValueError('chip_id must be 0x8000 or 0x8040')
        if nor_otp_path is not None and mask_rom_path is None:
            raise ValueError('NOR OTP input requires a mask ROM image')
        if flash and erom_path is None:
            raise ValueError('Flash-mode key hold requires an EROM image')
        mask_rom_path = Path(mask_rom_path).expanduser().resolve() if mask_rom_path is not None else None
        nor_otp_path = Path(nor_otp_path).expanduser().resolve() if nor_otp_path is not None else None
        self.temp = tempfile.TemporaryDirectory(prefix='w800-')
        self.directory = Path(self.temp.name)
        self.process = None
        self.sock = None
        self.reader = None
        self.stderr = None
        self.trace = trace
        self.exploratory = exploratory
        self.debug = debug
        self.bus_test = bus_test
        self.host_camera = host_camera
        self.uart = bool(uart)
        # UART0/1/4 are the MAIN driver units; 'sif' is the EROM service UART.
        self.uart_paths = ({0: self.directory / 'uart0.sock',
                            1: self.directory / 'uart1.sock',
                            4: self.directory / 'uart4.sock',
                            'sif': self.directory / 'uartsif.sock'} if uart else {})
        self.flash_path = Path(flash_path) if flash_path else ROOT / 'firmware/prepared/flash-gdfs.bin'
        self.mask_rom_path = mask_rom_path
        self.chip_id = chip_id
        self.mask_rom_info = None
        self.nor_otp_path = nor_otp_path
        self.nor_otp_info = None
        self.erom_path = Path(erom_path).expanduser().resolve() if erom_path is not None else None
        self.erom_info = None
        self.flash = bool(flash)
        self.usb = bool(usb)
        self.mmio_limit = mmio_limit
        if audio_output not in ('none', 'coreaudio'):
            raise ValueError('audio_output must be none or coreaudio')
        self.audio_output = audio_output
        self.audio_debug_stop_grace_us = audio_debug_stop_grace_us
        self.audio_capture_path = Path(audio_capture_path).expanduser().resolve() if audio_capture_path else None
        self.events = []
        self.pressed_keys = set()
        self.key_events_sent = 0
        self.binary_path = None
        self.binary_sha256 = None

    def _machine_option(self):
        """Stage one immutable input snapshot and describe exactly those bytes."""
        options = ['w800']
        if self.host_camera:
            options += ['camera-transport=on', 'host-camera=on']
        if self.exploratory:
            options += ['exploratory=on', f'mmio-limit={self.mmio_limit}']
        if self.mask_rom_path is not None:
            if not self.mask_rom_path.is_file():
                raise ValueError('Mask ROM must be a readable raw image file')
            with self.mask_rom_path.open('rb') as source:
                data = source.read(65537)
            if not 16384 <= len(data) <= 65536 or len(data) % 4096:
                raise ValueError('Mask ROM raw image must be 16–64 KiB and a multiple of 4096 bytes')
            staged = self.directory / 'mask-rom.bin'
            staged.write_bytes(data)
            # -M uses util/keyval.c: its value grammar doubles literal commas.
            # Stage first so user filenames never become machine options.
            options += ['mask-rom=' + str(staged).replace(',', ',,'),
                        f'chip-id=0x{self.chip_id:04x}']
            self.mask_rom_info = {'source_path': str(self.mask_rom_path),
                                  'sha256': hashlib.sha256(data).hexdigest(),
                                  'size': len(data), 'base': '0xffff0000',
                                  'chip_id': f'0x{self.chip_id:04x}',
                                  'profile': f'DB2010 chip-id 0x{self.chip_id:04x}'}
        if self.nor_otp_path is not None:
            if not self.nor_otp_path.is_file():
                raise ValueError('NOR OTP must be a readable raw image file')
            with self.nor_otp_path.open('rb') as source:
                data = source.read(19)
            if len(data) != 18:
                raise ValueError('NOR OTP image must contain exactly 18 bytes (little-endian words 0x80–0x88)')
            staged = self.directory / 'nor-otp.bin'
            staged.write_bytes(data)
            options += ['nor-otp=' + str(staged).replace(',', ',,')]
            self.nor_otp_info = {'source_path': str(self.nor_otp_path),
                                'sha256': hashlib.sha256(data).hexdigest(),
                                'size': len(data),
                                'format': 'little-endian 16-bit words 0x80–0x88'}
        if self.erom_path is not None:
            if not self.erom_path.is_file():
                raise ValueError('EROM must be a readable raw image file')
            with self.erom_path.open('rb') as source:
                data = source.read(0x20001)
            if not 0x4000 <= len(data) <= 0x20000 or len(data) % 4096:
                raise ValueError('EROM raw image must be 16–128 KiB and a multiple of 4096 bytes')
            staged = self.directory / 'erom.bin'
            staged.write_bytes(data)
            options += ['erom=' + str(staged).replace(',', ',,')]
            self.erom_info = {'source_path': str(self.erom_path),
                              'sha256': hashlib.sha256(data).hexdigest(),
                              'size': len(data), 'base': '0x44000000'}
        if self.flash:
            options += ['flash=on']
        if self.usb:
            options += ['usb-cable=on']
        return ','.join(options)

    def start(self):
        binary = ROOT / 'build/qemu-system-arm'
        flash = self.flash_path
        if not binary.is_file() or not flash.is_file():
            raise RuntimeError('Build QEMU and run the w800.firmware and w800.gdfs preparation commands first.')
        source_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        if self.host_camera or (self.audio_output == 'coreaudio' and not self.audio_capture_path):
            bundled = ROOT / 'build/W800 QEMU.app/Contents/MacOS/qemu-system-arm'
            manifest_path = ROOT / 'build/qemu-app.json'
            if not bundled.is_file() or not manifest_path.is_file():
                raise RuntimeError('Rebuild QEMU to create its local microphone/camera permission bundle.')
            manifest = json.loads(manifest_path.read_text())
            bundled_sha = hashlib.sha256(bundled.read_bytes()).hexdigest()
            if manifest.get('source_sha256') != source_sha or manifest.get('binary_sha256') != bundled_sha:
                raise RuntimeError('The QEMU permission bundle is stale; rebuild QEMU before host audio/camera use.')
            binary, source_sha = bundled, bundled_sha
        self.binary_path, self.binary_sha256 = str(binary), source_sha
        path = self.directory / 'qmp.sock'
        args = [str(binary), '-M', self._machine_option(), '-bios', str(flash), '-display', 'none',
                '-serial', 'none', '-monitor', 'none', '-S',
                '-qmp', f'unix:{path},server=on,wait=off']
        if self.uart:
            index = args.index('-serial')
            del args[index:index + 2]
            for port, endpoint in self.uart_paths.items():
                args += ['-chardev', f'socket,id=uart{port},path={endpoint},server=on,wait=off,logfile={self.directory / ("uart"+str(port)+".log")}',
                         '-serial', f'chardev:uart{port}']
        # QEMU receives only PCM from the emulated audio device. This selects
        # its sink; it never opens or decodes the phone's media files.
        audio = f'driver={self.audio_output},id=w800audio'
        if self.audio_capture_path:
            audio = 'driver=wav,id=w800audio,path=' + str(self.audio_capture_path).replace(',', ',,')
        elif self.audio_output == 'coreaudio' and self.debug:
            # Brief debugger stops must not repeatedly restart the host
            # audio device. The backend still stops on a sustained pause.
            audio += f',out.debug-stop-grace={self.audio_debug_stop_grace_us}'
        args += ['-audiodev', audio]
        if self.trace:
            args += ['-d', 'in_asm,guest_errors,unimp,int,mmu', '-D', str(self.directory / 'trace.log')]
        if self.debug:
            args += ['-gdb', f'unix:{self.directory / "gdb.sock"},server=on,wait=off']
        if self.bus_test:
            args += ['-accel', 'qtest', '-qtest', f'unix:{self.directory / "qtest.sock"},server=on,wait=off',
                     '-qtest-log', '/dev/null']
        self.stderr = (self.directory / 'stderr.log').open('w')
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=self.stderr)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        deadline = time.monotonic() + 10
        while True:
            if self.process.poll() is not None:
                raise RuntimeError(self.diagnostics() or 'QEMU exited before startup')
            try:
                self.sock.connect(str(path)); break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    raise RuntimeError('QEMU control socket did not become ready')
                time.sleep(.02)
        self.reader = self.sock.makefile('rb')
        greeting = self._read()
        if 'QMP' not in greeting:
            raise RuntimeError('Invalid QMP greeting')
        self.command('qmp_capabilities')
        return greeting

    def _read(self):
        line = self.reader.readline(1024 * 1024)
        if not line:
            raise RuntimeError('QEMU control connection closed: ' + self.diagnostics())
        return json.loads(line)

    def command(self, name, arguments=None):
        packet = {'execute': name}
        if arguments is not None: packet['arguments'] = arguments
        self.sock.sendall(json.dumps(packet).encode() + b'\r\n')
        while True:
            reply = self._read()
            if 'event' in reply:
                self.events.append(reply)
                self.events = self.events[-100:]
                continue
            if 'error' in reply: raise RuntimeError(str(reply['error']))
            return reply.get('return')

    def send_key(self, key, down):
        """Route photo controls to the modeled DB2010 matrix via QEMU input."""
        if key not in KEY_QCODES:
            raise ValueError(f'Unknown phone key: {key}')
        if not isinstance(down, bool):
            raise ValueError('Key state must be a boolean')
        if (key in self.pressed_keys) == down:
            return
        self.command('input-send-event', {'events': [
            {'type': 'key', 'data': {'down': down,
             'key': {'type': 'qcode', 'data': KEY_QCODES[key]}}}]})
        if down: self.pressed_keys.add(key)
        else: self.pressed_keys.discard(key)
        self.key_events_sent += 1

    def release_keys(self):
        """Avoid held contacts across host pause, focus loss or shutdown."""
        for key in tuple(self.pressed_keys):
            self.send_key(key, False)

    def set_usb(self, attached):
        """Attach/detach the DCU-60 VBUS input on the Vincenne companion."""
        if not isinstance(attached, bool):
            raise ValueError('USB attach state must be a boolean')
        self.command('human-monitor-command', {
            'command-line': f'qom-set /machine usb-cable {"true" if attached else "false"}'})
        self.usb = attached

    def framebuffer_path(self):
        """Capture QEMU's actual graphics console; raises if none is modeled."""
        path = self.directory / 'lcd.ppm'
        self.command('screendump', {'filename': str(path), 'format': 'ppm'})
        return path

    def diagnostics(self):
        path = self.directory / 'stderr.log'
        return path.read_text(errors='replace') if path.exists() else ''

    def guest_diagnostics(self):
        """Read bounded RAM evidence; these are guest strings, not a UART model."""
        path = self.directory / 'ram-diagnostics.bin'
        self.command('human-monitor-command', {
            'command-line': f'pmemsave 0x4c000000 0x800000 "{path}"'})
        data = path.read_bytes()
        path.unlink()
        messages = re.findall(rb'(?:OS|FSFLASH): [\x20-\x7e]{1,512}', data)
        return list(dict.fromkeys(m.decode('ascii') for m in messages))[:100]

    def snapshot(self):
        return {
            'status': self.command('query-status'),
            'registers': self.command('human-monitor-command', {'command-line': 'info registers'}),
            'diagnostics': self.diagnostics(),
            'guest_diagnostic_strings': self.guest_diagnostics(),
            'firmware_display_verified': False,
            'keypad_verified': False,
            'keypad_model': 'DB2010 matrix',
            'keypad_input_events_sent': self.key_events_sent,
            'display_model': 'R61500',
            'mask_rom': self.mask_rom_info,
            'nor_otp': self.nor_otp_info,
            'erom': self.erom_info,
            'usb_attached': self.usb,
            'original_boot_verified': False,
        }

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.release_keys()
                self.command('quit')
            except (OSError, RuntimeError, ValueError): pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
        if self.reader: self.reader.close()
        if self.sock: self.sock.close()
        if self.stderr: self.stderr.close()
        self.temp.cleanup()

    def __enter__(self):
        try: self.start()
        except BaseException:
            self.close(); raise
        return self
    def __exit__(self, *args): self.close()
