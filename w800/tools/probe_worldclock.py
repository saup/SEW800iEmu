"""Inspect the bundled WorldClock3D through native installation and keypad UI."""
import code
import hashlib
import io
import json
import os
import zipfile
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui, java_services
from w800.backend import ROOT


def main():
    app = QApplication.instance() or QApplication([])
    output = ROOT / 'reports/worldclock-native'
    output.mkdir(parents=True, exist_ok=True)
    point = 0x44d6e10a
    previous = guest_ui.CHECKPOINTS.get(point)
    guest_ui.CHECKPOINTS[point] = 'Java application execution'
    session = guest_ui.OriginalUISession(default_theme=True,
        progress=lambda message: print(message, flush=True))
    rows, keys, sources = [], [], []

    def record(label):
        path = output / (label + '.png')
        frame = session.frame()
        frame.save(str(path))
        rows.append({'label': label, 'path': str(path),
                     'installer_dialog': java_services.installation_dialog(frame),
                     'java': java_services.observe_java_state(session),
                     'events': list(session.events)[-30:]})
        report = {'images': rows, 'keys': keys, 'sources': sources,
                  'session': session.report()}
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(label, rows[-1]['installer_dialog'], flush=True)
        return str(path)

    def press(key, down=120, up=450):
        keys.append(key)
        session.advance(down, (key, True))
        session.advance(up, (key, False))

    def read_source(name):
        path = '/tpa/preset/default/java/' + name
        pointer = session.files.stage(0, path.encode('utf-16le') + b'\0\0')
        fd = session.invoke(0x45105f48, pointer, 1, 0x1b6)
        if fd & 0x80000000:
            raise OSError(f'Original file open failed: {fd:#x}')
        result = bytearray()
        try:
            while len(result) < 1024*1024:
                ptr = session.files.buffer + 1024
                count = session.invoke(0x451063d4, fd, ptr, 8192)
                if count > 8192:
                    raise OSError(f'Original file read failed: {count:#x}')
                if not count:
                    data = bytes(result)
                    (output / name).write_bytes(data)
                    row = {'path': path, 'size': len(data),
                           'sha256': hashlib.sha256(data).hexdigest()}
                    if name.endswith('.jad'):
                        row['jad'] = data.decode(errors='replace')
                    else:
                        with zipfile.ZipFile(io.BytesIO(data)) as jar:
                            row['manifest'] = jar.read('META-INF/MANIFEST.MF').decode(errors='replace')
                            row['jar_entries'] = jar.namelist()
                    sources.append(row)
                    return
                result.extend(session.d.read(ptr, count))
            raise ValueError('Original source exceeded bounded read')
        finally:
            if session.ready:
                session.invoke(0x45106254, fd, 0)

    try:
        session.start()
        press('select')
        read_source('WorldClock3D.jad')
        read_source('WorldClock3D.jar')
        request = java_services.begin_bundled_game_installation(session, 'WorldClock3D')
        session.advance(1000)
        record('01-native-installer')
        code.interact(local={'session': session, 'press': press, 'record': record,
                             'output': output, 'request': request,
                             'java_services': java_services})
    finally:
        if session.ready:
            record('final')
        session.close()
        if previous is None:
            guest_ui.CHECKPOINTS.pop(point, None)
        else:
            guest_ui.CHECKPOINTS[point] = previous


if __name__ == '__main__':
    main()
