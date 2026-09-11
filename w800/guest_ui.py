"""Run the original R1L002 UI components in a disposable QEMU session.

The mounted startup caller is parked while the original component manager
starts the UI services. Original timed receives let the other tasks run.
No firmware instructions, validation results or menu assets are substituted.
"""
from collections import deque
import hashlib
import struct
import time

from PySide6.QtGui import QImage

from .backend import QemuBackend, ROOT, KEY_QCODES
from .guest_files import ANCHOR, OriginalFiles, validate_firmware
from .tools.debug import Debugger
from .tools.trace_messages import cstring, decode
from .components import start_remaining
from .browser_host import BrowserHost, URL_OPEN, CONTENT_OPEN
from .browser_startup import BrowserStartup, CALLBACK as WAP_CALLBACK
from .offline_status import OfflineStatus, STATUS_SEND, AUDIO_STATUS_SEND, PLMN_STATUS_SEND
from .phonebook_services import install_unavailable_adapter, initialize as initialize_phonebook
from .radio_rejection import OriginalRadioRejection, REQUEST_SEND as RADIO_REQUEST_SEND
from .browser_unavailable import BrowserUnavailable, URL_LOAD_GUARD


COMPONENTS = (
    (55, 'Windowsystem'), (16, 'PR_Process'), (4, 'CLOCK_Process'),
    (29, 'Call Handler'), (32, 'Connection Manager'), (18, 'SEMC_TimerProcess'),
    (3, 'UI_Util_Process'), (10, 'Swbp_Process'), (15, 'MemoryManager_Process'),
    (19, 'SEMC_PDH_Process'), (12, 'ImageHandler_Process'),
    (43, 'AT Command Server'), (13, 'ROUTER_Process'),
    (6, 'PB_INIT_Process'), (5, 'PB_Process'), (7, 'Calendar_Process'),
    (14, 'Notes_Process'), (11, 'Extras_Process'),
    # OAF synchronously registers with Download during initialization.
    (23, 'DownloadProcess'),
    (35, 'OAF_Process'), (36, 'UI_OAF_Process'),
    (38, 'Java_VM_Timer'), (37, 'Java_VM_Process'),
    # MMI acquires IAudioControlManager once during44d22c9a. Publish the
    # real provider first so Timer/notification sounds retain that interface.
    (27, 'Audio Control'), (2, 'MMI_Process'),
)
CHECKPOINTS = {
    0x44d22c64: 'MMI_Process', 0x44e91cc0: 'InitBook',
    0x44e917b0: 'StartUp', 0x44e918d4: 'Started',
    0x44e919b8: 'MMIInit', 0x44e91b2c: 'StartupMode',
    0x44ab88b4: 'LCD completion', 0x44e44e08: 'Physical key signal',
    0x44e99f04: 'Theme activation result',
}
LOGGERS = {0x45169c70, 0x44ad6474}
# These checkpoints only collect diagnostics; neither supplies guest data nor
# completes a service. Keep all loggers, faults and functional adapters active.
BOOST_DIAGNOSTICS = {0x44ab88b4, 0x44e44e08}
FATAL = {0x44020000: False, 0: False, 4: False, 0xc: False, 0x10: False,
         0x450809a8: True, 0x4490cf80: True}
MMI_CALL = 0x44d22d86
PHONE_MENU_ACTION = 0x44e91afc


