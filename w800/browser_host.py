"""Host HTTP transport feeding the original firmware's local HTML renderer.

Explicit URL opens and document hyperlinks are bridged. This is not a cellular bearer
or a replacement renderer. HTTP GET pages are copied into temporary guest
storage and opened using the original WAP API.
"""
import hashlib
import struct
from .host_web import HostWeb, validate_url
from .live_filesystem import LiveFilesystem

URL_OPEN = 0x450bec44
CONTENT_OPEN = 0x44a752d4


class BrowserHost:
    def __init__(self, session):
        self.session = session
        self.host = HostWeb()
        self.pending = None
        self.polling = False
        self.rows = []
        self.profile_ready = False
        self.home_pending = None
        self.native_urls = []
        if session.d.read(URL_OPEN, 4) != bytes.fromhex('f1b50078'):
            self.host.close()
            raise RuntimeError('Unsupported original browser URL entry')
        session.d.breakpoint(URL_OPEN, thumb=True)
        if session.d.read(CONTENT_OPEN,4) != bytes.fromhex('fbb584b0'):
            self.host.close()
            raise RuntimeError('Unsupported native content operation')
        session.d.breakpoint(CONTENT_OPEN, thumb=True)
        self.report()

    def report(self):
        self.session.provenance['host_browser'] = {
            'transport': 'host HTTP GET to original local-page renderer',
            'pending': self.pending[0] if self.pending else None,
            'pages': self.rows[-16:],
            'cellular_bearer': False, 'native_profile_ready': self.profile_ready,
            'native_urls': self.native_urls[-16:],
        }

    def open(self, url):
        validate_url(url)
        future = self.host.fetch(url)
        if self.pending: self.pending[1].cancel()
        self.pending = (url, future)
        self.session.progress('Loading web page…')
        self.report()
        return {'url': url, 'queued': True}

    def observe(self, regs):
        if regs[15] not in (URL_OPEN, CONTENT_OPEN): return False
        content = regs[15] == CONTENT_OPEN
        pointer = regs[3]
        if content:
            if not 0x4c000000 <= regs[13] <= 0x4c7fffe0:
                raise RuntimeError('Native browser content stack outside RAM')
            operation, pointer, flags, file = struct.unpack('<4I', self.session.d.read(regs[13],16))
            # Observed explicit document navigation. Other methods, assets,
            # uploads and file operations remain with the original firmware.
            if operation != 64 or flags != 8 or file != 0:
                self.session.d.step_over_breakpoint(CONTENT_OPEN, thumb=True)
                return True
        if not (0x44000000 <= pointer < 0x45fff800 or
                0x4c000000 <= pointer < 0x4c7ff800):
            self.session.d.step_over_breakpoint(regs[15], thumb=True)
            return True
        raw = self.session.d.read(pointer, 2048).split(b'\0', 1)[0]
        self.native_urls.append({'url': raw.decode('utf-8', 'replace'),
                                 'caller': hex(regs[14]), 'content': content})
        del self.native_urls[:-16]
        self.report()
        # Original main-menu start-page dispatch. Its bundled operator portal
        # may be missing for the UI language or depend on retired remote hosts.
        # Keep explicit URL entry and ordinary local documents unchanged.
        if (not content and regs[14] == 0x44f3af5f and
                (raw == b'file:///FallbackPage.html' or
                 raw.startswith(b'file:///tpa/preset/system/wap/opmenu/'))):
            if self.pending:
                self.pending[1].cancel()
                self.pending = None
            self.home_pending = raw.decode('utf-8', 'replace')
            packet = bytearray(bytes.fromhex(self.session.d.packet('g')))
            struct.pack_into('<I', packet, 0, 1)
            struct.pack_into('<I', packet, 15 * 4, regs[14] & ~1)
            if self.session.d.packet('G' + packet.hex()) != 'OK':
                raise RuntimeError('Unable to queue native browser start page')
            self.report()
            return True
        if not raw.startswith((b'http://', b'https://')) or len(raw) == 2048:
            self.session.d.step_over_breakpoint(regs[15], thumb=True)
            return True
        error = None
        try:
            self.open(raw.decode('utf-8'))
        except (ValueError, OSError, RuntimeError) as exc:
            error = exc
            self.session.progress('Unable to open web page: ' + str(exc))
        packet = bytearray(bytes.fromhex(self.session.d.packet('g')))
        if content:
            # The renderer waits for the original 6991 operation confirmation
            # before it can cancel/replace a pending document. Run its native
            # allocator/sender on this task, retaining the caller's LR.
            struct.pack_into('<I', packet, 2 * 4, 0)
            struct.pack_into('<I', packet, 3 * 4, 18 if error else 0)
            struct.pack_into('<I', packet, 15 * 4, 0x44a795c0)
        else:
            struct.pack_into('<I', packet, 0, 7 if error else 1)  # Async acceptance or API error.
            struct.pack_into('<I', packet, 15 * 4, regs[14] & ~1)
        if self.session.d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to return from hosted URL request')
        return True

    def poll(self):
        s = self.session
        if (self.polling or s.pending_menu is not None or s.q.pressed_keys):
            return
        if (not self.profile_ready and getattr(s, '_phone_mode_started', False)
                and s.browser_startup.ready_reported
                and not s._application_startup_pending
                and not s._starting_application_services):
            self.polling = True
            try:
                from .native_internet_profile import provision
                s.progress('Creating Host Internet profile…')
                provision(s)
                self.profile_ready = True
                s.progress('Host Internet profile ready')
                self.report()
            finally:
                self.polling = False
        if self.home_pending is not None:
            original_url = self.home_pending
            self.home_pending = None
            self.polling = True
            try:
                page = (b'<html><head><title>Internet services</title></head><body>'
                        b'<p>Choose More, Enter address, New address to browse the web.</p>'
                        b'<p><a href="https://example.com/">Example Domain</a></p>'
                        b'</body></html>')
                path = self.render('local:internet-services', page, 'html')
                self.rows.append({'url': original_url, 'local_start_page': True, 'path': path})
                s.progress('Internet services ready. Choose More to enter an address.')
            finally:
                self.polling = False
                self.report()
        if not self.pending or not self.pending[1].done():
            return
        self.polling = True
        url, future = self.pending
        self.pending = None
        try:
            response = future.result()
            mime = response.content_type.split(';', 1)[0].strip().lower()
            if mime not in ('text/html', 'application/xhtml+xml', 'text/vnd.wap.wml', 'text/plain'):
                raise ValueError('This web bridge currently supports HTML, WML and text pages')
            extension = 'wml' if mime == 'text/vnd.wap.wml' else 'html'
            data = response.body
            if mime in ('text/html', 'application/xhtml+xml'):
                # Preserve the remote origin for relative hyperlinks when
                # loading downloaded HTML from a local guest filename.
                import re
                from html import escape
                base = ('<base href="' + escape(response.url, quote=True) + '">').encode()
                head = re.search(br'<head(?:\s[^>]*)?>', data, re.I)
                if head and not re.search(br'<base\s', data, re.I):
                    data = data[:head.end()] + base + data[head.end():]
            if mime == 'text/plain':
                from html import escape
                data = ('<html><body><pre>' + escape(data.decode('utf-8', 'replace')) + '</pre></body></html>').encode()
            path = self.render(url, data, extension)
            self.rows.append({'url': response.url, 'status': response.status,
                              'path': path, 'bytes': len(data)})
            s.progress('Web page opened in the original phone browser')
        except (OSError, ValueError, TimeoutError) as error:
            from html import escape
            page = ('<html><head><title>Page unavailable</title></head><body>'
                    '<p>Unable to load ' + escape(url) + '</p><p>' +
                    escape(str(error)) + '</p><p>Choose More, Enter address to try another page.</p>'
                    '</body></html>').encode()
            path = self.render(url, page, 'html')
            self.rows.append({'url': url, 'error': str(error), 'path': path})
            s.progress('Web page failed: ' + str(error))
        finally:
            self.polling = False
            self.report()

    def render(self, url, data, extension):
        s = self.session
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        path = f'/tpa/user/other/web-{digest}.{extension}'
        LiveFilesystem(s).upload_bytes(path, data, replace=True)
        pointer = s.files.stage(14000, ('file://' + path).encode() + b'\0')
        s.call_on_mmi(URL_OPEN, 0x44245520, 0, 6, pointer)
        return path

    def cancel(self):
        self.home_pending = None
        if self.pending:
            self.pending[1].cancel()
            self.pending = None
            self.session.progress('Web page load cancelled')
            self.report()

    def close(self):
        self.host.close()
