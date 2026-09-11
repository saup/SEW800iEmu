"""Original R1L002 application lifecycle calls and read-only state observations.

The post-startup callback belongs to InitBook and broadcasts the firmware's
normal application-start event. It must be scheduled after the selected
startup mode has been applied on MMI, with all applicable services created.
This module does not alter instructions, service flags or validation results.
"""

POST_STARTUP_SCHEDULER = 0x44e91798
POST_STARTUP_CALLBACK = 0x44e91700
APPLICATION_STARTUP_NOTIFICATION = 0x44e2d83c
BUNDLED_GAMES = ('PuzzleSlider', 'QuadraPop')
BUNDLED_APPLICATIONS = ('WorldClock3D',)
BUNDLED_JAVA_FILES = BUNDLED_GAMES + BUNDLED_APPLICATIONS
NATIVE_JAR_INSTALLER = 0x450de9d0


def observe_java_state(session):
    """Read lifecycle bytes; a cleared wait flag is not proof a game ran."""
    return {
        'oaf_flags': session.d.read(0x4c3912b6, 1)[0],
        'oaf_state': session.d.read(0x4c0bad90, 16).hex(),
        'java_waiting_for_startup': bool(session.d.read(0x4c050a80, 1)[0]),
    }


def schedule_post_startup(session):
    """Schedule the original delayed InitBook callback on its MMI task.

    Pass a null optional book to the original scheduling wrapper. This keeps
    the current book and lets the real 500 ms callback run through UI timers.
    Call only once for the current session, after the original mode setters.
    """
    if not session.full_services:
        raise RuntimeError('Post-startup lifecycle requires all applicable services')
    if getattr(session, '_post_startup_scheduled', False):
        return observe_java_state(session)
    session.call_on_mmi(POST_STARTUP_SCHEDULER, 0)
    session._post_startup_scheduled = True
    return observe_java_state(session)


def notify_application_startup(session):
    """Call the original application lifecycle producer after Phone selection.

    InitBook calls this API immediately before a separate callback-table
    dispatcher. The producer allocates and broadcasts its own native event;
    Java/OAF handlers own their state transitions. This does not claim the
    later network-dependent callback table completed. Native game installation,
    gameplay and Contacts have been verified after this notification.
    """
    if not session.full_services:
        raise RuntimeError('Application lifecycle requires all applicable services')
    if session.word(0x4c289034) & (1 << 22):
        raise RuntimeError('Select the original Start phone mode before application startup')
    if getattr(session, '_application_startup_notified', False):
        return observe_java_state(session)
    before = observe_java_state(session)
    result = session.call_on_mmi(APPLICATION_STARTUP_NOTIFICATION, 0)
    session._application_startup_notified = True
    after = observe_java_state(session)
    session.provenance['experimental_application_startup'] = {
        'entry': hex(APPLICATION_STARTUP_NOTIFICATION),
        'result': hex(result), 'before': before, 'after_call': after,
        'later_initbook_callback_table_invoked': False,
    }
    return after


def stage_bundled_game_files(session, game):
    """Copy exact preset files to the visible Other folder in a private VM.

    This only exposes the original JAD/JAR to the original file-manager
    installer. It does not register an application, parse or alter signatures,
    or change an installation decision. Existing differing files are refused.
    All reads/writes use original filesystem APIs and allocated staging RAM.
    """
    import hashlib
    if game not in BUNDLED_JAVA_FILES:
        raise ValueError('Choose an original bundled Java title')
    files = session.files

    def read(directory, leaf, missing_ok=False):
        fd = session.invoke(0x44d83be0, *files.names(directory, leaf), 1, 0x180)
        if fd & 0x80000000:
            if missing_ok:
                return None
            raise OSError(f'Original file open failed for {leaf}: {fd:#x}')
        data = bytearray()
        try:
            while len(data) < 1024 * 1024:
                count = session.invoke(0x44d83bf0, fd, files.buffer + 1024, 8192)
                if count > 8192:
                    raise OSError(f'Original file read failed for {leaf}: {count:#x}')
                if not count:
                    return bytes(data)
                data.extend(session.d.read(files.buffer + 1024, count))
            raise ValueError('Bundled game file exceeds the bounded copy limit')
        finally:
            if session.ready:
                session.invoke(0x44d83bf8, fd)

    rows = []
    for extension in ('.jad', '.jar'):
        leaf = game + extension
        original = read('/tpa/preset/default/java', leaf)
        existing = read('/tpa/user/other', leaf, missing_ok=True)
        if existing is not None and existing != original:
            raise FileExistsError(f'Refusing to replace a differing original phone file: {leaf}')
        if existing is None:
            fd = session.invoke(0x44d83be0, *files.names('/tpa/user/other', leaf), 0x304, 0x180)
            if fd & 0x80000000:
                raise OSError(f'Original file create failed for {leaf}: {fd:#x}')
            try:
                for offset in range(0, len(original), 8192):
                    data = original[offset:offset + 8192]
                    pointer = files.stage(1024, data)
                    count = session.invoke(0x44d83c00, fd, pointer, len(data))
                    if count != len(data):
                        raise OSError(f'Original file write failed for {leaf}: {count:#x}')
            finally:
                if session.ready:
                    session.invoke(0x44d83bf8, fd)
        if read('/tpa/user/other', leaf) != original:
            raise OSError(f'Original file readback differs for {leaf}')
        rows.append({'source': '/tpa/preset/default/java/' + leaf,
                     'destination': '/tpa/user/other/' + leaf,
                     'size': len(original), 'sha256': hashlib.sha256(original).hexdigest(),
                     'existing_identical_copy': existing is not None})
    session.provenance['bundled_game_files_staged'] = rows
    return rows


