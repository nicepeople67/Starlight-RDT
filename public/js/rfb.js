/**
 * DeltaRDT — RFB 3.8 Client
 * Full VNC protocol over WebSocket.
 * Encodings: Raw, RRE, Hextile, DesktopSize
 */

/* ─── State ─── */
let ws       = null;
let rfbState = 'idle';
let ibuf     = new Uint8Array(0);

let canvas  = null;
let ctx     = null;
let fbW     = 0;
let fbH     = 0;
let imgData = null;

let fpsCount   = 0;
let fpsTimer   = null;
let pointerBtns = 0;
let currentScale = 1;

/* ─── Init (called after DOM ready) ─── */
function rfbInit() {
  canvas = document.getElementById('remote-canvas');
  ctx    = canvas.getContext('2d');
}

/* ─── Buffer helpers ─── */
function bufAppend(data) {
  const n = new Uint8Array(ibuf.length + data.byteLength);
  n.set(ibuf);
  n.set(new Uint8Array(data), ibuf.length);
  ibuf = n;
}
function avail()    { return ibuf.length; }
function consume(n) { const c = ibuf.slice(0, n); ibuf = ibuf.slice(n); return c; }
function readU8()   { const v = ibuf[0]; ibuf = ibuf.slice(1); return v; }
function readU16()  { const v = (ibuf[0]<<8)|ibuf[1]; ibuf = ibuf.slice(2); return v; }
function readU32()  { const v = ((ibuf[0]<<24)|(ibuf[1]<<16)|(ibuf[2]<<8)|ibuf[3])>>>0; ibuf = ibuf.slice(4); return v; }
function readS32()  { const v = new DataView(ibuf.buffer, ibuf.byteOffset, 4).getInt32(0, false); ibuf = ibuf.slice(4); return v; }

function sendRaw(data)    { if (ws && ws.readyState === 1) ws.send(data); }
function sendBytes(...b)  { sendRaw(new Uint8Array(b).buffer); }

/* ─── Connect ─── */
function vncConnect() {
  const relay = document.getElementById('vi-relay').value.trim();
  const code  = document.getElementById('vi-code').value.trim().replace(/-/g,'').toUpperCase();

  if (!code) { toast('Enter a session code first', 'err'); return; }
  if (ws) ws.close();

  ibuf     = new Uint8Array(0);
  rfbState = 'version';

  const url = relay.replace(/\/$/, '') + '/connect/' + code;
  document.querySelector('#vbar .vbar-info').textContent = url;

  setVStatus('ing', 'Connecting…');
  setVLoading(true, 'Contacting relay…');
  setSplash(true);

  document.getElementById('vbtn-connect').disabled = true;
  document.getElementById('vbtn-dc').disabled = false;

  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => setVLoading(true, 'Waiting for agent…');

  ws.onmessage = e => {
    if (typeof e.data === 'string') {
      try {
        const msg = JSON.parse(e.data);
        if (msg.error) {
          setVStatus('err', 'Error');
          setVLoading(false);
          toast('Relay: ' + msg.error, 'err');
          resetVUI();
          ws.close();
        }
      } catch (_) {}
      return;
    }
    bufAppend(e.data);
    try { pump(); } catch(err) { toast('Protocol error: ' + err.message, 'err'); }
  };

  ws.onerror = () => {
    setVStatus('err', 'Error');
    setVLoading(false);
    toast('Cannot reach relay — check the relay URL', 'err');
    resetVUI();
  };

  ws.onclose = () => {
    if (rfbState !== 'idle') { setVStatus('err', 'Disconnected'); toast('Connection closed'); }
    rfbState = 'idle';
    setVLoading(false);
    setSplash(true);
    resetVUI();
    if (fpsTimer) { clearInterval(fpsTimer); fpsTimer = null; }
  };
}

function vncDisconnect() {
  if (ws) ws.close();
  rfbState = 'idle';
  setVStatus('', 'Disconnected');
  setVLoading(false);
  setSplash(true);
  resetVUI();
}

