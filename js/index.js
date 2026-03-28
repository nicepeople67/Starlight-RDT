
function showPage(name, linkEl) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('p-' + name).classList.add('active');
  document.querySelectorAll('nav ul a').forEach(a => a.classList.remove('active'));
  if (linkEl) linkEl.classList.add('active');
  window.scrollTo(0, 0);
}

function scrollTo(id) {
  setTimeout(() => {
    const el = document.querySelector(id);
    if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
  }, 50);
}

function openDlModal() {
  document.getElementById('dl-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeDlModal(e) {
  if (e.target === document.getElementById('dl-modal')) {
    document.getElementById('dl-modal').classList.remove('open');
    document.body.style.overflow = '';
  }
}

function switchTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('on'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('on'));
  btn.classList.add('on');
  const tc = document.getElementById('tc-' + name);
  tc.classList.add('on');
  tc.querySelectorAll('.ss').forEach((s, i) => {
    s.classList.remove('vis');
    setTimeout(() => s.classList.add('vis'), i * 110);
  });
}

function toggleFaq(btn) {
  const item = btn.closest('.faq-item');
  const open = item.classList.contains('open');
  document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('open'));
  if (!open) item.classList.add('open');
}

function copyCode(btn) {
  const block = btn.closest('.code-block');
  const text = Array.from(block.childNodes)
    .filter(n => n.nodeType === 3 || n.nodeName !== 'BUTTON')
    .map(n => n.textContent).join('').trim();
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'copied'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('ok'); }, 2000);
  }).catch(() => { btn.textContent = '✓'; setTimeout(() => btn.textContent = 'copy', 2000); });
}

function toast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'vis' + (type ? ' ' + type : '');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('vis'), 4000);
}

const obs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('vis');
      e.target.querySelectorAll('.ss').forEach((s, i) =>
        setTimeout(() => s.classList.add('vis'), i * 110));
    }
  });
}, { threshold: 0.07 });

document.querySelectorAll('.reveal, .stagger').forEach(el => obs.observe(el));

setTimeout(() => {
  document.querySelectorAll('#tc-win .ss').forEach((s, i) =>
    setTimeout(() => s.classList.add('vis'), 400 + i * 110));
}, 200);

window.addEventListener('scroll', () => {
  const y = window.scrollY + 80;
  document.querySelectorAll('[id]').forEach(s => {
    if (s.offsetTop <= y) {
      document.querySelectorAll('nav ul a').forEach(a => {
        a.style.color = a.getAttribute('href') === '#' + s.id ? 'var(--white)' : '';
      });
    }
  });
}, { passive: true });
let ws = null;
let rfbState = 'idle';
let ibuf = new Uint8Array(0);
let canvas = document.getElementById('remote-canvas');
let ctx = canvas.getContext('2d');
let fbW = 0, fbH = 0, imgData = null;
let fpsCount = 0, fpsTimer = 0;
let pingStart = 0, latency = 0;
let pointerBtns = 0;
let recentSessions = JSON.parse(localStorage.getItem('deltardt_recent') || '[]');
let currentScale = 1;

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
  document.getElementById('vsplash').style.pointerEvents = vis ? 'none' : 'none';
  canvas.style.display = vis ? 'none' : 'block';
}

function append(data) {
  const n = new Uint8Array(ibuf.length + data.byteLength);
  n.set(ibuf); n.set(new Uint8Array(data), ibuf.length); ibuf = n;
}
function avail() { return ibuf.length; }
function consume(n) { const c = ibuf.slice(0, n); ibuf = ibuf.slice(n); return c; }
function readU8()  { const v = ibuf[0]; ibuf = ibuf.slice(1); return v; }
function readU16() { const v = (ibuf[0]<<8)|ibuf[1]; ibuf = ibuf.slice(2); return v; }
function readU32() { const v = ((ibuf[0]<<24)|(ibuf[1]<<16)|(ibuf[2]<<8)|ibuf[3])>>>0; ibuf = ibuf.slice(4); return v; }
function readS32() { const v = new DataView(ibuf.buffer, ibuf.byteOffset, 4).getInt32(0, false); ibuf = ibuf.slice(4); return v; }

