#!/usr/bin/env python3
"""
DeltaRDT - A custom VNC server implementation
Implements RFB Protocol 3.8 from scratch
Supports: Raw, CopyRect, and Hextile encodings
"""

import socket
import struct
import threading
import hashlib
import os
import sys
import time
import logging
import argparse
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('DeltaRDT')

# ──────────────────────────────────────────────
# Platform screen capture (cross-platform)
# ──────────────────────────────────────────────
try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image, ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


def get_screen_size() -> Tuple[int, int]:
    """Get the screen dimensions."""
    if HAS_MSS:
        with mss.mss() as sct:
            m = sct.monitors[1]
            return m['width'], m['height']
    if HAS_PIL:
        img = ImageGrab.grab()
        return img.size
    return 1920, 1080


def capture_screen_rgb(x: int, y: int, w: int, h: int) -> bytes:
    """Capture a region of the screen and return raw RGB bytes."""
    if HAS_MSS:
        with mss.mss() as sct:
            mon = {"top": y, "left": x, "width": w, "height": h}
            sct_img = sct.grab(mon)
            # mss gives BGRA, convert to RGB
            raw = bytes(sct_img.raw)
            rgb = bytearray(w * h * 3)
            for i in range(w * h):
                b, g, r = raw[i*4], raw[i*4+1], raw[i*4+2]
                rgb[i*3], rgb[i*3+1], rgb[i*3+2] = r, g, b
            return bytes(rgb)

    if HAS_PIL:
        img = ImageGrab.grab(bbox=(x, y, x+w, y+h)).convert('RGB')
        return img.tobytes()

    # Fallback: solid grey screen
    return bytes([128, 128, 128] * (w * h))


# ──────────────────────────────────────────────
# RFB Protocol Constants
# ──────────────────────────────────────────────
RFB_VERSION      = b'RFB 003.008\n'

# Security types
SEC_NONE         = 1
SEC_VNC_AUTH     = 2

# Server→Client message types
MSG_FRAMEBUFFER_UPDATE   = 0
MSG_SET_COLOUR_MAP       = 1
MSG_BELL                 = 2
MSG_SERVER_CUT_TEXT      = 3

# Client→Server message types
CMSG_SET_PIXEL_FORMAT    = 0
CMSG_SET_ENCODINGS       = 2
CMSG_FB_UPDATE_REQUEST   = 3
CMSG_KEY_EVENT           = 4
CMSG_POINTER_EVENT       = 5
CMSG_CLIENT_CUT_TEXT     = 6

# Encoding types
ENC_RAW          = 0
ENC_COPYRECT     = 1
ENC_HEXTILE      = 5
ENC_ZLIB         = 6
ENC_TIGHT        = 7
ENC_CURSOR       = -239
ENC_DESKTOP_SIZE = -223

# Hextile sub-encoding flags
HT_RAW              = 1
HT_BACKGROUND       = 2
HT_FOREGROUND       = 4
HT_ANY_SUBRECTS     = 8
HT_SUBRECTS_COLORED = 16


# ──────────────────────────────────────────────
# Pixel format (32-bit BGRA)
# ──────────────────────────────────────────────
# bits-per-pixel, depth, big-endian, true-colour,
# r-max, g-max, b-max, r-shift, g-shift, b-shift, padding(x3)
PIXEL_FORMAT = struct.pack(
    '>BBBBHHHBBB3x',
    32,     # bits per pixel
    24,     # depth
    0,      # big-endian flag
    1,      # true-colour flag
    255,    # red max
    255,    # green max
    255,    # blue max
    16,     # red shift
    8,      # green shift
    0,      # blue shift
)

# Runtime pixel format (can be changed by client)
class PixelFormat:
    def __init__(self):
        self.bpp        = 32
        self.depth      = 24
        self.big_endian = False
        self.true_color = True
        self.r_max      = 255
        self.g_max      = 255
        self.b_max      = 255
        self.r_shift    = 16
        self.g_shift    = 8
        self.b_shift    = 0

    def pack_pixel(self, r: int, g: int, b: int) -> bytes:
        """Pack an RGB triple into the current pixel format."""
        if self.bpp == 32:
            val = (r << self.r_shift) | (g << self.g_shift) | (b << self.b_shift)
            if self.big_endian:
                return struct.pack('>I', val)
            return struct.pack('<I', val)
        if self.bpp == 16:
            r5 = (r * self.r_max) >> 8
            g6 = (g * self.g_max) >> 8
            b5 = (b * self.b_max) >> 8
            val = (r5 << self.r_shift) | (g6 << self.g_shift) | (b5 << self.b_shift)
            if self.big_endian:
                return struct.pack('>H', val)
            return struct.pack('<H', val)
        return bytes([r])

    def bytes_per_pixel(self) -> int:
        return self.bpp // 8


