const ACCOUNT_KEY = 'starlight-rdt_account';
const CODE_TTL    = 7 * 24 * 60 * 60 * 1000;

function _generateCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  const arr = new Uint8Array(8);
  crypto.getRandomValues(arr);
  arr.forEach(b => { code += chars[b % chars.length]; });
  return code;
}

function getAccount() {
  try {
    const raw = localStorage.getItem(ACCOUNT_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function saveAccount(account) {
  localStorage.setItem(ACCOUNT_KEY, JSON.stringify(account));
}

function getOrCreateAccount(label) {
  let account = getAccount();
  const now = Date.now();
  if (!account) {
    account = {
      label: label || 'My PC',
      code: _generateCode(),
      codeIssuedAt: now,
      sessions: []
    };
    saveAccount(account);
    return account;
  }
  if (now - account.codeIssuedAt > CODE_TTL) {
    account.code = _generateCode();
    account.codeIssuedAt = now;
    saveAccount(account);
  }
  return account;
}

function getActiveCode() {
  const account = getAccount();
  if (!account) return null;
  if (Date.now() - account.codeIssuedAt > CODE_TTL) {
    account.code = _generateCode();
    account.codeIssuedAt = Date.now();
    saveAccount(account);
  }
  return account.code;
}

function codeExpiresIn() {
  const account = getAccount();
  if (!account) return 0;
  const remaining = CODE_TTL - (Date.now() - account.codeIssuedAt);
  return Math.max(0, remaining);
}

function formatExpiry(ms) {
  const days  = Math.floor(ms / 86400000);
  const hours = Math.floor((ms % 86400000) / 3600000);
  if (days > 0) return days + 'd ' + hours + 'h';
  const mins = Math.floor((ms % 3600000) / 60000);
  return hours + 'h ' + mins + 'm';
}

function addSavedSession(code, label) {
  const account = getAccount();
  if (!account) return;
  account.sessions = (account.sessions || []).filter(s => s.code !== code);
  account.sessions.unshift({ code, label: label || 'Remote PC', ts: Date.now() });
  account.sessions = account.sessions.slice(0, 10);
  saveAccount(account);
}

function removeSavedSession(code) {
  const account = getAccount();
  if (!account) return;
  account.sessions = (account.sessions || []).filter(s => s.code !== code);
  saveAccount(account);
  if (typeof renderSaved === 'function') renderSaved();
  if (typeof renderConnectRecent === 'function') renderConnectRecent();
}

function getSavedSessions() {
  const account = getAccount();
  return account ? (account.sessions || []) : [];
}

function setAuthed(code) {
  sessionStorage.setItem('starlight-rdt_auth', code);
}

function getAuthed() {
  return sessionStorage.getItem('starlight-rdt_auth') || null;
}

function clearAuthed() {
  sessionStorage.removeItem('starlight-rdt_auth');
}

function normaliseCode(raw) {
  return raw.replace(/[^A-Z0-9]/gi, '').toUpperCase().slice(0, 12);
}

function isValidFormat(code) {
  return /^[A-Z0-9]{4,12}$/.test(code);
}

function showErr(msg) {
  const el  = document.getElementById('auth-err');
  const inp = document.getElementById('code-input');
  if (!el) return;
  el.textContent = '⚠ ' + msg;
  el.classList.add('vis');
  if (inp) inp.classList.add('err');
}

function clearErr() {
  const el  = document.getElementById('auth-err');
  const inp = document.getElementById('code-input');
  if (!el) return;
  el.classList.remove('vis');
  if (inp) inp.classList.remove('err');
}

function loginWithCode(code) {
  const inp = document.getElementById('code-input');
  if (inp) inp.value = code;
  submitLogin();
}

function submitLogin() {
  clearErr();
  const inp   = document.getElementById('code-input');
  const raw   = inp ? inp.value : '';
  const code  = normaliseCode(raw);
  const labelEl = document.getElementById('label-input');
  const label = labelEl ? labelEl.value.trim() : '';

  if (!isValidFormat(code)) {
    showErr('Enter a valid session code (4–12 characters).');
    return;
  }

  const btn = document.getElementById('auth-submit');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="auth-spinner"></span>Connecting…';
  }

  setTimeout(() => {
    setAuthed(code);
    addSavedSession(code, label || 'Remote PC');
    window.location.href = 'connect.html';
  }, 500);
}

function renderSaved() {
  const wrap = document.getElementById('saved-sessions');
  if (!wrap) return;
  const list = getSavedSessions();
  if (!list.length) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = '<div class="auth-sessions-title">Saved sessions</div>' +
    list.map(s => `
      <div class="saved-session" onclick="loginWithCode('${s.code}')">
        <div class="ss-icon">🖥</div>
        <div class="ss-info">
          <div class="ss-name">${s.label}</div>
          <div class="ss-code">${s.code}</div>
        </div>
        <button class="ss-del" title="Remove" onclick="event.stopPropagation();removeSavedSession('${s.code}')">✕</button>
      </div>`).join('');
}

window.addEventListener('DOMContentLoaded', () => {
  renderSaved();
  const input = document.getElementById('code-input');
  if (!input) return;
  input.addEventListener('input', () => {
    clearErr();
    input.value = normaliseCode(input.value);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') submitLogin();
  });
});