function sendRaw(data) { if (ws && ws.readyState === 1) ws.send(data); }
function sendBytes(...b) { sendRaw(new Uint8Array(b).buffer); }

function vncConnect() {
  const relay = document.getElementById('vi-relay').value.trim();
  const code  = document.getElementById('vi-code').value.trim().replace(/-/g, '').toUpperCase();

  if (!code) { toast('Enter a session code first', 'err'); return; }

  if (ws) { ws.close(); }
  ibuf = new Uint8Array(0);
  rfbState = 'version';

  const url = relay.endsWith('/') ? relay + 'vnc/' + code : relay + '/vnc/' + code;
  document.querySelector('#vbar .vbar-info').textContent = url;

  setVStatus('ing', 'Connecting…');
  setVLoading(true, 'Opening WebSocket to relay…');
  setSplash(true);

  document.getElementById('vbtn-connect').disabled = true;
  document.getElementById('vbtn-dc').disabled = false;

  ws = new WebSocket(url, ['binary']);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => setVLoading(true, 'RFB handshake…');
  ws.onmessage = e => { append(e.data); try { pump(); } catch(err) { toast('Protocol error: ' + err.message, 'err'); console.error(err); } };
  ws.onerror = () => { setVStatus('err', 'Error'); setVLoading(false); toast('WebSocket error — check relay URL and code', 'err'); resetVUI(); };
  ws.onclose = () => {
    if (rfbState !== 'idle') { setVStatus('err', 'Disconnected'); toast('Connection closed'); }
    rfbState = 'idle'; setVLoading(false); setSplash(true); resetVUI();
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

function resetVUI() {
  document.getElementById('vbtn-connect').disabled = false;
  document.getElementById('vbtn-dc').disabled = true;
  document.getElementById('vsess-label').textContent = 'No session';
  document.getElementById('vsess-detail').textContent = '';
}

function pump() {
  for (let i = 0; i < 200; i++) {
    const before = ibuf.length;
    if      (rfbState === 'version')    { if (!doVersion())   break; }
    else if (rfbState === 'security')   { if (!doSecurity())  break; }
    else if (rfbState === 'secresult')  { if (!doSecResult()) break; }
    else if (rfbState === 'clientinit') { doClientInit(); }
    else if (rfbState === 'serverinit') { if (!doServerInit()) break; }
    else if (rfbState === 'active')     { if (!doMessage())   break; }
    else break;
    if (ibuf.length === before) break;
  }
}

function doVersion() {
  if (avail() < 12) return false;
  const ver = new TextDecoder().decode(consume(12));
  sendRaw(new TextEncoder().encode('RFB 003.008\n'));
  rfbState = 'security'; return true;
}

function doSecurity() {
  if (avail() < 2) return false;
  const n = readU8();
  if (n === 0) { consume(avail()); toast('Server rejected connection', 'err'); return false; }
  if (avail() < n) return false;
  const types = Array.from(consume(n));
  const chosen = types.includes(1) ? 1 : types.includes(2) ? 2 : types[0];
  sendBytes(chosen);
  rfbState = 'secresult'; return true;
}

function doSecResult() {
  if (avail() < 4) return false;
  const res = readU32();
  if (res !== 0) { let m = 'Auth failed'; if (avail() >= 4) { const l = readU32(); if (avail() >= l) m = new TextDecoder().decode(consume(l)); } toast(m, 'err'); ws.close(); return false; }
  rfbState = 'clientinit'; return true;
}

function doClientInit() { sendBytes(1); rfbState = 'serverinit'; }

function doServerInit() {
  if (avail() < 24) return false;
  fbW = readU16(); fbH = readU16();
  consume(16);
  const nl = readU32();
  if (avail() < nl) return false;
  const name = new TextDecoder().decode(consume(nl));

  canvas.width = fbW; canvas.height = fbH;
  imgData = ctx.createImageData(fbW, fbH);
  for (let i = 3; i < imgData.data.length; i += 4) imgData.data[i] = 255;
  ctx.putImageData(imgData, 0, 0);

  setSplash(false);
  setVLoading(false);
  setVStatus('conn', 'Connected');
  document.getElementById('vsess-label').textContent = name || 'Remote Desktop';
  document.getElementById('vsess-detail').textContent = `${fbW}×${fbH}`;
  fitScreen();
  bindInput();
  sendPixelFormat();
  sendEncodings();
  reqUpdate(false);

  const code = document.getElementById('vi-code').value.trim();
  const relay = document.getElementById('vi-relay').value.trim();
  recentSessions = recentSessions.filter(r => r.code !== code);
  recentSessions.unshift({ code, relay, name: name || 'Remote Desktop', ts: Date.now() });
  recentSessions = recentSessions.slice(0, 8);
  localStorage.setItem('deltardt_recent', JSON.stringify(recentSessions));
  renderRecent();

  fpsTimer = setInterval(() => {
    document.getElementById('vfps').textContent = fpsCount + ' fps';
    fpsCount = 0;
  }, 1000);

  rfbState = 'active'; return true;
}

function doMessage() {
  if (avail() < 1) return false;
  const t = ibuf[0];
  if (t === 0) return doFBUpdate();
  if (t === 2) { consume(1); return true; }
  if (t === 3) return doCutText();
  consume(1); return true;
}

function doFBUpdate() {
  if (avail() < 4) return false;
  const nr = (ibuf[2]<<8)|ibuf[3]; consume(4);
  for (let i = 0; i < nr; i++) {
    if (avail() < 12) return false;
    const x = readU16(), y = readU16(), w = readU16(), h = readU16();
    const enc = readS32();
    if (enc === 0) {
      const b = w*h*4; if (avail() < b) return false;
      copyRaw(consume(b), x, y, w, h);
    } else if (enc === 2) {
      if (avail() < 8) return false;
      const ns = readU32(), bg = consume(4);
      fillRect(x,y,w,h,bg[2],bg[1],bg[0]);
      if (avail() < ns*12) return false;
      for (let s=0;s<ns;s++) { const fg=consume(4),sx=readU16(),sy=readU16(),sw=readU16(),sh=readU16(); fillRect(x+sx,y+sy,sw,sh,fg[2],fg[1],fg[0]); }
    } else if (enc === 5) {
      if (!doHextile(x,y,w,h)) return false;
    } else if (enc === -223) {
      fbW=w; fbH=h; canvas.width=w; canvas.height=h;
      imgData=ctx.createImageData(w,h);
      document.getElementById('vsess-detail').textContent=`${w}×${h}`;
      fitScreen();
    }
  }
  ctx.putImageData(imgData, 0, 0); fpsCount++;
  reqUpdate(true); return true;
}

function copyRaw(d, x, y, w, h) {
  for (let r=0;r<h;r++) for (let c=0;c<w;c++) {
    const si=(r*w+c)*4, di=((y+r)*fbW+(x+c))*4;
    imgData.data[di]=d[si+2]; imgData.data[di+1]=d[si+1]; imgData.data[di+2]=d[si]; imgData.data[di+3]=255;
  }
}

function fillRect(x,y,w,h,r,g,b) {
  for (let row=0;row<h;row++) for (let col=0;col<w;col++) {
    const di=((y+row)*fbW+(x+col))*4;
    imgData.data[di]=r; imgData.data[di+1]=g; imgData.data[di+2]=b; imgData.data[di+3]=255;
  }
}

function doHextile(rx,ry,rw,rh) {
  let bgR=0,bgG=0,bgB=0,fgR=0,fgG=0,fgB=0;
  const cols=Math.ceil(rw/16), rows=Math.ceil(rh/16);
  for (let tr=0;tr<rows;tr++) for (let tc=0;tc<cols;tc++) {
    if (avail()<1) return false;
    const se=readU8(), tx=rx+tc*16, ty=ry+tr*16, tw=Math.min(16,rw-tc*16), th=Math.min(16,rh-tr*16);
    if (se&1) { const b=tw*th*4; if(avail()<b) return false; copyRaw(consume(b),tx,ty,tw,th); continue; }
    if (se&2) { if(avail()<4) return false; const c=consume(4); bgR=c[2]; bgG=c[1]; bgB=c[0]; }
    if (se&4) { if(avail()<4) return false; const c=consume(4); fgR=c[2]; fgG=c[1]; fgB=c[0]; }
    fillRect(tx,ty,tw,th,bgR,bgG,bgB);
    if (se&8) {
      if(avail()<1) return false;
      const ns=readU8();
      for (let s=0;s<ns;s++) {
        let r=fgR,g=fgG,b=fgB;
        if (se&16) { if(avail()<4) return false; const c=consume(4); r=c[2]; g=c[1]; b=c[0]; }
        if(avail()<2) return false;
        const xy=readU8(),wh=readU8(),sx=(xy>>4)&0xf,sy=xy&0xf,sw=((wh>>4)&0xf)+1,sh=(wh&0xf)+1;
        fillRect(tx+sx,ty+sy,sw,sh,r,g,b);
      }
    }
  }
  return true;
}

function doCutText() {
  if(avail()<8) return false;
  consume(4); const l=readU32(); if(avail()<l) return false;
  document.getElementById('vclip-text').value = new TextDecoder().decode(consume(l));
  return true;
}

function sendPixelFormat() {
  const b = new DataView(new ArrayBuffer(20));
  b.setUint8(0,0); b.setUint8(4,32); b.setUint8(5,24);
  b.setUint8(6,0); b.setUint8(7,1);
  b.setUint16(8,255,false); b.setUint16(10,255,false); b.setUint16(12,255,false);
  b.setUint8(14,16); b.setUint8(15,8); b.setUint8(16,0);
  sendRaw(b.buffer);
}

function sendEncodings() {
  const encs = [5,2,0,-223];
  const b = new DataView(new ArrayBuffer(4+encs.length*4));
  b.setUint8(0,2); b.setUint16(2,encs.length,false);
  encs.forEach((e,i) => b.setInt32(4+i*4,e,false));
  sendRaw(b.buffer);
}

function reqUpdate(inc) {
  const b = new DataView(new ArrayBuffer(10));
  b.setUint8(0,3); b.setUint8(1,inc?1:0);
  b.setUint16(2,0,false); b.setUint16(4,0,false);
  b.setUint16(6,fbW,false); b.setUint16(8,fbH,false);
  sendRaw(b.buffer);
}

function sendSpecialKey(sym, down=true) {
  const b = new DataView(new ArrayBuffer(8));
  b.setUint8(0,4); b.setUint8(1,down?1:0); b.setUint32(4,sym,false);
  sendRaw(b.buffer);
  if (down) setTimeout(() => sendSpecialKey(sym,false), 80);
}

function sendCAD() {
  if (rfbState!=='active') { toast('Not connected','err'); return; }
  const keys = [0xffe3,0xffe9,0xffff];
  keys.forEach(k => sendSpecialKey(k,true));
  setTimeout(() => keys.reverse().forEach(k => sendSpecialKey(k,false)), 80);
}

function sendClipboard() {
  const text = document.getElementById('vclip-text').value;
  const enc = new TextEncoder().encode(text);
  const b = new DataView(new ArrayBuffer(8+enc.length));
  b.setUint8(0,6); b.setUint32(4,enc.length,false);
  new Uint8Array(b.buffer,8).set(enc);
  sendRaw(b.buffer);
  toast('Clipboard sent to remote', 'good');
}

function bindInput() {
  canvas.onmousemove = onMM;
  canvas.onmousedown = onMD;
  canvas.onmouseup   = onMU;
  canvas.oncontextmenu = e => e.preventDefault();
  canvas.onwheel = onWheel;
  window.onkeydown = onKD;
  window.onkeyup   = onKU;
}

function cc(e) {
  const r = canvas.getBoundingClientRect();
  return { x: Math.round((e.clientX-r.left)*(fbW/r.width)), y: Math.round((e.clientY-r.top)*(fbH/r.height)) };
}

function sendPtr(e) {
  const {x,y} = cc(e);
  const b = new DataView(new ArrayBuffer(6));
  b.setUint8(0,5); b.setUint8(1,pointerBtns); b.setUint16(2,x,false); b.setUint16(4,y,false);
  sendRaw(b.buffer);
}

function onMM(e) { if(rfbState==='active') sendPtr(e); }
function onMD(e) { if(rfbState!=='active') return; pointerBtns|=(e.button===2?4:1<<e.button); sendPtr(e); }
function onMU(e) { if(rfbState!=='active') return; pointerBtns&=~(e.button===2?4:1<<e.button); sendPtr(e); }
function onWheel(e) {
  if(rfbState!=='active') return; e.preventDefault();
  const {x,y}=cc(e), btn=e.deltaY<0?8:16;
  const b=new DataView(new ArrayBuffer(6));
  b.setUint8(0,5); b.setUint8(1,btn); b.setUint16(2,x,false); b.setUint16(4,y,false);
  sendRaw(b.buffer);
  b.setUint8(1,0); sendRaw(b.buffer);
}

const KM = {
  Backspace:0xff08,Tab:0xff09,Enter:0xff0d,Escape:0xff1b,Delete:0xffff,
  Home:0xff50,End:0xff57,PageUp:0xff55,PageDown:0xff56,
  ArrowLeft:0xff51,ArrowUp:0xff52,ArrowRight:0xff53,ArrowDown:0xff54,
  Insert:0xff63,F1:0xffbe,F2:0xffbf,F3:0xffc0,F4:0xffc1,F5:0xffc2,F6:0xffc3,
  F7:0xffc4,F8:0xffc5,F9:0xffc6,F10:0xffc7,F11:0xffc8,F12:0xffc9,
  ShiftLeft:0xffe1,ShiftRight:0xffe2,ControlLeft:0xffe3,ControlRight:0xffe4,
  AltLeft:0xffe9,AltRight:0xffea,MetaLeft:0xffeb,MetaRight:0xffec,
  CapsLock:0xffe5,
};

function ksym(e) {
  if(KM[e.code]) return KM[e.code];
  if(e.key.length===1) return e.key.codePointAt(0);
  return null;
}

function sendKey(sym, down) {
  const b=new DataView(new ArrayBuffer(8));
  b.setUint8(0,4); b.setUint8(1,down?1:0); b.setUint32(4,sym,false);
  sendRaw(b.buffer);
}

function onKD(e) {
  if(rfbState!=='active') return;
  if(e.target!==document.body&&e.target!==canvas) return;
  const s=ksym(e); if(!s) return;
  e.preventDefault(); sendKey(s,true);
}

function onKU(e) {
  if(rfbState!=='active') return;
  const s=ksym(e); if(!s) return; sendKey(s,false);
}

function fitScreen() {
  const wrap=document.getElementById('vcanvas-wrap');
  const mw=wrap.clientWidth-32, mh=wrap.clientHeight-32;
  if(!fbW||!fbH) return;
  currentScale=Math.min(mw/fbW,mh/fbH,1);
  canvas.style.width=Math.round(fbW*currentScale)+'px';
  canvas.style.height=Math.round(fbH*currentScale)+'px';
}

function toggleFS() {
  if(!document.fullscreenElement) document.getElementById('vmain').requestFullscreen();
  else document.exitFullscreen();
}

function toggleClip() { document.getElementById('vclip').classList.toggle('open'); }
function setQuality(q) {}
window.addEventListener('resize',()=>{ if(rfbState==='active') fitScreen(); });

function renderRecent() {
  const list=document.getElementById('recent-list');
  if(!recentSessions.length) { list.innerHTML='<div style="padding:16px;font-family:var(--mono);font-size:11px;color:var(--dim);text-align:center">No recent sessions</div>'; return; }
  list.innerHTML=recentSessions.map(r=>`
    <div class="rec-item" onclick="loadRecent('${r.code}','${r.relay}')">
      <div class="rec-icon">🖥</div>
      <div class="rec-meta">
        <div class="rec-name">${r.name}</div>
        <div class="rec-addr">${r.code}</div>
      </div>
    </div>`).join('');
}

function loadRecent(code,relay) {
  document.getElementById('vi-code').value=code;
  document.getElementById('vi-relay').value=relay;
  toast('Code loaded — click Connect');
}

renderRecent();

document.getElementById('vi-code').addEventListener('keydown', e => {
  if(e.key==='Enter') vncConnect();
});

document.querySelectorAll('#vconn-panel input').forEach(inp => {
  inp.addEventListener('keydown', e => { if(e.key==='Enter') vncConnect(); });
});