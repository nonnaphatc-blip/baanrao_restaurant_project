'use strict';
const CSRF = document.querySelector('meta[name=csrf]').content;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const baht = n => Number(n || 0).toLocaleString('th-TH', {minimumFractionDigits: 2, maximumFractionDigits: 2});

const THEME_KEY = 'baanrao-theme';
const themeToggle = $('#themeToggle');
function applyTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  if (themeToggle) {
    $('.theme-switch-thumb', themeToggle).textContent = dark ? '☾' : '☀';
    themeToggle.setAttribute('aria-pressed', String(dark));
    const label = dark ? 'เปลี่ยนเป็นโหมดสว่าง' : 'เปลี่ยนเป็นโหมดมืด';
    themeToggle.setAttribute('aria-label', label);
    themeToggle.setAttribute('title', label);
  }
}
let savedTheme = 'light';
try { savedTheme = localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; } catch {}
applyTheme(savedTheme);
themeToggle?.addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch {}
});

function toast(msg, bad = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (bad ? ' bad' : '');
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 4000);
}

async function api(url, method = 'GET', body) {
  const opt = {method, headers: {'X-CSRF-Token': CSRF}};
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let res, data;
  try { res = await fetch(url, opt); data = await res.json(); }
  catch { throw new Error('เชื่อมต่อเซิร์ฟเวอร์ไม่ได้'); }
  if (!res.ok || !data.ok) { const err = new Error(data.error || 'เกิดข้อผิดพลาด'); err.status = res.status; throw err; }
  return data;
}

async function run(fn) { try { await fn(); } catch (e) { toast(e.message, true); } }
function poll(fn, ms = 4000) { run(fn); setInterval(() => { if (!document.hidden) run(fn); }, ms); }

// one click dispatcher: <button data-act="name"> -> map[name](dataset, element)
function on(map) {
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    const fn = b && map[b.dataset.act];
    if (fn) run(() => fn(b.dataset, b));
  });
}

// real-time notifications by polling (staff pages)
function startEvents() {
  let last = -1;
  poll(async () => {
    const d = await api('/api/events?since=' + last);
    if (last >= 0) d.events.forEach(e => toast('🔔 ' + e.text));
    last = d.last;
    await refreshNotificationBadge();
  });
}

async function refreshNotificationBadge() {
  const dot = $('#notificationDot');
  if (!dot) return;
  const result = await api('/api/notifications/unread');
  dot.hidden = result.unread === 0;
}

// forms with data-api (reservation / queue on home page)
document.addEventListener('submit', e => {
  const form = e.target.closest('form[data-api]');
  if (!form) return;
  e.preventDefault();
  run(async () => {
    const data = Object.fromEntries(new FormData(form));
    const res = await api(form.dataset.api, 'POST', data);
    toast(res.message || 'สำเร็จ');
    form.reset();
  });
});

document.addEventListener('click', e => { if (e.target.closest('[data-print]')) window.print(); });

// menu list with search + category filter; onPick(item) when a card is clicked
function menuPicker(root, menu, onPick) {
  root.innerHTML = `<div class="row"><input type="search" class="grow" placeholder="ค้นหาเมนู">
    <select class="cat-sel"><option value="0">ทุกหมวด</option>${menu.categories.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></div>
    <div class="grid list"></div>`;
  const q = $('input', root), sel = $('select', root), list = $('.list', root);
  const draw = () => {
    const text = q.value.trim().toLowerCase(), cat = +sel.value;
    const items = menu.items.filter(m => (!cat || m.category_id === cat) && m.name.toLowerCase().includes(text));
    list.innerHTML = items.map(m => `<button class="card item" data-id="${m.id}" ${m.available ? '' : 'disabled'}>
      ${m.image ? `<img src="/uploads/${esc(m.image)}" alt="">` : '<div class="ph">🍽</div>'}
      <b>${esc(m.name)}</b><span>฿${baht(m.price)} / ${esc(m.unit || 'จาน')}</span>${m.available ? '' : '<em class="pill bad">หมด</em>'}</button>`).join('') || '<p class="muted">ไม่พบเมนู</p>';
  };
  q.oninput = sel.onchange = draw;
  list.onclick = e => { const b = e.target.closest('[data-id]'); if (b) onPick(menu.items.find(m => m.id === +b.dataset.id)); };
  draw();
}

// option dialog (spice / egg / size / qty / note); onAdd receives the line to order
function itemDialog(item, cfg, onAdd) {
  const d = document.createElement('dialog');
  d.className = 'modal';
  d.innerHTML = `<form method="dialog" class="stack"><h3>${esc(item.name)} · ฿${baht(item.price)} / ${esc(item.unit || 'จาน')}</h3>
    ${item.has_spice ? `<label>ความเผ็ด<select name="spice">${cfg.spice.map(s => `<option>${esc(s)}</option>`).join('')}</select></label>` : ''}
    ${item.has_size ? `<label>ขนาด<select name="size">${cfg.sizes.map((s, i) => `<option>${esc(s)}</option>`).join('')}</select><small class="muted">ขนาดพิเศษ +฿${baht(cfg.size_price)}</small></label>` : ''}
    ${item.has_egg ? `<label class="check"><input type="checkbox" name="egg"> เพิ่มไข่ (+฿${baht(cfg.egg_price)})</label>` : ''}
    <label>จำนวน<input type="number" name="qty" value="1" min="1" max="99"></label>
    <label>หมายเหตุ<input name="note" maxlength="100"></label>
    <div class="row between"><button class="btn" value="cancel" formnovalidate>ยกเลิก</button><button class="btn primary" value="ok">เพิ่มรายการ</button></div></form>`;
  document.body.append(d);
  d.addEventListener('close', () => {
    if (d.returnValue === 'ok') {
      const f = new FormData($('form', d));
      onAdd({menu_id: item.id, qty: +f.get('qty'), note: f.get('note'),
             options: {spice: f.get('spice'), size: f.get('size'), egg: f.get('egg') === 'on'}});
    }
    d.remove();
  });
  d.showModal();
}
