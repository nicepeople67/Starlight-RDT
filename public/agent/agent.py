#!/usr/bin/env python3
import os, sys, socket, struct, threading, time, json, hashlib, secrets, logging, argparse, platform
from pathlib import Path
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('DeltaRDT')

RELAY_URL   = os.environ.get('DELTARDT_RELAY', 'wss://relay.deltardt.app')
VNC_PORT    = int(os.environ.get('DELTARDT_PORT', '5900'))
CODE_TTL    = 7 * 24 * 3600
CONFIG_DIR  = Path.home() / '.deltardt'
CONFIG_FILE = CONFIG_DIR / 'config.json'

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
    import websockets, asyncio
    HAS_WS = True
except ImportError:
    HAS_WS = False

PLATFORM = platform.system()


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
            raw = bytes(img.raw)
            out = bytearray(w * h * 4)
            for i in range(w * h):
                out[i*4]   = raw[i*4+2]
                out[i*4+1] = raw[i*4+1]
                out[i*4+2] = raw[i*4]
                out[i*4+3] = 255
            return bytes(out)
    if HAS_PIL:
        img = ImageGrab.grab(bbox=(x, y, x+w, y+h)).convert('RGBA')
        return img.tobytes()
    return bytes([128, 128, 128, 255] * (w * h))


class Config:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.data = {}
        self._load()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                self.data = json.loads(CONFIG_FILE.read_text())
            except Exception:
                self.data = {}

    def save(self):
        CONFIG_FILE.write_text(json.dumps(self.data, indent=2))

    def get_code(self) -> str:
        now = time.time()
        if 'code' not in self.data or now - self.data.get('code_issued', 0) > CODE_TTL:
            self.data['code'] = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
            self.data['code_issued'] = now
            self.save()
        return self.data['code']

    def code_expires_in(self) -> int:
        issued = self.data.get('code_issued', 0)
        return max(0, int(CODE_TTL - (time.time() - issued)))


RFB_VER    = b'RFB 003.008\n'
SEC_NONE   = 1
PIXEL_FMT  = struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
KEYSYM_MAP = {
    0xff08:'backspace', 0xff09:'tab',    0xff0d:'enter',  0xff1b:'escape',
    0xffff:'delete',    0xff50:'home',   0xff57:'end',     0xff55:'pageup',
    0xff56:'pagedown',  0xff51:'left',   0xff52:'up',      0xff53:'right',
    0xff54:'down',      0xff63:'insert', 0xffe1:'shift',   0xffe2:'shift',
    0xffe3:'ctrl',      0xffe4:'ctrl',   0xffe9:'alt',     0xffea:'alt',
    0xffeb:'winleft',   0xffec:'winright',
    **{0xffbe+i: f'f{i+1}' for i in range(12)},
}


class RFBClient(threading.Thread):
    def __init__(self, conn, addr, server):
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
            while self.running:
                hdr = self._recv(1)
                if not hdr:
                    break
                t = hdr[0]
                if t == 0:
                    self._recv(19)
                elif t == 2:
                    d = self._recv(3)
                    count = struct.unpack('!xH', d)[0]
                    self._recv(count * 4)
                elif t == 3:
                    d = self._recv(9)
                    inc, x, y, w, h = struct.unpack('!BHHHH', d)
                    self._send_update(x, y, w, h)
                elif t == 4:
                    d = self._recv(7)
                    down, _, sym = struct.unpack('!BxxI', d)
                    self._handle_key(down, sym)
                elif t == 5:
                    d = self._recv(5)
                    mask, x, y = struct.unpack('!BHH', d)
                    self._handle_ptr(mask, x, y)
                elif t == 6:
                    d = self._recv(7)
                    length = struct.unpack('!3xI', d)[0]
                    self._recv(length)
        except Exception as e:
            log.debug(f'Client error: {e}')
        finally:
            self.conn.close()
            log.info(f'Client disconnected: {self.addr}')

    def _handshake(self) -> bool:
        self._send(RFB_VER)
        self._recv(12)
        self._send(struct.pack('!BB', 1, SEC_NONE))
        self._recv(1)
        self._send(struct.pack('!I', 0))
        self._recv(1)
        w, h = self.server.width, self.server.height
        name = b'DeltaRDT'
        self._send(struct.pack('!HH', w, h) + PIXEL_FMT + struct.pack('!I', len(name)) + name)
        return True

    def _send_update(self, x, y, w, h):
        sw, sh = self.server.width, self.server.height
        x = max(0, min(x, sw - 1))
        y = max(0, min(y, sh - 1))
        w = max(1, min(w, sw - x))
        h = max(1, min(h, sh - y))
        data = capture_screen(x, y, w, h)
        hdr  = struct.pack('!BBH', 0, 0, 1)
        rhdr = struct.pack('!HHHHi', x, y, w, h, 0)
        self._send(hdr + rhdr + data)

    def _handle_key(self, down: int, sym: int):
        if not HAS_INPUT:
            return
        key = KEYSYM_MAP.get(sym)
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