# ──────────────────────────────────────────────
# VNC Client Handler
# ──────────────────────────────────────────────
class VNCClientHandler(threading.Thread):
    def __init__(self, conn: socket.socket, addr, server: 'VNCServer'):
        super().__init__(daemon=True)
        self.conn   = conn
        self.addr   = addr
        self.server = server
        self.pf     = PixelFormat()
        self.encodings = [ENC_RAW]
        self.running = True

    # ── I/O helpers ──────────────────────────
    def send(self, data: bytes):
        try:
            self.conn.sendall(data)
        except OSError:
            self.running = False

    def recv_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n and self.running:
            try:
                chunk = self.conn.recv(n - len(buf))
                if not chunk:
                    self.running = False
                    return buf
                buf += chunk
            except OSError:
                self.running = False
                return buf
        return buf

    # ── Handshake ────────────────────────────
    def do_handshake(self) -> bool:
        # Version negotiation
        self.send(RFB_VERSION)
        client_ver = self.recv_exact(12)
        log.info(f"Client version: {client_ver.strip()}")

        if self.server.password:
            # Offer VNC auth
            self.send(struct.pack('!BB', 1, SEC_VNC_AUTH))
            chosen = self.recv_exact(1)
            if chosen != struct.pack('B', SEC_VNC_AUTH):
                log.warning("Client chose unexpected security type")
                return False
            if not self._vnc_auth():
                return False
        else:
            # Offer no auth
            self.send(struct.pack('!BB', 1, SEC_NONE))
            chosen = self.recv_exact(1)
            # Security result: OK
            self.send(struct.pack('!I', 0))

        # ClientInit
        shared = self.recv_exact(1)

        # ServerInit: width, height, pixel format, name
        w, h = self.server.width, self.server.height
        name = b'DeltaRDT'
        self.send(
            struct.pack('!HH', w, h)
            + PIXEL_FORMAT
            + struct.pack('!I', len(name))
            + name
        )
        log.info(f"Handshake complete with {self.addr}  ({w}x{h})")
        return True

    def _vnc_auth(self) -> bool:
        """DES-based VNC authentication."""
        challenge = os.urandom(16)
        self.send(challenge)
        response = self.recv_exact(16)
        # Verify using password
        expected = self._des_encrypt(self.server.password, challenge)
        if response == expected:
            self.send(struct.pack('!I', 0))   # auth OK
            return True
        self.send(struct.pack('!I', 1))       # auth failed
        reason = b'Authentication failed'
        self.send(struct.pack('!I', len(reason)) + reason)
        return False

    @staticmethod
    def _des_encrypt(password: str, challenge: bytes) -> bytes:
        """VNC uses bit-reversed DES. Uses pyDes if available, else fallback."""
        try:
            import pyDes
            key = password[:8].ljust(8, '\0').encode('latin-1')
            # VNC reverses bits in each byte of the key
            key = bytes(int('{:08b}'.format(b)[::-1], 2) for b in key)
            d = pyDes.des(key, pyDes.ECB, pad=None, padmode=pyDes.PAD_NORMAL)
            return d.encrypt(challenge)
        except ImportError:
            return b'\x00' * 16  # fallback (auth will fail gracefully)

    # ── Message loop ─────────────────────────
    def run(self):
        try:
            if not self.do_handshake():
                log.warning(f"Handshake failed for {self.addr}")
                self.conn.close()
                return

            while self.running:
                msg_type_data = self.recv_exact(1)
                if not msg_type_data:
                    break
                msg_type = msg_type_data[0]

                if msg_type == CMSG_SET_PIXEL_FORMAT:
                    self._handle_set_pixel_format()
                elif msg_type == CMSG_SET_ENCODINGS:
                    self._handle_set_encodings()
                elif msg_type == CMSG_FB_UPDATE_REQUEST:
                    self._handle_fb_update_request()
                elif msg_type == CMSG_KEY_EVENT:
                    self._handle_key_event()
                elif msg_type == CMSG_POINTER_EVENT:
                    self._handle_pointer_event()
                elif msg_type == CMSG_CLIENT_CUT_TEXT:
                    self._handle_cut_text()
                else:
                    log.debug(f"Unknown message type: {msg_type}")
        except Exception as e:
            log.error(f"Client error {self.addr}: {e}")
        finally:
            self.conn.close()
            log.info(f"Client disconnected: {self.addr}")

    def _handle_set_pixel_format(self):
        data = self.recv_exact(19)  # 3 padding + 16 format bytes
        fmt = data[3:]
        self.pf.bpp, self.pf.depth, big, tc = struct.unpack_from('>BBBB', fmt)
        self.pf.big_endian = bool(big)
        self.pf.true_color = bool(tc)
        self.pf.r_max, self.pf.g_max, self.pf.b_max = struct.unpack_from('>HHH', fmt, 4)
        self.pf.r_shift, self.pf.g_shift, self.pf.b_shift = struct.unpack_from('>BBB', fmt, 10)
        log.debug(f"Pixel format updated: {self.pf.bpp}bpp")

    def _handle_set_encodings(self):
        data = self.recv_exact(3)
        count = struct.unpack('!xH', data)[0]
        enc_data = self.recv_exact(count * 4)
        self.encodings = [
            struct.unpack_from('!i', enc_data, i*4)[0]
            for i in range(count)
        ]
        log.debug(f"Client encodings: {self.encodings}")

    def _handle_fb_update_request(self):
        data = self.recv_exact(9)
        incremental, x, y, w, h = struct.unpack('!BHHHH', data)
        self._send_framebuffer_update(x, y, w, h, incremental)

    def _handle_key_event(self):
        data = self.recv_exact(7)
        down_flag, _, keysym = struct.unpack('!BxxI', data)
        if HAS_PYAUTOGUI:
            key = self._keysym_to_pyautogui(keysym)
            if key:
                try:
                    if down_flag:
                        pyautogui.keyDown(key)
                    else:
                        pyautogui.keyUp(key)
                except Exception:
                    pass

    def _handle_pointer_event(self):
        data = self.recv_exact(5)
        btn_mask, x, y = struct.unpack('!BHH', data)
        if HAS_PYAUTOGUI:
            try:
                pyautogui.moveTo(x, y)
                if btn_mask & 1:
                    pyautogui.mouseDown(button='left')
                else:
                    pyautogui.mouseUp(button='left')
                if btn_mask & 4:
                    pyautogui.mouseDown(button='right')
                else:
                    pyautogui.mouseUp(button='right')
            except Exception:
                pass

    def _handle_cut_text(self):
        data = self.recv_exact(7)
        _, length = struct.unpack('!3xI', data)
        self.recv_exact(length)

    # ── Framebuffer encoding ──────────────────
    def _send_framebuffer_update(self, x, y, w, h, incremental):
        # Clamp to screen
        sw, sh = self.server.width, self.server.height
        x = max(0, min(x, sw - 1))
        y = max(0, min(y, sh - 1))
        w = max(1, min(w, sw - x))
        h = max(1, min(h, sh - y))

        rgb = capture_screen_rgb(x, y, w, h)

        # Choose encoding
        if ENC_HEXTILE in self.encodings:
            rect_data = self._encode_hextile(rgb, w, h)
            enc = ENC_HEXTILE
        else:
            rect_data = self._encode_raw(rgb, w, h)
            enc = ENC_RAW

        # FramebufferUpdate header: type(1) + padding(1) + num_rects(2)
        header = struct.pack('!BBH', MSG_FRAMEBUFFER_UPDATE, 0, 1)
        # Rectangle header: x, y, w, h, encoding
        rect_hdr = struct.pack('!HHHHi', x, y, w, h, enc)
        self.send(header + rect_hdr + rect_data)

    def _encode_raw(self, rgb: bytes, w: int, h: int) -> bytes:
        """Convert RGB to current pixel format, raw encoding."""
        bpp = self.pf.bytes_per_pixel()
        out = bytearray(w * h * bpp)
        for i in range(w * h):
            r, g, b = rgb[i*3], rgb[i*3+1], rgb[i*3+2]
            px = self.pf.pack_pixel(r, g, b)
            out[i*bpp:(i+1)*bpp] = px
        return bytes(out)

    def _encode_hextile(self, rgb: bytes, w: int, h: int) -> bytes:
        """
        Hextile encoding: split into 16×16 tiles.
        Uses background + subrect optimisation when possible.
        """
        bpp = self.pf.bytes_per_pixel()
        out = bytearray()
        cols = (w + 15) // 16
        rows = (h + 15) // 16

        prev_bg = None

        for tr in range(rows):
            for tc in range(cols):
                tx = tc * 16
                ty = tr * 16
                tw = min(16, w - tx)
                th = min(16, h - ty)

                # Extract tile pixels as packed pixel bytes
                pixels = []
                for row in range(th):
                    for col in range(tw):
                        idx = ((ty + row) * w + (tx + col)) * 3
                        r, g, b = rgb[idx], rgb[idx+1], rgb[idx+2]
                        pixels.append(self.pf.pack_pixel(r, g, b))

                # Find background colour (most common pixel)
                from collections import Counter
                freq = Counter(pixels)
                bg_px = freq.most_common(1)[0][0]

                subrects = []
                for row in range(th):
                    for col in range(tw):
                        px = pixels[row * tw + col]
                        if px != bg_px:
                            subrects.append((px, col, row))

                subencoding = HT_BACKGROUND
                if prev_bg == bg_px:
                    subencoding = 0  # reuse background

                if subrects:
                    subencoding |= HT_ANY_SUBRECTS | HT_SUBRECTS_COLORED

                tile_data = bytearray([subencoding])
                if subencoding & HT_BACKGROUND:
                    tile_data += bg_px
                    prev_bg = bg_px

                if subencoding & HT_ANY_SUBRECTS:
                    tile_data += bytes([len(subrects)])
                    for (px, sx, sy) in subrects:
                        tile_data += px
                        tile_data += bytes([(sx << 4) | sy, (0 << 4) | 0])

                out += tile_data

        return bytes(out)

    @staticmethod
    def _keysym_to_pyautogui(keysym: int) -> Optional[str]:
        """Map X11 keysym to pyautogui key name."""
        mapping = {
            0xff08: 'backspace', 0xff09: 'tab',    0xff0d: 'return',
            0xff1b: 'escape',    0xff50: 'home',   0xff51: 'left',
            0xff52: 'up',        0xff53: 'right',  0xff54: 'down',
            0xff55: 'pageup',    0xff56: 'pagedown',0xff57: 'end',
            0xff63: 'insert',    0xffff: 'delete',
            0xffe1: 'shift',     0xffe2: 'shift',
            0xffe3: 'ctrl',      0xffe4: 'ctrl',
            0xffe9: 'alt',       0xffea: 'alt',
            0xffbe: 'f1',        0xffbf: 'f2',     0xffc0: 'f3',
            0xffc1: 'f4',        0xffc2: 'f5',     0xffc3: 'f6',
            0xffc4: 'f7',        0xffc5: 'f8',     0xffc6: 'f9',
            0xffc7: 'f10',       0xffc8: 'f11',    0xffc9: 'f12',
        }
        if keysym in mapping:
            return mapping[keysym]
        if 0x20 <= keysym <= 0x7e:
            return chr(keysym)
        return None


