#!/usr/bin/env python3
"""
StarlightRDT Agent
- Runs a local VNC server on 127.0.0.1:5900
- Connects to the relay and registers the session code
- When a viewer connects, bridges VNC traffic through the relay WebSocket
- Shows a system tray icon with the code on Windows/macOS/Linux
"""

import os, sys, socket, struct, threading, time, json, secrets, logging, argparse, platform, asyncio
from pathlib import Path
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('StarlightRDT')

RELAY_URL   = os.environ.get('STARLIGHT_RELAY', 'wss://starlight-rdt-relay.YOUR-NAME.workers.dev')
VNC_PORT    = int(os.environ.get('STARLIGHT_PORT', '5900'))
CODE_TTL    = 7 * 24 * 3600
CONFIG_DIR  = Path.home() / '.starlight-rdt'
CONFIG_FILE = CONFIG_DIR / 'config.json'
PLATFORM    = platform.system()

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    HAS_INPUT = True
except ImportError:
    HAS_INPUT = False

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False


# ── Screen capture ────────────────────────────────────────────────────────────
def get_screen_size() -> Tuple[int, int]:
    if HAS_MSS:
        with mss.mss() as s:
            m = s.monitors[1]
            return m['width'], m['height']
    if HAS_PIL:
        img = ImageGrab.grab()
        return img.size
    return 1920, 1080


def capture_screen(x: int, y: int, w: int, h: int) -> bytes:
    if HAS_MSS:
        with mss.mss() as s:
            img = s.grab({'top': y, 'left': x, 'width': w, 'height': h})
            # mss gives BGRA — send as-is; JS copyRaw reads [si]=B,[si+1]=G,[si+2]=R
            return bytes(img.raw)
    if HAS_PIL:
        # PIL gives RGBA — reorder to BGRA for the JS decoder
        img = ImageGrab.grab(bbox=(x, y, x+w, y+h)).convert('RGBA')
        raw = img.tobytes()
        out = bytearray(w * h * 4)
        for i in range(w * h):
            out[i*4]   = raw[i*4+2]  # B
            out[i*4+1] = raw[i*4+1]  # G
            out[i*4+2] = raw[i*4]    # R
            out[i*4+3] = 255
        return bytes(out)
    # Fallback — mid-grey BGRA
    return bytes([100, 100, 100, 255] * (w * h))


# ── Config / session code ─────────────────────────────────────────────────────
class Config:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self._load()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                self._data = json.loads(CONFIG_FILE.read_text())
            except Exception:
                self._data = {}

    def _save(self):
        CONFIG_FILE.write_text(json.dumps(self._data, indent=2))

    def get_code(self) -> str:
        now = time.time()
        if 'code' not in self._data or now - self._data.get('code_issued', 0) > CODE_TTL:
            chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            self._data['code'] = ''.join(secrets.choice(chars) for _ in range(8))
            self._data['code_issued'] = now
            self._save()
        return self._data['code']

    def code_expires_in(self) -> int:
        issued = self._data.get('code_issued', 0)
        return max(0, int(CODE_TTL - (time.time() - issued)))

    def expiry_str(self) -> str:
        secs  = self.code_expires_in()
        days  = secs // 86400
        hours = (secs % 86400) // 3600
        return f'Refreshes in {days}d {hours}h'


# ── RFB Protocol constants ────────────────────────────────────────────────────
RFB_VER   = b'RFB 003.008\n'
SEC_NONE  = 1
# Pixel format: 32bpp BGRA  (B at byte 0, G at 1, R at 2, A at 3)
# r-shift=16 means red value goes into bits 16-23 of the 32-bit word,
# but since we're little-endian that puts it at byte index 2.
# b-shift=0 puts blue at byte index 0.
PIX_FMT = struct.pack(
    '>BBBBHHHBBB3x',
    32,   # bits-per-pixel
    24,   # depth
    0,    # big-endian: False (little-endian 32-bit words)
    1,    # true-colour: True
    255,  # red max
    255,  # green max
    255,  # blue max
    16,   # red shift   → byte index 2 in LE word
    8,    # green shift → byte index 1
    0,    # blue shift  → byte index 0
)

