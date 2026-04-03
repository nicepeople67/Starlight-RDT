/**
 * StarlightRDT — UI Utilities
 * Handles: page routing, tabs, FAQ accordion, modal, copy buttons,
 *          scroll animations, toast notifications, nav highlight
 */

function triggerDownload(filename, label) {
  const url = 'downloads/' + filename;
  fetch(url, { method: 'HEAD' })
    .then(r => {
      if (r.ok) {
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } else {
        _dlFallback(label);
      }
    })
    .catch(() => _dlFallback(label));
}

function _dlFallback(label) {
  const modal = document.getElementById('dl-coming-soon');
  if (modal) {
    document.getElementById('dl-cs-label').textContent = label;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
  } else {
    toast(label + ' — build not available yet. See agent/build.py to compile.', 'warn');
  }
}

function scrollToSection(id) {
  const el = document.querySelector(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ─── Setup guide tabs ─── */
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

/* ─── FAQ accordion ─── */
function toggleFaq(btn) {
  const item = btn.closest('.faq-item');
  const open = item.classList.contains('open');
  document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('open'));
  if (!open) item.classList.add('open');
}

/* ─── Download modal ─── */
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

/* ─── Copy code blocks ─── */
function copyCode(btn) {
  const block = btn.closest('.code-block');
  const text = Array.from(block.childNodes)
    .filter(n => n.nodeName !== 'BUTTON')
    .map(n => n.textContent)
    .join('')
    .trim();
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'copied';
    btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('ok'); }, 2000);
  }).catch(() => {
    btn.textContent = '✓';
    setTimeout(() => btn.textContent = 'copy', 2000);
  });
}

/* ─── Toast ─── */
function toast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'vis' + (type ? ' ' + type : '');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('vis'), 4000);
}

/* ─── Scroll animations ─── */
const revealObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('vis');
      e.target.querySelectorAll('.ss').forEach((s, i) =>
        setTimeout(() => s.classList.add('vis'), i * 110)
      );
    }
  });
}, { threshold: 0.07 });

document.querySelectorAll('.reveal, .stagger').forEach(el => revealObserver.observe(el));

/* ─── Nav highlight on scroll ─── */
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

window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    document.querySelectorAll('#tc-win .ss').forEach((s, i) =>
      setTimeout(() => s.classList.add('vis'), 400 + i * 110)
    );
  }, 200);
  if (window.location.hash) {
    setTimeout(() => scrollToSection(window.location.hash), 150);
  }
});