class OriginalUISession:
    def __init__(self, cancelled=lambda: False, progress=lambda message: None,
                 start_screen='startup', default_theme=False, full_services=True,
                 audio_output='none', audio_capture_path=None,
                 audio_debug_stop_grace_us=20000, game_boost=False, host_camera=False,
                 start_phone_standby=False, uart=False, host_web=False):
        if start_screen not in ('menu', 'startup'):
            raise ValueError('Unsupported original UI start screen')
        self.start_screen = start_screen
        if not isinstance(start_phone_standby, bool):
            raise ValueError('Start phone standby must be a boolean')
        self.start_phone_standby = start_phone_standby
        self.host_web_enabled = host_web
        self.host_browser = None
        self.uart = uart
        self.debug_log = None
        self._standby_pending = False
        self._phone_mode_started = False
        self.default_theme = default_theme
        self.full_services = full_services
        self.audio_output = audio_output
        if not isinstance(host_camera, bool):
            raise ValueError("Host camera must be a boolean")
        self.host_camera = host_camera
        self.audio_capture_path = audio_capture_path
        self.audio_debug_stop_grace_us = audio_debug_stop_grace_us
        self.browser_startup = None
        self.pending_menu = None
        self.cancelled = cancelled
        self.progress = progress
        self.q = self.d = self.files = None
        self.offline_status = None
        self.radio_rejection = None
        self.browser_unavailable = None
        self.phonebook_reply = None
        self.virtual_network = None
        self._phonebook_initialized = False
        self._application_startup_pending = False
        self._starting_application_services = False
        self.ready = False
        self.events = deque(maxlen=1000)
        self.messages = deque(maxlen=500)
        self.frames_completed = 0
        self.provenance = {}
        if not isinstance(game_boost, bool):
            raise ValueError('Game boost must be a boolean')
        self.game_boost_requested = game_boost
        self.game_boost = False
        self.observed_checkpoints = 0

    @property
    def idle_window_ms(self):
        return 50 if self.game_boost else 40

    def set_game_boost(self, enabled):
        """Change diagnostics at the parked caller, without changing time."""
        self.check_cancelled()
        if not isinstance(enabled, bool) or not self.ready:
            raise RuntimeError('Game boost requires a ready original UI session')
        if enabled != self.game_boost:
            try:
                for pc in sorted(BOOST_DIAGNOSTICS):
                    self.d.breakpoint(pc, enabled=not enabled, thumb=True)
            except Exception:
                self.ready = False
                raise
        self.game_boost = self.game_boost_requested = enabled
        self.provenance['game_boost'] = {
            'enabled': enabled, 'idle_window_ms': self.idle_window_ms,
            'running_display_poll_ms': 16 if enabled else None,
            'disabled_diagnostic_checkpoints': [hex(pc) for pc in sorted(BOOST_DIAGNOSTICS)] if enabled else [],
            'clock_multiplier': 1, 'cpu_clock_cap': False,
        }

    def check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('UI session stopped')

    def word(self, address):
        return struct.unpack('<I', self.d.read(address, 4))[0]

    def start(self):
        self.check_cancelled()
        flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
        validate_firmware(flash)
        self.provenance = {
            'mode': 'original firmware UI components; startup caller suspended',
            'guest_instruction_patches': False, 'validation_result_changes': False,
            'source_sha256': hashlib.sha256(flash.read_bytes()).hexdigest(),
            'audio_debug_stop_grace_us': self.audio_debug_stop_grace_us,
            'host_camera': self.host_camera,
        }
        try:
            self.progress('Booting firmware and mounting filesystems…')
            self.q = QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                                 audio_output=self.audio_output,
                                 audio_capture_path=self.audio_capture_path,
                                 audio_debug_stop_grace_us=self.audio_debug_stop_grace_us,
                                 host_camera=self.host_camera, uart=self.uart)
            self.q.start()
            if self.uart:
                self.debug_log = (self.q.directory / "firmware-debug.log").open("w", buffering=1)
            self.provenance["uart_endpoints"] = {str(p): str(path) for p, path in self.q.uart_paths.items()}
            self.provenance['qemu_sha256'] = self.q.binary_sha256
            self.provenance['qemu_binary'] = self.q.binary_path
            self.d = Debugger(self.q)
            self.d.sock.settimeout(5)
            self.d.breakpoint(0x44f772b4, thumb=True)
            # Startup observations can outlast the ordinary socket deadline.
            # Keep one run request outstanding and poll its response within
            # a bounded, cancellable wait, preserving partial RSP packets.
            self.d.send_packet('c')
            self.d.receive_packet(timeout=20, cancelled=self.cancelled)
            self.check_cancelled()
            regs = self.d.registers()
            if regs[15] != 0x44f772b4:
                raise RuntimeError('Application manager initialization was not reached')
            manager = self.word(regs[0] + 4)
            self.manager = manager
            service = self.word(manager + 0x34)
            if self.word(self.word(service) + 0xb4) != 0x446f3df9:
                raise RuntimeError('Unsupported original task-service interface')
            self.d.breakpoint(0x44f772b4, enabled=False, thumb=True)
            self.d.breakpoint(ANCHOR, thumb=True)
            self.d.run()
            self.check_cancelled()
            if self.d.registers()[15] != ANCHOR:
                raise RuntimeError('Mounted-filesystem checkpoint was not reached')
            self.files = OriginalFiles(self.d)
            descriptor = self.word(0x4c04efd0) + 6 * 32
            if self.word(descriptor) != 2 or self.word(descriptor + 0x10) != 0x44d22c65:
                raise RuntimeError('Unsupported original MMI descriptor')
            mode = self.word(0x4c39dcdc)
            if mode != 1:
                raise RuntimeError('Unsupported original component startup mode')
            self.provenance['original_manager_mode'] = mode
            for pc in {*CHECKPOINTS, *LOGGERS}:
                self.d.breakpoint(pc, thumb=True)
            for pc, thumb in FATAL.items():
                self.d.breakpoint(pc, thumb=thumb)
            self.d.breakpoint(PHONE_MENU_ACTION, thumb=True)
            self.provenance['start_phone_action'] = ('original standby after Start phone'
                if self.start_phone_standby else 'emulator UI callback redirect to original MainMenu')
            self.ready = True
            for component, name in COMPONENTS:
                self.progress(f'Starting original {name}…')
                if self.invoke(0x44f773f8, manager, component, mode):
                    raise RuntimeError(f'Original {name} could not start')
            # Keep the scheduler filter outside the file staging area.
            self.wait_filter = self.files.stage(16000, struct.pack('<2I', 1, 0x7ffffffe))
            self.offline_status = OfflineStatus(self)
            self.d.breakpoint(STATUS_SEND, thumb=True)
            self.d.breakpoint(AUDIO_STATUS_SEND, thumb=True)
            self.d.breakpoint(PLMN_STATUS_SEND, thumb=True)
            self.radio_rejection = OriginalRadioRejection(self)
            self.d.breakpoint(RADIO_REQUEST_SEND, thumb=True)
            self.browser_unavailable = BrowserUnavailable(self)
            self.d.breakpoint(URL_LOAD_GUARD, thumb=True)
            self.browser_startup = BrowserStartup(self)
            if self.host_web_enabled:
                self.host_browser = BrowserHost(self)
            self.phonebook_reply = install_unavailable_adapter(self)
            self.advance(1000)
            self.advance(1000)
            if self.full_services:
                start_remaining(self)
            for _ in range(8):
                if any(e['checkpoint'] == 'StartupMode' for e in self.events):
                    break
                self.advance(1000)
            else:
                raise TimeoutError('Original MMI initialization did not reach the startup selector')
            self.provenance['mmi_handle'] = hex(self.word(0x4c0bae24))
            self.progress('Loading original phone contacts…')
            initialize_phonebook(self)
            if self.default_theme:
                self.progress('Applying original orange Walkman theme…')
                self.apply_default_theme()
            if self.start_screen == 'menu':
                self.progress('Opening original phone menu…')
                self.open_main_menu()
            self.set_game_boost(self.game_boost_requested)
            self.progress('Original firmware UI running')
            return self
        except Exception:
            self.close()
            raise

    def invoke(self, function, *args, key_event=None, frame_callback=None):
        """Only a verified return restores the parked startup caller.

        A fault, cancellation or unresolved wait invalidates the context;
        callers must discard this VM, rather than resume an incomplete call.
        """
        self.check_cancelled()
        if not self.ready or len(args) > 4:
            raise RuntimeError('Original UI call context is unavailable')
        self.ready = False
        d = self.d
        packet = bytearray(self.files.saved)
        stack = struct.unpack_from('<I', packet, 13 * 4)[0]
        if not 0x4c000000 <= stack <= 0x4c800000:
            raise RuntimeError('Original caller stack is outside RAM')
        for index, value in enumerate(args):
            struct.pack_into('<I', packet, index * 4, value)
        struct.pack_into('<I', packet, 14 * 4, ANCHOR | 1)
        struct.pack_into('<I', packet, 15 * 4, function & ~1)
        if d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to establish original UI call arguments')
        delivered = key_event is None
        deadline = time.monotonic() + 20
        for _ in range(5000):
            self.check_cancelled()
            if time.monotonic() > deadline:
                raise TimeoutError('Original UI call exceeded the observation limit')
            d.send_packet('c')
            if not delivered:
                try:
                    self.q.send_key(*key_event)
                    delivered = True
                except RuntimeError as error:
                    if 'VM not running' not in str(error):
                        raise
            display_poll = ({'on_wait': frame_callback, 'poll_interval': .016}
                            if frame_callback is not None else {})
            d.receive_packet(timeout=max(.001, deadline - time.monotonic()),
                             cancelled=self.cancelled, **display_poll)
            self.check_cancelled()
            regs = d.registers()
            pc = regs[15]
            self.observed_checkpoints += 1
            if pc in (STATUS_SEND, AUDIO_STATUS_SEND, PLMN_STATUS_SEND) and self.offline_status is not None:
                self.offline_status.route(regs)
                d.step_over_breakpoint(pc, thumb=True)
                continue
            if pc == RADIO_REQUEST_SEND and self.radio_rejection is not None:
                self.radio_rejection.route(regs)
                d.step_over_breakpoint(pc, thumb=True)
                continue
            if pc == URL_LOAD_GUARD and self.browser_unavailable is not None:
                self.browser_unavailable.observe(regs)
                d.step_over_breakpoint(pc, thumb=True)
                continue
            if pc == PHONE_MENU_ACTION:
                # The original startup selector has identified Start phone
                # and run its mode setters, including leaving Music only.
                # Replace only the following page transition with the
                # original standby/menu launcher, on the original MMI task.
                # Returning to its existing epilogue preserves the callback
                # stack. SIM/radio startup and its results are untouched.
                if not 0x4c000000 <= regs[13] <= 0x4c7ffff4:
                    raise RuntimeError('Original startup callback stack is outside RAM')
                call = bytearray(bytes.fromhex(d.packet('g')))
                struct.pack_into('<I', call, 0, 0x44407c14)
                struct.pack_into('<I', call, 14 * 4, 0x44e91b07)
                struct.pack_into('<I', call, 15 * 4,
                                 0x44e9203c if self.start_phone_standby else 0x45059c7c)
                if d.packet('G' + call.hex()) != 'OK':
                    raise RuntimeError('Unable to redirect the startup menu action')
                self._phone_mode_started = True
                self._standby_pending = self.start_phone_standby
                self.events.append({'checkpoint': ('Start phone → original Standby'
                                    if self.start_phone_standby else 'Start phone → original MainMenu'),
                                    'args': list(regs[:4])})
                if not getattr(self, '_application_startup_notified', False):
                    self._application_startup_pending = True
                continue
            if pc in (URL_OPEN, CONTENT_OPEN) and self.host_browser is not None:
                self.host_browser.observe(regs)
                continue
            if pc == WAP_CALLBACK and self.browser_startup is not None:
                self.browser_startup.observe(regs)
                continue
            if pc == MMI_CALL and self.pending_menu is not None:
                # GUI creation belongs on the original MMI task, so its
                # timers, book ownership and callbacks keep their context.
                pending = self.pending_menu
                if 'saved' not in pending:
                    pending['saved'] = bytes.fromhex(d.packet('g'))
                    pending['stack'] = regs[13]
                    call = bytearray(pending['saved'])
                    for i, arg in enumerate(pending['args']):
                        struct.pack_into('<I', call, i * 4, arg)
                    struct.pack_into('<I', call, 14 * 4, MMI_CALL | 1)
                    struct.pack_into('<I', call, 15 * 4, pending['function'])
                    if d.packet('G' + call.hex()) != 'OK':
                        raise RuntimeError('Unable to call the original menu launcher')
                else:
                    if regs[13] != pending['stack']:
                        raise RuntimeError('Original menu launcher returned on a different stack')
                    if d.packet('G' + pending['saved'].hex()) != 'OK':
                        raise RuntimeError('Unable to restore the original MMI caller')
                    d.breakpoint(MMI_CALL, enabled=False, thumb=True)
                    self.pending_menu = None
                    pending['result'] = regs[0]
                    self.mmi_call_result = pending['result']
                continue
            if pc in FATAL:
                self.provenance['fatal_registers'] = [hex(x) for x in regs]
                raise RuntimeError(f'Original firmware fault/reset at {pc:08x}; restart the UI session')
            if pc == ANCHOR:
                if regs[13] != stack or not delivered:
                    raise RuntimeError('Original call did not complete with the expected context')
                if d.packet('G' + self.files.saved.hex()) != 'OK':
                    raise RuntimeError('Unable to restore the parked caller')
                self.ready = True
                return regs[0]
            if pc in LOGGERS:
                fmt = cstring(d, regs[0])
                arguments = list(regs[1:4]) + list(struct.unpack('<8I', d.read(regs[13], 32)))
                message = decode(d, fmt, arguments).strip()
                self.messages.append(message)
                if self.debug_log is not None:
                    self.debug_log.write(message + "\n")
                if 'User called assert' in message or 'Assert Failure' in message:
                    raise RuntimeError('Original firmware assertion; restart the UI session')
            elif pc in CHECKPOINTS:
                self.events.append({'checkpoint': CHECKPOINTS[pc], 'args': list(regs[:4])})
                if pc == 0x44ab88b4:
                    self.frames_completed += 1
            else:
                raise RuntimeError(f'Unexpected firmware stop at {pc:08x}')
            d.step_over_breakpoint(pc, thumb=True)
        raise TimeoutError('Original UI checkpoint limit exceeded')

    def advance(self, milliseconds=100, key_event=None, frame_callback=None):
        if not 10 <= milliseconds <= 1000:
            raise ValueError('Scheduler window must be 10–1000 ms')
        if key_event is not None:
            key, down = key_event
            if key not in KEY_QCODES or not isinstance(down, bool):
                raise ValueError('Invalid physical phone key event')
            if key == 'back' and down and self.host_browser is not None:
                self.host_browser.cancel()
        result = self.invoke(0x44a47ae8, milliseconds, self.wait_filter,
                             key_event=key_event, frame_callback=frame_callback)
        if self.offline_status is not None:
            self.offline_status.poll()
        if self.radio_rejection is not None:
            self.radio_rejection.poll()
        if self.browser_unavailable is not None:
            self.browser_unavailable.poll()
        if self.browser_startup is not None:
            self.browser_startup.report()
        if self.host_browser is not None:
            self.host_browser.poll()
        if (self._application_startup_pending and self.full_services and
                not self._starting_application_services and self.pending_menu is None and
                not self.q.pressed_keys):
            # Wait for the physical Start phone key to be released. The
            # original installer uses its own dialogs and physical choices,
            # so neither a held user contact nor a nested MMI call may leak
            # into provisioning. This guard also covers helper advances.
            self._starting_application_services = True
            try:
                from .java_services import (notify_application_startup,
                                            provision_bundled_games,
                                            provision_bundled_applications)
                self.progress('Starting original Java services…')
                notify_application_startup(self)
                self.advance(1000)
                self.progress('Installing original bundled games…')
                provision_bundled_games(self)
                self.progress('Installing original World Clock application…')
                provision_bundled_applications(self)
                self._application_startup_pending = False
                self.progress('Original firmware UI running')
            finally:
                self._starting_application_services = False
        if (self._standby_pending and not self._starting_application_services
                and self.pending_menu is None and not self.q.pressed_keys):
            self._standby_pending = False
            from .virtual_network import show_original_standby
            show_original_standby(self)
        return result

    def frame(self):
        self.check_cancelled()
        frame = QImage(str(self.q.framebuffer_path()))
        if frame.isNull() or (frame.width(), frame.height()) != (176, 220):
            raise RuntimeError('QEMU did not return the original 176×220 LCD')
        return frame

    def open_main_menu(self):
        """Open the firmware's MainMenu element through its own UI launcher.

        This starts an independent original menu book. SIM authentication
        remains untouched; no radio or SIM startup success is reported.
        """
        if self.d.read(0x44407c14, 18) != 'MainMenu\0'.encode('utf-16le'):
            raise RuntimeError('Original MainMenu identifier did not match')
        self.call_on_mmi(0x45059c7c, 0x44407c14)
        if not getattr(self, '_application_startup_notified', False):
            self._application_startup_pending = True
        self.provenance['menu_launcher'] = 'original 45059c7c(MainMenu), on MMI task'

    def call_on_mmi(self, function, *args):
        if self.pending_menu is not None or not self.ready or len(args) > 4:
            raise RuntimeError('An original UI call is already unresolved')
        call = {'function': function, 'args': args}
        self.pending_menu = call
        self.d.breakpoint(MMI_CALL, thumb=True)
        for _ in range(5):
            self.advance(1000)
            if 'result' in call:
                self.advance(300)
                # advance can finish deferred application startup, which
                # makes further native MMI calls. Retain this invocation's
                # result independently of those completed nested calls.
                self.mmi_call_result = call['result']
                return call['result']
        self.ready = False
        raise TimeoutError('Original MMI menu launch did not complete')

    def set_virtual_network_name(self, name):
        """Set the original offline standby label; None/empty restores it."""
        from .virtual_network import VirtualNetworkName, validate_name
        name = validate_name(name)
        if self.virtual_network is None:
            self.virtual_network = VirtualNetworkName(self)
        return self.virtual_network.set(name)

    def apply_default_theme(self):
        # Same original entry and path/name arguments observed when choosing
        # Default in Settings > Display > Themes. -1 is its supported
        # standalone caller, as used by the original default-theme loader.
        directory, name = self.files.names('/tpa/user/theme', 'Default.thm')
        self.call_on_mmi(0x44e99f30, directory, name, 0xffffffff, 1)
        for _ in range(10):
            results = [e for e in self.events if e['checkpoint'] == 'Theme activation result']
            if results:
                result = results[-1]['args'][1]
                if result != 0xf:
                    raise RuntimeError(f'Original theme activation failed: {result:#x}')
                self.advance(500)
                self.provenance['theme'] = '/tpa/user/theme/Default.thm'
                return
            self.advance(1000)
        raise TimeoutError('Original theme activation did not complete')

    def report(self):
        flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
        return {**self.provenance, 'caller_restored': self.ready,
                'lcd_completions': self.frames_completed,
                'lcd_completion_trace_enabled': not self.game_boost,
                'observed_checkpoints': self.observed_checkpoints, 'events': list(self.events),
                'messages': list(self.messages),
                'source_unchanged': hashlib.sha256(flash.read_bytes()).hexdigest() == self.provenance.get('source_sha256')}

    def close(self):
        self.ready = False
        if self.host_browser is not None:
            self.host_browser.close()
            self.host_browser = None
        if self.debug_log is not None:
            self.debug_log.close()
            self.debug_log = None
        if self.d:
            self.d.close()
            self.d = None
        if self.q:
            self.q.close()
            self.q = None
        self.files = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