KEYSYM = {
    0xff08:'backspace', 0xff09:'tab',    0xff0d:'enter',   0xff1b:'escape',
    0xffff:'delete',    0xff50:'home',   0xff57:'end',      0xff55:'pageup',
    0xff56:'pagedown',  0xff51:'left',   0xff52:'up',       0xff53:'right',
    0xff54:'down',      0xff63:'insert',
    0xffe1:'shift',     0xffe2:'shift',  0xffe3:'ctrl',     0xffe4:'ctrl',
    0xffe9:'alt',       0xffea:'alt',    0xffeb:'winleft',  0xffec:'winright',
    **{0xffbe+i: f'f{i+1}' for i in range(12)},
}


# ── RFB client handler (one per viewer connection) ────────────────────────────
class RFBClient(threading.Thread):
    def __init__(self, conn: socket.socket, addr, server: 'VNCServer'):
        super().__init__(daemon=True)
        self.conn    = conn
        self.addr    = addr
        self.server  = server
        self.running = True

    def _send(self, data: bytes):
        try:
            self.conn.sendall(data)
        except OSError:
            self.running = False

    def _recv(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n and self.running:
            try:
                chunk = self.conn.recv(n - len(buf))
                if not chunk:
                    self.running = False
                    break
                buf += chunk
            except OSError:
                self.running = False
                break
        return buf

    def run(self):
        try:
            if not self._handshake():
                return
            self._message_loop()
        except Exception as e:
            log.debug(f'RFBClient error: {e}')
        finally:
            self.conn.close()
            log.info(f'Viewer disconnected: {self.addr}')

    def _handshake(self) -> bool:
        self._send(RFB_VER)
        self._recv(12)
        self._send(struct.pack('!BB', 1, SEC_NONE))
        self._recv(1)
        self._send(struct.pack('!I', 0))
        self._recv(1)
        w, h = self.server.width, self.server.height
        name = b'StarlightRDT'
        self._send(struct.pack('!HH', w, h) + PIX_FMT + struct.pack('!I', len(name)) + name)
        log.info(f'RFB handshake done with {self.addr}  ({w}x{h})')
        return True

    def _message_loop(self):
        while self.running:
            hdr = self._recv(1)
            if not hdr:
                break
            t = hdr[0]
            if t == 0:    # SetPixelFormat
                self._recv(19)
            elif t == 2:  # SetEncodings
                d = self._recv(3)
                count = struct.unpack('!xH', d)[0]
                self._recv(count * 4)
            elif t == 3:  # FramebufferUpdateRequest
                d = self._recv(9)
                _, x, y, w, h = struct.unpack('!BHHHH', d)
                self._send_update(x, y, w, h)
            elif t == 4:  # KeyEvent
                d = self._recv(7)
                down, _, sym = struct.unpack('!BxxI', d)
                self._handle_key(down, sym)
            elif t == 5:  # PointerEvent
                d = self._recv(5)
                mask, x, y = struct.unpack('!BHH', d)
                self._handle_ptr(mask, x, y)
            elif t == 6:  # ClientCutText
                d = self._recv(7)
                length = struct.unpack('!3xI', d)[0]
                self._recv(length)

    def _send_update(self, x: int, y: int, w: int, h: int):
        sw, sh = self.server.width, self.server.height
        x = max(0, min(x, sw - 1))
        y = max(0, min(y, sh - 1))
        w = max(1, min(w, sw - x))
        h = max(1, min(h, sh - y))
        data = capture_screen(x, y, w, h)
        self._send(struct.pack('!BBH', 0, 0, 1))
        self._send(struct.pack('!HHHHi', x, y, w, h, 0))
        self._send(data)

    def _handle_key(self, down: int, sym: int):
        if not HAS_INPUT:
            return
        key = KEYSYM.get(sym)
        if key is None and 0x20 <= sym <= 0x7e:
            key = chr(sym)
        if not key:
            return
        try:
            if down:
                pyautogui.keyDown(key)
            else:
                pyautogui.keyUp(key)
        except Exception:
            pass

    def _handle_ptr(self, mask: int, x: int, y: int):
        if not HAS_INPUT:
            return
        try:
            pyautogui.moveTo(x, y)
            if mask & 1:
                pyautogui.mouseDown(button='left')
            else:
                pyautogui.mouseUp(button='left')
            if mask & 4:
                pyautogui.mouseDown(button='right')
            else:
                pyautogui.mouseUp(button='right')
            if mask & 8:
                pyautogui.scroll(3)
            if mask & 16:
                pyautogui.scroll(-3)
        except Exception:
            pass


# ── Local VNC server ──────────────────────────────────────────────────────────
class VNCServer(threading.Thread):
    def __init__(self, port: int = VNC_PORT):
        super().__init__(daemon=True)
        self.port    = port
        self.running = True
        self._sock: Optional[socket.socket] = None
        self.width, self.height = get_screen_size()

    def run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', self.port))
        self._sock.listen(5)
        log.info(f'VNC server: 127.0.0.1:{self.port}  ({self.width}x{self.height})')
        while self.running:
            try:
                conn, addr = self._sock.accept()
                log.info(f'VNC connection from {addr}')
                RFBClient(conn, addr, self).start()
            except OSError:
                break

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# ── Relay connector ───────────────────────────────────────────────────────────
class RelayConnector:
    """
    Connects to relay at /register/<code>.
    When relay sends {"status": "viewer_connected"}, opens a raw TCP socket
    to the local VNC server and bridges all subsequent bytes bidirectionally
    over the relay WebSocket.
    """

    def __init__(self, relay_url: str, code: str, vnc_port: int,
                 on_status=None):
        self.relay_url = relay_url.rstrip('/')
        self.code      = code
        self.vnc_port  = vnc_port
        self.on_status = on_status or (lambda s: None)
        self._running  = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        if not HAS_WS:
            log.error('websockets package not installed — relay disabled.')
            log.error('Run:  pip install websockets')
            return

        while self._running:
            try:
                asyncio.run(self._connect())
            except Exception as e:
                log.warning(f'Relay error: {e}')
                self.on_status('disconnected')
            if self._running:
                log.info('Reconnecting to relay in 5s…')
                time.sleep(5)

    async def _connect(self):
        url = f'{self.relay_url}/register/{self.code}'
        log.info(f'Connecting to relay: {url}')
        self.on_status('connecting')

        # Support websockets v10/11 (connect) and v12/13 (connect with different kwargs)
        connect_kwargs = dict(
            ping_interval=20,
            ping_timeout=30,
            max_size=16 * 1024 * 1024,
        )
        try:
            ws_connect = websockets.connect(url, **connect_kwargs)
        except TypeError:
            ws_connect = websockets.connect(url)

        async with ws_connect as ws:
            self.on_status('connected')
            log.info(f'Relay connected — code: {self.code}')

            async for raw_msg in ws:
                if not isinstance(raw_msg, str):
                    continue
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                status = msg.get('status', '')
                if status == 'registered':
                    log.info(f'Registered — code: {self.code}')
                elif status == 'viewer_connected':
                    log.info('Viewer connected — opening VNC bridge')
                    self.on_status('viewer_connected')
                    await self._bridge_vnc(ws)
                    self.on_status('connected')
                    log.info('Viewer left — waiting for next viewer')
                elif 'error' in msg:
                    log.error(f'Relay: {msg["error"]}')

    async def _bridge_vnc(self, relay_ws):
        """Bridge the relay WebSocket directly to the local VNC TCP socket."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', self.vnc_port),
                timeout=5.0,
            )
        except Exception as e:
            log.error(f'Cannot reach VNC on :{self.vnc_port} — {e}')
            log.error('Make sure the agent started the VNC server successfully.')
            try:
                await relay_ws.send(json.dumps({'error': 'VNC server not reachable on agent side'}))
            except Exception:
                pass
            return

        log.info(f'VNC bridge open :{self.vnc_port}')

        async def vnc_to_relay():
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await relay_ws.send(data)   # send raw bytes
            except Exception as e:
                log.debug(f'vnc_to_relay ended: {e}')

        async def relay_to_vnc():
            try:
                async for msg in relay_ws:
                    if isinstance(msg, bytes):
                        writer.write(msg)
                        await writer.drain()
                    elif isinstance(msg, str):
                        # control JSON — only stop on viewer_disconnected
                        try:
                            ctrl = json.loads(msg)
                            if ctrl.get('status') == 'viewer_disconnected':
                                break
                        except json.JSONDecodeError:
                            # not JSON — treat as raw text, unlikely but safe
                            writer.write(msg.encode())
                            await writer.drain()
            except Exception as e:
                log.debug(f'relay_to_vnc ended: {e}')

        t1 = asyncio.create_task(vnc_to_relay())
        t2 = asyncio.create_task(relay_to_vnc())
        done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        log.info('VNC bridge closed')


# ── System tray ───────────────────────────────────────────────────────────────
class TrayApp:
    def __init__(self, config: Config, vnc: VNCServer, relay: RelayConnector):
        self.config = config
        self.vnc    = vnc
        self.relay  = relay
        self._status = 'connecting'

    def set_status(self, status: str):
        self._status = status

    def _status_label(self) -> str:
        return {
            'connecting':       'Connecting to relay…',
            'connected':        'Waiting for viewer',
            'viewer_connected': '● Viewer connected',
            'disconnected':     'Relay disconnected',
        }.get(self._status, self._status)

    def run(self):
        if PLATFORM == 'Windows':
            self._windows()
        elif PLATFORM == 'Darwin':
            self._macos()
        else:
            self._linux()

    def _quit(self):
        self.vnc.stop()
        self.relay.stop()
        os._exit(0)

    def _copy_code(self):
        code = self.config.get_code()
        try:
            import pyperclip
            pyperclip.copy(code)
            log.info(f'Code copied: {code}')
        except ImportError:
            if PLATFORM == 'Darwin':
                import subprocess
                subprocess.run(['pbcopy'], input=code.encode())
            elif PLATFORM == 'Windows':
                import subprocess
                subprocess.run(['clip'], input=code.encode())
            else:
                log.info(f'Session code: {code}')

    def _windows(self):
        try:
            import pystray
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            log.warning('pystray/Pillow not installed — falling back to CLI')
            self._cli()
            return

        img  = Image.new('RGB', (64, 64), (0, 229, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([16, 16, 48, 48], fill=(13, 15, 20))
        draw.text((20, 24), 'D', fill=(0, 229, 255))

        code   = self.config.get_code()
        expiry = self.config.expiry_str()

        def make_menu():
            return pystray.Menu(
                pystray.MenuItem(f'Code: {code}', lambda i, _: self._copy_code(), default=True),
                pystray.MenuItem(expiry, None, enabled=False),
                pystray.MenuItem(self._status_label(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('Copy code to clipboard', lambda i, _: self._copy_code()),
                pystray.MenuItem('Quit StarlightRDT', lambda i, _: self._quit()),
            )

        icon = pystray.Icon('StarlightRDT', img, f'StarlightRDT  |  {code}', make_menu())
        log.info(f'Session code: {code}  ({expiry})')
        print(f'\n  ╔══════════════════════════════╗')
        print(f'  ║  StarlightRDT is running         ║')
        print(f'  ║  Session code: {code:<14} ║')
        print(f'  ║  {expiry:<28} ║')
        print(f'  ╚══════════════════════════════╝\n')
        icon.run()

    def _macos(self):
        try:
            import rumps
        except ImportError:
            log.warning('rumps not installed — falling back to CLI')
            self._cli()
            return

        code   = self.config.get_code()
        expiry = self.config.expiry_str()
        cfg    = self.config

        class App(rumps.App):
            def __init__(inner):
                super().__init__('StarlightRDT', title='⬡ StarlightRDT')
                inner.menu = [
                    rumps.MenuItem(f'Code: {code}'),
                    rumps.MenuItem(expiry),
                    None,
                    rumps.MenuItem('Copy code'),
                    rumps.MenuItem('Quit'),
                ]

            @rumps.clicked('Copy code')
            def do_copy(inner, _):
                import subprocess
                subprocess.run(['pbcopy'], input=cfg.get_code().encode())
                rumps.notification('StarlightRDT', 'Code copied', cfg.get_code())

            @rumps.clicked('Quit')
            def do_quit(inner, _):
                rumps.quit_application()

        log.info(f'Session code: {code}  ({expiry})')
        print(f'\n  StarlightRDT running in menu bar — code: {code}\n')
        App().run()

    def _linux(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('AppIndicator3', '0.1')
            from gi.repository import Gtk, AppIndicator3
        except Exception:
            log.warning('GTK/AppIndicator3 not available — falling back to CLI')
            self._cli()
            return

        code   = self.config.get_code()
        expiry = self.config.expiry_str()

        ind = AppIndicator3.Indicator.new(
            'starlight-rdt', 'network-transmit',
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        for label, sensitive in [(f'Code: {code}', False), (expiry, False)]:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        copy_item = Gtk.MenuItem(label='Copy code')
        copy_item.connect('activate', lambda _: self._copy_code())
        menu.append(copy_item)

        quit_item = Gtk.MenuItem(label='Quit StarlightRDT')
        quit_item.connect('activate', lambda _: self._quit())
        menu.append(quit_item)

        menu.show_all()
        ind.set_menu(menu)

        log.info(f'Session code: {code}  ({expiry})')
        print(f'\n  StarlightRDT running in tray — code: {code}\n')
        Gtk.main()

    def _cli(self):
        code   = self.config.get_code()
        expiry = self.config.expiry_str()
        print()
        print('  ╔══════════════════════════════════╗')
        print('  ║       StarlightRDT Agent Running     ║')
        print(f'  ║   Session code : {code:<16} ║')
        print(f'  ║   {expiry:<32} ║')
        print(f'  ║   VNC port     : {self.vnc.port:<16} ║')
        print('  ║   Press Ctrl+C to stop           ║')
        print('  ╚══════════════════════════════════╝')
        print()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='StarlightRDT Agent')
    parser.add_argument('--relay',    default=RELAY_URL,  help='Relay WebSocket URL')
    parser.add_argument('--port',     default=VNC_PORT,   type=int, help='Local VNC port')
    parser.add_argument('--no-tray',  action='store_true', help='CLI mode, no tray icon')
    args = parser.parse_args()

    if not HAS_MSS and not HAS_PIL:
        log.warning('No screen capture library found.')
        log.warning('Install with:  pip install mss')
    if not HAS_INPUT:
        log.warning('pyautogui not found — keyboard/mouse input will be disabled.')
        log.warning('Install with:  pip install pyautogui')
    if not HAS_WS:
        log.error('websockets not installed — cannot connect to relay!')
        log.error('Install with:  pip install websockets')

    cfg   = Config()
    code  = cfg.get_code()
    vnc   = VNCServer(port=args.port)
    tray  = TrayApp(cfg, vnc, None)
    relay = RelayConnector(args.relay, code, args.port, on_status=tray.set_status)
    tray.relay = relay

    vnc.start()
    relay.start()

    if args.no_tray:
        tray._cli()
    else:
        tray.run()

    vnc.stop()
    relay.stop()


if __name__ == '__main__':
    main()