/* ─── RFB state machine ─── */
function pump() {
  for (let i = 0; i < 500; i++) {
    const before = ibuf.length;
    if      (rfbState === 'version')    { if (!doVersion())    break; }
    else if (rfbState === 'security')   { if (!doSecurity())   break; }
    else if (rfbState === 'secresult')  { if (!doSecResult())  break; }
    else if (rfbState === 'clientinit') { doClientInit(); }
    else if (rfbState === 'serverinit') { if (!doServerInit()) break; }
    else if (rfbState === 'active')     { if (!doMessage())    break; }
    else break;
    if (ibuf.length === before) break;
  }
}

function doVersion() {
  if (avail() < 12) return false;
  consume(12);
  sendRaw(new TextEncoder().encode('RFB 003.008\n'));
  rfbState = 'security';
  return true;
}

function doSecurity() {
  if (avail() < 2) return false;
  const n = readU8();
  if (n === 0) { consume(avail()); toast('Server rejected connection', 'err'); return false; }
  if (avail() < n) return false;
  const types = Array.from(consume(n));
  const chosen = types.includes(1) ? 1 : (types.includes(2) ? 2 : types[0]);
  sendBytes(chosen);
  rfbState = 'secresult';
  return true;
}

function doSecResult() {
  if (avail() < 4) return false;
  const res = readU32();
  if (res !== 0) {
    let m = 'Authentication failed';
    if (avail() >= 4) { const l = readU32(); if (avail() >= l) m = new TextDecoder().decode(consume(l)); }
    toast(m, 'err');
    ws.close();
    return false;
  }
  rfbState = 'clientinit';
  return true;
}

function doClientInit() {
  sendBytes(1); // shared flag
  rfbState = 'serverinit';
}

function doServerInit() {
  if (avail() < 24) return false;
  fbW = readU16(); fbH = readU16();
  consume(16); // server pixel format (we'll set our own)
  const nl = readU32();
  if (avail() < nl) return false;
  const name = new TextDecoder().decode(consume(nl));

  canvas.width  = fbW;
  canvas.height = fbH;
  imgData = ctx.createImageData(fbW, fbH);
  for (let i = 3; i < imgData.data.length; i += 4) imgData.data[i] = 255;
  ctx.putImageData(imgData, 0, 0);

  setSplash(false);
  setVLoading(false);
  setVStatus('conn', 'Connected');
  document.getElementById('vsess-label').textContent  = name || 'Remote Desktop';
  document.getElementById('vsess-detail').textContent = `${fbW}×${fbH}`;

  fitScreen();
  bindInput();
  sendPixelFormat();
  sendEncodings();
  reqUpdate(false);
  saveRecent(name);

  fpsTimer = setInterval(() => {
    document.getElementById('vfps').textContent = fpsCount + ' fps';
    fpsCount = 0;
  }, 1000);

  rfbState = 'active';
  return true;
}

/* ─── Server messages ─── */
function doMessage() {
  if (avail() < 1) return false;
  const t = ibuf[0];
  if (t === 0) return doFBUpdate();
  if (t === 2) { consume(1); return true; } // Bell
  if (t === 3) return doCutText();
  consume(1);
  return true;
}