class VNCServer(threading.Thread):
    def __init__(self, port: int = VNC_PORT):
        super().__init__(daemon=True)
        self.port   = port
        self.width, self.height = get_screen_size()
        self._sock  = None
        self.running = True

    def run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', self.port))
        self._sock.listen(5)
        log.info(f'VNC server on 127.0.0.1:{self.port} ({self.width}x{self.height})')
        while self.running:
            try:
                conn, addr = self._sock.accept()
                log.info(f'VNC client: {addr}')
                RFBClient(conn, addr, self).start()
            except OSError:
                break

    def stop(self):
        self.running = False
        if self._sock:
            self._sock.close()


class RelayConnector:
    def __init__(self, relay_url: str, session_code: str, vnc_port: int):
        self.relay_url    = relay_url
        self.session_code = session_code
        self.vnc_port     = vnc_port
        self._running     = False
        self._thread      = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        if not HAS_WS:
            log.warning('websockets not installed — relay disabled')
            return
        import asyncio
        while self._running:
            try:
                asyncio.run(self._connect())
            except Exception as e:
                log.warning(f'Relay disconnected: {e}')
            if self._running:
                log.info('Reconnecting to relay in 5s…')
                time.sleep(5)

    async def _connect(self):
        import websockets as ws
        reg_url = self.relay_url.rstrip('/') + f'/register/{self.session_code}'
        log.info(f'Connecting to relay: {reg_url}')
        async with ws.connect(reg_url, ping_interval=20, ping_timeout=10) as relay_ws:
            log.info('Relay connected — waiting for viewer')
            async for message in relay_ws:
                if message == 'VIEWER_CONNECTED':
                    log.info('Viewer connected — bridging to VNC')
                    await self._bridge(relay_ws)

    async def _bridge(self, relay_ws):
        import asyncio
        reader, writer = await asyncio.open_connection('127.0.0.1', self.vnc_port)

        async def relay_to_vnc():
            async for data in relay_ws:
                if isinstance(data, bytes):
                    writer.write(data)
                    await writer.drain()

        async def vnc_to_relay():
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await relay_ws.send(data)

        done, pending = await asyncio.wait(
            [asyncio.create_task(relay_to_vnc()), asyncio.create_task(vnc_to_relay())],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        writer.close()


class TrayApp:
    def __init__(self, config: Config, vnc: VNCServer, relay: RelayConnector):
        self.config = config
        self.vnc    = vnc
        self.relay  = relay

    def run(self):
        if PLATFORM == 'Windows':
            self._run_windows()
        elif PLATFORM == 'Darwin':
            self._run_macos()
        else:
            self._run_linux()

    def _make_menu_items(self):
        code    = self.config.get_code()
        expires = self.config.code_expires_in()
        days    = expires // 86400
        hours   = (expires % 86400) // 3600
        return code, f'Refreshes in {days}d {hours}h'

    def _run_windows(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            self._run_cli()
            return

        img  = Image.new('RGB', (64, 64), color=(0, 229, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([16, 16, 48, 48], fill=(13, 15, 20))

        code, expiry = self._make_menu_items()

        def on_quit(icon, _):
            icon.stop()
            self.vnc.stop()
            self.relay.stop()
            os._exit(0)

        def copy_code(icon, _):
            try:
                import pyperclip
                pyperclip.copy(self.config.get_code())
            except Exception:
                pass

        menu = pystray.Menu(
            pystray.MenuItem(f'Code: {code}', copy_code, default=True),
            pystray.MenuItem(expiry, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Copy code', copy_code),
            pystray.MenuItem('Quit DeltaRDT', on_quit),
        )
        icon = pystray.Icon('DeltaRDT', img, 'DeltaRDT', menu)
        log.info(f'Session code: {code}  ({expiry})')
        icon.run()

    def _run_macos(self):
        try:
            import rumps
        except ImportError:
            self._run_cli()
            return

        code, expiry = self._make_menu_items()

        class DeltaApp(rumps.App):
            def __init__(inner, config, relay):
                inner.config = config
                inner.relay  = relay
                super().__init__('DeltaRDT', title='⬡')

            @rumps.clicked(f'Code: {code}')
            def show_code(inner, _):
                c = inner.config.get_code()
                rumps.alert('Your session code', c, ok='Copy')
                try:
                    import subprocess
                    subprocess.run(['pbcopy'], input=c.encode())
                except Exception:
                    pass

            @rumps.clicked('Quit')
            def quit_app(inner, _):
                rumps.quit_application()

        app = DeltaApp(self.config, self.relay)
        app.menu = [f'Code: {code}', expiry, None, 'Quit']
        log.info(f'Session code: {code}  ({expiry})')
        app.run()

    def _run_linux(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('AppIndicator3', '0.1')
            from gi.repository import Gtk, AppIndicator3
        except Exception:
            self._run_cli()
            return

        code, expiry = self._make_menu_items()
        indicator = AppIndicator3.Indicator.new(
            'deltardt', 'network-transmit',
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        code_item = Gtk.MenuItem(label=f'Code: {code}')
        code_item.set_sensitive(False)
        menu.append(code_item)

        exp_item = Gtk.MenuItem(label=expiry)
        exp_item.set_sensitive(False)
        menu.append(exp_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label='Quit DeltaRDT')
        quit_item.connect('activate', lambda _: (self.vnc.stop(), Gtk.main_quit()))
        menu.append(quit_item)

        menu.show_all()
        indicator.set_menu(menu)
        log.info(f'Session code: {code}  ({expiry})')
        Gtk.main()

    def _run_cli(self):
        code, expiry = self._make_menu_items()
        print('=' * 50)
        print(f'  DeltaRDT Agent running')
        print(f'  Session code : {code}')
        print(f'  {expiry}')
        print(f'  VNC port     : {self.vnc.port}')
        print('  Press Ctrl+C to stop')
        print('=' * 50)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description='DeltaRDT Agent')
    parser.add_argument('--relay', default=RELAY_URL)
    parser.add_argument('--port',  default=VNC_PORT, type=int)
    parser.add_argument('--no-tray', action='store_true')
    args = parser.parse_args()

    if not HAS_MSS and not HAS_PIL:
        log.warning('No screen capture library — install mss or Pillow')
    if not HAS_INPUT:
        log.warning('pyautogui not found — keyboard/mouse input disabled')

    cfg   = Config()
    code  = cfg.get_code()
    vnc   = VNCServer(port=args.port)
    relay = RelayConnector(args.relay, code, args.port)

    vnc.start()
    relay.start()

    if args.no_tray:
        TrayApp(cfg, vnc, relay)._run_cli()
    else:
        TrayApp(cfg, vnc, relay).run()

    vnc.stop()
    relay.stop()


if __name__ == '__main__':
    main()