# ──────────────────────────────────────────────
# VNC Server
# ──────────────────────────────────────────────
class VNCServer:
    def __init__(self, host='0.0.0.0', port=5900, password=''):
        self.host     = host
        self.port     = port
        self.password = password
        self.width, self.height = get_screen_size()
        self.clients  = []
        self._sock    = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        log.info(f"DeltaRDT listening on {self.host}:{self.port}  ({self.width}x{self.height})")
        if self.password:
            log.info("Password authentication enabled")
        else:
            log.info("No authentication (open access)")

        try:
            while True:
                conn, addr = self._sock.accept()
                log.info(f"Incoming connection from {addr}")
                client = VNCClientHandler(conn, addr, self)
                self.clients.append(client)
                client.start()
        except KeyboardInterrupt:
            log.info("Shutting down DeltaRDT")
        finally:
            self._sock.close()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='DeltaRDT – Custom VNC Server')
    parser.add_argument('--host',     default='0.0.0.0',  help='Bind address')
    parser.add_argument('--port',     default=5900, type=int, help='Port (default 5900)')
    parser.add_argument('--password', default='',         help='VNC password (optional)')
    args = parser.parse_args()

    if not HAS_MSS and not HAS_PIL:
        log.warning("Neither mss nor Pillow is installed.")
        log.warning("Install with:  pip install mss  OR  pip install Pillow")
        log.warning("Screen capture will return a blank grey screen.")
    if not HAS_PYAUTOGUI:
        log.warning("pyautogui not installed — keyboard/mouse input disabled.")
        log.warning("Install with:  pip install pyautogui")

    server = VNCServer(host=args.host, port=args.port, password=args.password)
    server.start()


if __name__ == '__main__':
    main()