function doFBUpdate() {
  if (avail() < 4) return false;
  const nr = (ibuf[2]<<8)|ibuf[3];
  consume(4);

  for (let i = 0; i < nr; i++) {
    if (avail() < 12) return false;
    const x = readU16(), y = readU16(), w = readU16(), h = readU16();
    const enc = readS32();

    if (enc === 0) {         // Raw
      const b = w*h*4;
      if (avail() < b) return false;
      copyRaw(consume(b), x, y, w, h);

    } else if (enc === 2) {  // RRE
      if (avail() < 8) return false;
      const ns = readU32(), bg = consume(4);
      fillRect(x, y, w, h, bg[2], bg[1], bg[0]);
      if (avail() < ns*12) return false;
      for (let s = 0; s < ns; s++) {
        const fg = consume(4);
        const sx = readU16(), sy = readU16(), sw = readU16(), sh = readU16();
        fillRect(x+sx, y+sy, sw, sh, fg[2], fg[1], fg[0]);
      }

    } else if (enc === 5) {  // Hextile
      if (!doHextile(x, y, w, h)) return false;

    } else if (enc === -223) { // DesktopSize
      fbW = w; fbH = h;
      canvas.width = w; canvas.height = h;
      imgData = ctx.createImageData(w, h);
      document.getElementById('vsess-detail').textContent = `${w}×${h}`;
      fitScreen();
    }
  }

  ctx.putImageData(imgData, 0, 0);
  fpsCount++;
  reqUpdate(true);
  return true;
}

function copyRaw(d, x, y, w, h) {
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      const si = (r*w + c)*4;
      const di = ((y+r)*fbW + (x+c))*4;
      imgData.data[di]   = d[si+2]; // R (server sends BGR0)
      imgData.data[di+1] = d[si+1]; // G
      imgData.data[di+2] = d[si];   // B
      imgData.data[di+3] = 255;
    }
  }
}

function fillRect(x, y, w, h, r, g, b) {
  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      const di = ((y+row)*fbW + (x+col))*4;
      imgData.data[di]   = r;
      imgData.data[di+1] = g;
      imgData.data[di+2] = b;
      imgData.data[di+3] = 255;
    }
  }
}

function doHextile(rx, ry, rw, rh) {
  let bgR=0, bgG=0, bgB=0, fgR=0, fgG=0, fgB=0;
  const cols = Math.ceil(rw/16), rows = Math.ceil(rh/16);

  for (let tr = 0; tr < rows; tr++) {
    for (let tc = 0; tc < cols; tc++) {
      if (avail() < 1) return false;
      const se = readU8();
      const tx = rx + tc*16, ty = ry + tr*16;
      const tw = Math.min(16, rw - tc*16), th = Math.min(16, rh - tr*16);

      if (se & 1) { // Raw tile
        const b = tw*th*4;
        if (avail() < b) return false;
        copyRaw(consume(b), tx, ty, tw, th);
        continue;
      }
      if (se & 2)  { if (avail() < 4) return false; const c = consume(4); bgR=c[2]; bgG=c[1]; bgB=c[0]; }
      if (se & 4)  { if (avail() < 4) return false; const c = consume(4); fgR=c[2]; fgG=c[1]; fgB=c[0]; }
      fillRect(tx, ty, tw, th, bgR, bgG, bgB);
      if (se & 8) {
        if (avail() < 1) return false;
        const ns = readU8();
        for (let s = 0; s < ns; s++) {
          let r = fgR, g = fgG, b = fgB;
          if (se & 16) { if (avail() < 4) return false; const c = consume(4); r=c[2]; g=c[1]; b=c[0]; }
          if (avail() < 2) return false;
          const xy = readU8(), wh = readU8();
          const sx = (xy>>4)&0xf, sy = xy&0xf;
          const sw = ((wh>>4)&0xf)+1, sh = (wh&0xf)+1;
          fillRect(tx+sx, ty+sy, sw, sh, r, g, b);
        }
      }
    }
  }
  return true;
}

function doCutText() {
  if (avail() < 8) return false;
  consume(4);
  const l = readU32();
  if (avail() < l) return false;
  document.getElementById('vclip-text').value = new TextDecoder().decode(consume(l));
  return true;
}

/* ─── Send messages ─── */
function sendPixelFormat() {
  // Request 32bpp BGR0 little-endian (standard VNC format)
  const b = new DataView(new ArrayBuffer(20));
  b.setUint8(0, 0);           // message type
  b.setUint8(4, 32);          // bits-per-pixel
  b.setUint8(5, 24);          // depth
  b.setUint8(6, 0);           // big-endian flag
  b.setUint8(7, 1);           // true-colour flag
  b.setUint16(8,  255, false); // red-max
  b.setUint16(10, 255, false); // green-max
  b.setUint16(12, 255, false); // blue-max
  b.setUint8(14, 16);          // red-shift
  b.setUint8(15, 8);           // green-shift
  b.setUint8(16, 0);           // blue-shift
  sendRaw(b.buffer);
}