def _call_installer_on_mmi(session, context, jad_path, jar_path):
    """Supply the original API's two extra zero arguments using its ARM ABI.

    The existing session call facility provides r0-r3. The native JAR API
    additionally takes two stack arguments. Save and restore those eight bytes
    at the actual MMI call/return checkpoint, while the original MMI caller is
    suspended. A missing or mismatched return invalidates this private VM.
    """
    from .guest_ui import MMI_CALL
    original_registers = session.d.registers
    stack = {}

    def write(address, data):
        if session.d.packet(f'M{address:x},{len(data):x}:' + data.hex()) != 'OK':
            raise RuntimeError('Unable to supply native installer stack arguments')

    def registers():
        regs = original_registers()
        pending = session.pending_menu
        if (regs[15] == MMI_CALL and pending is not None and
                pending['function'] == NATIVE_JAR_INSTALLER):
            if 'saved' not in pending:
                if stack or not 0x4c000000 <= regs[13] <= 0x4c7ffff8:
                    raise RuntimeError('Native installer MMI argument stack is invalid')
                stack.update(address=regs[13], saved=session.d.read(regs[13], 8))
                write(regs[13], bytes(8))
            else:
                if not stack or regs[13] != stack['address']:
                    raise RuntimeError('Native installer returned on a different MMI stack')
                write(stack['address'], stack['saved'])
                stack['restored'] = True
        return regs

    session.d.registers = registers
    try:
        result = session.call_on_mmi(NATIVE_JAR_INSTALLER, context, 0,
                                     jad_path, jar_path)
        if not stack.get('restored'):
            raise RuntimeError('Native installer did not restore its MMI argument stack')
        return result
    except Exception:
        session.ready = False
        raise
    finally:
        session.d.registers = original_registers


def begin_bundled_game_installation(session, game):
    """Open original installation UI for an exact original preset JAR.

    This is the ordinary File manager JAR API, with its ordinary asynchronous
    correlation context. UI_OAF owns the native Save in, installation and Start
    now dialogs. Callers must observe those dialogs and complete them through
    physical keys. No installed-app registry or permission state is supplied.
    The separate signed JAD provisioning route remains unverified.
    """
    import struct
    if game not in BUNDLED_JAVA_FILES:
        raise ValueError('Choose an original bundled Java title')
    if not getattr(session, '_application_startup_notified', False):
        raise RuntimeError('Original application startup must finish before installation')
    state = observe_java_state(session)
    if state['java_waiting_for_startup'] or state['oaf_flags']:
        raise RuntimeError('Original Java/OAF lifecycle is not ready for installation')
    path = '/tpa/preset/default/java/' + game + '.jar'
    jar_path = session.files.stage(1024, path.encode('utf-16le') + b'\0\0')
    correlation = session.invoke(0x44c9039c)
    context = session.files.stage(12000, struct.pack('<2I', 1, correlation))
    result = _call_installer_on_mmi(session, context, 0, jar_path)
    record = {'game': game, 'path': path, 'entry': hex(NATIVE_JAR_INSTALLER),
              'correlation': hex(correlation), 'result': result,
              'mmi_argument_stack_restored': True,
              'signed_jad_route': False}
    session.provenance.setdefault('native_game_installation_requests', []).append(record)
    if result != 1:
        raise RuntimeError(f'Original JAR installer did not accept its request: {result:#x}')
    return record