function sendEncodings() {
  const encs = [5, 2, 0, -223]; // Hextile, RRE, Raw, DesktopSize
  const b = new DataView(new ArrayBuffer(4 + encs.length*4));
  b.setUint8(0, 2);
  b.setUint16(2, encs.length, false);
  encs.forEach((e, i) => b.setInt32(4 + i*4, e, false));
  sendRaw(b.buffer);
}

function reqUpdate(inc) {
  const b = new DataView(new ArrayBuffer(10));
  b.setUint8(0, 3);
  b.setUint8(1, inc ? 1 : 0);
  b.setUint16(2, 0, false);
  b.setUint16(4, 0, false);
  b.setUint16(6, fbW, false);
  b.setUint16(8, fbH, false);
  sendRaw(b.buffer);
}

function sendKeyMsg(sym, down) {
  const b = new DataView(new ArrayBuffer(8));
  b.setUint8(0, 4);
  b.setUint8(1, down ? 1 : 0);
  b.setUint32(4, sym, false);
  sendRaw(b.buffer);
}

function sendSpecialKey(sym, down = true) {
  sendKeyMsg(sym, down);
  if (down) setTimeout(() => sendKeyMsg(sym, false), 80);
}

/* ─── Convenience actions ─── */
function sendCAD() {
  if (rfbState !== 'active') { toast('Not connected', 'err'); return; }
  [0xffe3, 0xffe9, 0xffff].forEach(k => sendKeyMsg(k, true));
  setTimeout(() => [0xffff, 0xffe9, 0xffe3].forEach(k => sendKeyMsg(k, false)), 80);
}

function sendClipboard() {
  const text = document.getElementById('vclip-text').value;
  const enc = new TextEncoder().encode(text);
  const b = new DataView(new ArrayBuffer(8 + enc.length));
  b.setUint8(0, 6);
  b.setUint32(4, enc.length, false);
  new Uint8Array(b.buffer, 8).set(enc);
  sendRaw(b.buffer);
  toast('Clipboard sent to remote', 'good');
}

/* ─── Input binding ─── */
const KEY_MAP = {
  Backspace:0xff08, Tab:0xff09, Enter:0xff0d, Escape:0xff1b, Delete:0xffff,
  Home:0xff50, End:0xff57, PageUp:0xff55, PageDown:0xff56, Insert:0xff63,
  ArrowLeft:0xff51, ArrowUp:0xff52, ArrowRight:0xff53, ArrowDown:0xff54,
  F1:0xffbe,  F2:0xffbf,  F3:0xffc0,  F4:0xffc1,  F5:0xffc2,  F6:0xffc3,
  F7:0xffc4,  F8:0xffc5,  F9:0xffc6,  F10:0xffc7, F11:0xffc8, F12:0xffc9,
  ShiftLeft:0xffe1,   ShiftRight:0xffe2,
  ControlLeft:0xffe3, ControlRight:0xffe4,
  AltLeft:0xffe9,     AltRight:0xffea,
  MetaLeft:0xffeb,    MetaRight:0xffec,
  CapsLock:0xffe5,
};

function keyToSym(e) {
  if (KEY_MAP[e.code]) return KEY_MAP[e.code];
  if (e.key.length === 1) return e.key.codePointAt(0);
  return null;
}

function bindInput() {
  canvas.onmousemove   = onMM;
  canvas.onmousedown   = onMD;
  canvas.onmouseup     = onMU;
  canvas.oncontextmenu = e => e.preventDefault();
  canvas.onwheel       = onWheel;
  window.onkeydown     = onKD;
  window.onkeyup       = onKU;
}

function canvasCoords(e) {
  const r = canvas.getBoundingClientRect();
  return {
    x: Math.round((e.clientX - r.left) * (fbW / r.width)),
    y: Math.round((e.clientY - r.top)  * (fbH / r.height)),
  };
}

function sendPointer(e) {
  const {x, y} = canvasCoords(e);
  const b = new DataView(new ArrayBuffer(6));
  b.setUint8(0, 5);
  b.setUint8(1, pointerBtns);
  b.setUint16(2, x, false);
  b.setUint16(4, y, false);
  sendRaw(b.buffer);
}

function onMM(e) { if (rfbState === 'active') sendPointer(e); }
function onMD(e) { if (rfbState !== 'active') return; pointerBtns |= (e.button === 2 ? 4 : 1<<e.button);  sendPointer(e); }
function onMU(e) { if (rfbState !== 'active') return; pointerBtns &= ~(e.button === 2 ? 4 : 1<<e.button); sendPointer(e); }

function onWheel(e) {
  if (rfbState !== 'active') return;
  e.preventDefault();
  const {x, y} = canvasCoords(e);
  const btn = e.deltaY < 0 ? 8 : 16;
  const b = new DataView(new ArrayBuffer(6));
  b.setUint8(0, 5); b.setUint8(1, btn); b.setUint16(2, x, false); b.setUint16(4, y, false);
  sendRaw(b.buffer);
  b.setUint8(1, 0);
  sendRaw(b.buffer);
}

function onKD(e) {
  if (rfbState !== 'active') return;
  if (e.target !== document.body && e.target !== canvas) return;
  const s = keyToSym(e);
  if (!s) return;
  e.preventDefault();
  sendKeyMsg(s, true);
}

function onKU(e) {
  if (rfbState !== 'active') return;
  const s = keyToSym(e);
  if (!s) return;
  sendKeyMsg(s, false);
}

/* ─── UI helpers ─── */
function setVStatus(state, text) {
  document.getElementById('vdot').className = state;
  document.getElementById('vstatus-text').textContent = text;
}

function setVLoading(vis, msg = 'Connecting…') {
  document.getElementById('vload-msg').textContent = msg;
  document.getElementById('vloading').classList.toggle('vis', vis);
}

function setSplash(vis) {
  document.getElementById('vsplash').style.opacity = vis ? '1' : '0';
  canvas.style.display = vis ? 'none' : 'block';
}

function resetVUI() {
  document.getElementById('vbtn-connect').disabled = false;
  document.getElementById('vbtn-dc').disabled = true;
  document.getElementById('vsess-label').textContent = 'No session';
  document.getElementById('vsess-detail').textContent = '';
}

function fitScreen() {
  const wrap = document.getElementById('vcanvas-wrap');
  const maxW = wrap.clientWidth  - 32;
  const maxH = wrap.clientHeight - 32;
  if (!fbW || !fbH) return;
  currentScale = Math.min(maxW/fbW, maxH/fbH, 1);
  canvas.style.width  = Math.round(fbW * currentScale) + 'px';
  canvas.style.height = Math.round(fbH * currentScale) + 'px';
}

function toggleFS() {
  if (!document.fullscreenElement) document.getElementById('vmain').requestFullscreen();
  else document.exitFullscreen();
}

function toggleClip() {
  document.getElementById('vclip').classList.toggle('open');
}

function setQuality(_q) {
  // Quality hint — adjust update request rate in a real implementation
}

window.addEventListener('resize', () => { if (rfbState === 'active') fitScreen(); });

function saveRecent(name) {
  const code  = document.getElementById('vi-code').value.trim();
  const relay = document.getElementById('vi-relay').value.trim();
  if (typeof addSession === 'function') addSession(code, name || 'Remote Desktop');
  if (typeof renderConnectRecent === 'function') renderConnectRecent();
}

window.addEventListener('DOMContentLoaded', () => {
  rfbInit();
  document.querySelectorAll('#vconn-panel input').forEach(inp => {
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') vncConnect(); });
  });
});