def installation_dialog(frame):
    """Recognize only reviewed original English installer dialog text.

    Require the Save in header and the dark selected-item glyphs separately:
    the orange theme's pale selection gradient is not part of either mask.
    Completion dialogs have identical white text in both reviewed themes.
    Templates observe firmware LCD output. Unknown dialogs receive no key input.
    """
    import hashlib
    import json
    from pathlib import Path
    templates = json.loads(Path(__file__).with_name('java_installation_dialogs.json').read_text())
    masks = {}
    for name, row in templates['dialogs'].items():
        matches = True
        for region in row['regions']:
            key = (tuple(region['rect']), region['mask'])
            if key not in masks:
                x, y, width, height = region['rect']
                if region['mask'] == 'dark':
                    foreground = lambda rgb: max(rgb) < 128
                elif region['mask'] == 'light':
                    foreground = lambda rgb: min(rgb) > 235
                else:
                    raise ValueError('Unknown native dialog foreground mask')
                mask = bytes(int(foreground(frame.pixelColor(xx, yy).getRgb()[:3]))
                             for yy in range(y, y + height)
                             for xx in range(x, x + width))
                masks[key] = hashlib.sha256(mask).hexdigest()
            if masks[key] != region['sha256']:
                matches = False
                break
        if matches:
            return name
    return None


def finish_bundled_game_installation(session, record):
    """Choose Games and defer launch in the original installer dialogs.

    A bounded helper for provisioning the user-requested original games. It
    never answers runtime permission, signature, replacement or error dialogs.
    Success requires the observed native Start now prompt after saving in Games.
    """
    if record['game'] not in BUNDLED_GAMES:
        raise ValueError('This bundled title belongs in Applications')
    return _finish_bundled_installation(session, record, 'Games',
                                        'native_game_installations')


def finish_bundled_application_installation(session, record):
    """Save a bundled application in Applications through reviewed native UI."""
    if record['game'] not in BUNDLED_APPLICATIONS:
        raise ValueError('Choose an original bundled application')
    return _finish_bundled_installation(session, record, 'Applications',
                                        'native_application_installations')


def _finish_bundled_installation(session, record, destination, provenance_key):
    selected = 'save_' + destination.lower()
    saved_dialog = 'saved_' + destination.lower()
    sequence = []
    saved = False
    complete = False
    previous = None
    quiet = 0
    for _ in range(100):
        frame = session.frame()
        dialog = installation_dialog(frame)
        if complete and dialog is None:
            quiet += 1
            if quiet >= 3:
                record['physical_installation_choices'] = sequence
                record['native_completion_observed'] = True
                record['destination'] = destination
                session.provenance.setdefault(provenance_key, []).append(record)
                return record
        else:
            quiet = 0
        key = None
        if dialog != previous:
            if dialog == selected and not saved:
                key = 'select'
                saved = True
            elif dialog in ('save_applications', 'save_games') and not saved:
                key = 'down' if destination == 'Games' else 'up'
            elif dialog == 'start_now' and saved:
                key = 'soft_right'  # Leave launch to the original folder UI.
                complete = True
            elif dialog == saved_dialog and saved:
                key = 'soft_left'  # Native informational OK.
        if key:
            sequence.append({'dialog': dialog, 'key': key})
            session.advance(120, (key, True))
            session.advance(250, (key, False))
        else:
            session.advance(250)
        previous = dialog
    raise TimeoutError('Original Java installer did not reach its reviewed completion dialog')


def provision_bundled_games(session, games=('QuadraPop', 'PuzzleSlider')):
    """Install original preset JARs through native MMI APIs and native dialogs.

    Call after the first real Phone-mode application-start notification, before
    accepting user navigation. Provision once per private VM. Native installer
    metadata, registry writes and ordinary untrusted permission policy remain
    owned by the firmware. There is no prepared-image or restart persistence.
    """
    installed = getattr(session, '_native_games_provisioned', set())
    results = []
    for game in games:
        if game not in BUNDLED_GAMES:
            raise ValueError('This bundled title belongs in Applications')
        if game in installed:
            continue
        record = begin_bundled_game_installation(session, game)
        finish_bundled_game_installation(session, record)
        installed.add(game)
        session._native_games_provisioned = installed
        results.append(record)
    return results


def provision_bundled_applications(session, applications=BUNDLED_APPLICATIONS):
    """Explicitly install original preset applications in Applications.

    This helper is separate from the two-game production provisioning path.
    Original installer validation, registry writes and runtime permissions are
    still owned by the firmware. Installation is once per private VM.
    """
    installed = getattr(session, '_native_applications_provisioned', set())
    results = []
    for application in applications:
        if application not in BUNDLED_APPLICATIONS:
            raise ValueError('Choose an original bundled application')
        if application in installed:
            continue
        record = begin_bundled_game_installation(session, application)
        finish_bundled_application_installation(session, record)
        installed.add(application)
        session._native_applications_provisioned = installed
        results.append(record)
    return results
