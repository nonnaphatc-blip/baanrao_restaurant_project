'use strict';
const role = document.body.dataset.role;
const L = {categories: [], tables: [], ingredients: []};
const LBL = {free: 'ว่าง', occupied: 'มีลูกค้า', billing: 'รอเช็คบิล', pending: 'รอยืนยัน', confirmed: 'ยืนยันแล้ว', seated: 'นั่งแล้ว',
  cancelled: 'ยกเลิก', waiting: 'รอคิว', called: 'เรียกแล้ว', admin: 'ผู้ดูแล', cashier: 'แคชเชียร์', kitchen: 'ครัว', customer: 'สมาชิก'};
const pairs = keys => keys.map(k => [k, LBL[k]]);
const FMT = {
  money: v => '฿' + baht(v),
  bool: v => v ? '✓' : '—',
  img: v => v ? `<img class="thumb" src="/uploads/${esc(v)}" alt="">` : '',
  cat: v => esc(L.categories.find(c => c.id === v)?.name ?? v),
  tbl: v => esc(L.tables.find(t => t.id === v)?.name ?? '-'),
  avail: v => v ? '<span class="pill ok">มีของ</span>' : '<span class="pill bad">หมด</span>',
  lbl: v => `<span class="pill ${esc(v)}">${esc(LBL[v] || v)}</span>`,
  queueStatus: v => {
    const state = ['waiting', 'called', 'seated', 'cancelled'].includes(v) ? v : 'waiting';
    return `<span class="pill ${state}">${esc(LBL[state])}</span>`;
  },
};

// cols: [key, label, formatter, sortable]   fields: [key, label, type, options()]
const ENT = {
  menu: {title: 'เมนู', filter: ['available', [['true', 'มีของ'], ['false', 'หมด']]], def: {available: true, unit: 'จาน'},
    cols: [['id', '#'], ['image', 'รูป', 'img', 0], ['name', 'ชื่อ'], ['category_id', 'หมวด', 'cat'], ['price', 'ราคา', 'money'], ['unit', 'หน่วย'], ['available', 'สถานะ', 'avail']],
    fields: [['name', 'ชื่อเมนู', 'text'], ['category_id', 'หมวดหมู่', 'select', () => L.categories.map(c => [c.id, c.name])], ['price', 'ราคา (บาท)', 'number'], ['unit', 'หน่วยที่ขาย เช่น จาน, แก้ว, ขวด', 'text'],
      ['description', 'รายละเอียด', 'text'], ['image', 'รูปภาพ', 'image'], ['available', 'มีของ (ไม่ติ๊ก = หมด)', 'check'],
      ['has_spice', 'เลือกระดับความเผ็ดได้', 'check'], ['has_egg', 'เพิ่มไข่ได้', 'check'], ['has_size', 'เลือกขนาดได้', 'check'], ['recipe', 'สูตรอาหาร (ตัดสต็อกอัตโนมัติ)', 'recipe']]},
  categories: {title: 'หมวดหมู่', def: {sort: 0}, cols: [['id', '#'], ['name', 'ชื่อ'], ['sort', 'ลำดับ']],
    fields: [['name', 'ชื่อหมวดหมู่', 'text'], ['sort', 'ลำดับ', 'number']]},
  tables: {title: 'โต๊ะ', qr: true, filter: ['status', pairs(['free', 'occupied', 'billing'])], def: {seats: 4, status: 'free'},
    cols: [['id', '#'], ['name', 'ชื่อ'], ['seats', 'ที่นั่ง'], ['status', 'สถานะ', 'lbl']],
    fields: [['name', 'ชื่อโต๊ะ', 'text'], ['seats', 'จำนวนที่นั่ง', 'number'], ['status', 'สถานะ', 'select', () => pairs(['free', 'occupied', 'billing'])]]},
  ingredients: {title: 'วัตถุดิบ / สต็อก', def: {stock: 0, min_stock: 0}, cols: [['id', '#'], ['name', 'ชื่อ'], ['stock', 'คงเหลือ'], ['unit', 'หน่วย'], ['min_stock', 'ขั้นต่ำ']],
    fields: [['name', 'ชื่อวัตถุดิบ', 'text'], ['unit', 'หน่วย (เช่น กรัม, ฟอง)', 'text'], ['stock', 'คงเหลือ', 'number'], ['min_stock', 'แจ้งเตือนเมื่อเหลือไม่เกิน', 'number']]},
  users: {title: 'ผู้ใช้', filter: ['role', pairs(['admin', 'cashier', 'kitchen', 'customer'])], def: {active: true, role: 'customer', points: 0},
    cols: [['id', '#'], ['username', 'ชื่อผู้ใช้'], ['name', 'ชื่อ'], ['role', 'บทบาท', 'lbl'], ['points', 'แต้ม'], ['active', 'ใช้งาน', 'bool']],
    fields: [['username', 'ชื่อผู้ใช้', 'text'], ['name', 'ชื่อ', 'text'], ['role', 'บทบาท', 'select', () => pairs(['admin', 'cashier', 'kitchen', 'customer'])],
      ['password', 'รหัสผ่าน (เว้นว่างเมื่อแก้ไข = ไม่เปลี่ยน)', 'password'], ['points', 'แต้มสะสม', 'number'], ['active', 'เปิดใช้งาน', 'check']]},
  reservations: {title: 'จองโต๊ะ', filter: ['status', pairs(['pending', 'confirmed', 'seated', 'cancelled'])], def: {status: 'confirmed', party: 2},
    cols: [['id', '#'], ['date', 'วันที่'], ['time', 'เวลา'], ['name', 'ชื่อ'], ['party', 'คน'], ['table_id', 'โต๊ะ', 'tbl'], ['status', 'สถานะ', 'lbl']],
    fields: [['name', 'ชื่อผู้จอง', 'text'], ['date', 'วันที่', 'date'], ['time', 'เวลา', 'time'], ['party', 'จำนวนคน', 'number'],
      ['table_id', 'โต๊ะ', 'select', () => [['', '- ยังไม่กำหนด -'], ...L.tables.map(t => [t.id, t.name])]], ['note', 'หมายเหตุ', 'text'],
      ['status', 'สถานะ', 'select', () => pairs(['pending', 'confirmed', 'seated', 'cancelled'])]]},
  queue: {title: 'คิว', filter: ['status', pairs(['waiting', 'called', 'seated', 'cancelled'])], def: {status: 'waiting', party: 2},
    cols: [['number', 'คิว'], ['date', 'วันที่'], ['name', 'ชื่อ'], ['party', 'คน'], ['status', 'สถานะ', 'queueStatus']],
    fields: [['name', 'ชื่อ', 'text'], ['party', 'จำนวนคน', 'number'], ['status', 'สถานะ', 'select', () => pairs(['waiting', 'called', 'seated', 'cancelled'])]]},
  logs: {title: 'Log', ro: true, filter: ['action', ['create', 'update', 'delete', 'checkout', 'login', 'login_failed', 'upload'].map(a => [a, a])],
    cols: [['ts', 'เวลา'], ['user', 'ผู้ใช้'], ['action', 'การกระทำ'], ['entity', 'ข้อมูล'], ['ref', 'อ้างอิง'], ['detail', 'รายละเอียด', null, 0]]},
};
const TABS = role === 'admin'
  ? [['dashboard', 'แดชบอร์ด'], ['menu', 'เมนู'], ['categories', 'หมวดหมู่'], ['tables', 'โต๊ะ'], ['ingredients', 'สต็อก'], ['users', 'ผู้ใช้'],
     ['reservations', 'จองโต๊ะ'], ['queue', 'คิว'], ['logs', 'Log'], ['settings', 'ตั้งค่า']]
  : [['tables', 'โต๊ะ'], ['reservations', 'จองโต๊ะ'], ['queue', 'คิว']];
let S = {};
let tableStatusTimer = null;
let viewGeneration = 0;
let listRequestId = 0;
let dashboardRequestId = 0;
let lookupsPromise = null;

async function cashierTablesView(generation) {
  $('#view').innerHTML = `<div class="row toolbar"><input type="search" id="tableAccessSearch" class="grow" placeholder="ค้นหาโต๊ะ..."></div>
    <div id="tableAccessList"></div>`;
  const draw = (tables, query) => {
    const rows = tables.filter(t => t.name.toLowerCase().includes(query.trim().toLowerCase()));
    $('#tableAccessList').innerHTML = `<div class="tblwrap"><table class="tbl"><thead><tr>
      <th>#</th><th>ชื่อโต๊ะ</th><th>ที่นั่ง</th><th>สถานะ</th><th>รหัสโต๊ะ</th><th></th>
      </tr></thead><tbody>${rows.map(t => `<tr><td>${t.id}</td><td><b>${esc(t.name)}</b></td><td>${t.seats}</td>
      <td>${FMT.lbl(t.status)}</td><td><b>${esc(t.access_code || '—')}</b></td>
      <td class="right"><button class="btn sm" data-act="qr" data-id="${t.id}">แสดง QR</button></td></tr>`).join('') ||
      '<tr><td colspan="6" class="muted">ไม่พบโต๊ะ</td></tr>'}</tbody></table></div>`;
  };
  const refresh = async () => {
    const tables = (await api('/api/tables')).tables;
    if (generation !== viewGeneration || S.ent !== 'tables') return;
    if (tables.some(t => !t.qr_token || !t.access_code)) {
      throw new Error('ข้อมูล QR และรหัสโต๊ะยังโหลดไม่ครบ กรุณารีสตาร์ทเซิร์ฟเวอร์แล้วลองใหม่');
    }
    S.rows = tables;
    draw(tables, $('#tableAccessSearch')?.value || '');
  };
  $('#tableAccessSearch').oninput = e => draw(S.rows, e.target.value);
  await refresh();
  if (generation !== viewGeneration) return;
  tableStatusTimer = setInterval(() => {
    if (!document.hidden && generation === viewGeneration && S.ent === 'tables' && !$('dialog[open]')) refresh().catch(() => {});
  }, 2500);
}

async function loadLookups() {
  L.categories = (await api('/api/menu')).categories;
  L.tables = (await api('/api/tables')).tables;
  if (role === 'admin') L.ingredients = (await api('/api/admin/ingredients?per=200')).items;
}

function ensureLookups() {
  if (!lookupsPromise) lookupsPromise = loadLookups().catch(error => { lookupsPromise = null; throw error; });
  return lookupsPromise;
}

function refreshLookups() {
  lookupsPromise = null;
  return ensureLookups();
}

// ---------- dashboard ----------
async function dash(date, generation = viewGeneration) {
  const requestId = ++dashboardRequestId;
  const d = (await api('/api/admin/dashboard?date=' + date)).dashboard;
  if (generation !== viewGeneration || requestId !== dashboardRequestId) return;
  const max = Math.max(1, ...d.week.map(w => w.sales));
  const methods = {cash: 'เงินสด', card: 'บัตร', qr: 'QR'};
  $('#view').innerHTML = `<div class="dashboard-header"><h2>สรุปยอดขายรายวัน</h2><div class="dashboard-actions"><input type="date" id="dd" value="${d.date}" class="auto"><a class="btn primary" href="/api/admin/dashboard/export?date=${d.date}">ดาวน์โหลด CSV 30 วัน</a></div></div>
    <div class="grid kpi">
      <div class="card"><small class="muted">ยอดขาย</small><h2>฿${baht(d.sales)}</h2></div>
      <div class="card"><small class="muted">จำนวนบิล</small><h2>${d.bills}</h2></div>
      <div class="card"><small class="muted">เฉลี่ยต่อบิล</small><h2>฿${baht(d.avg)}</h2></div>
      <div class="card"><small class="muted">โต๊ะที่มีออเดอร์</small><h2>${d.open_orders}</h2></div></div>
    <div class="grid two mt">
      <div class="card stack"><h3>ยอดขาย 7 วันล่าสุด</h3>${d.week.map(w => `<div class="bar"><span>${esc(w.date.slice(5))}</span><i data-w="${Math.round(w.sales / max * 100)}"></i><b>${baht(w.sales)}</b></div>`).join('')}</div>
      <div class="card stack"><h3>เมนูขายดี</h3>${d.top.length ? d.top.map((t, i) => `<div class="row between"><span>${i + 1}. ${esc(t.name)}</span><span>${t.qty} ${esc(t.unit)} · ฿${baht(t.revenue)}</span></div>`).join('') : '<p class="muted">ยังไม่มียอดขายในวันนี้</p>'}</div>
      <div class="card stack"><h3>สถานะโต๊ะ</h3>${Object.entries(d.tables).map(([k, v]) => `<div class="row between"><span class="pill ${k}">${LBL[k]}</span><b>${v}</b></div>`).join('')}
        <h3>ช่องทางชำระเงิน</h3>${Object.entries(d.methods).map(([k, v]) => `<div class="row between"><span>${methods[k]}</span><b>฿${baht(v)}</b></div>`).join('') || '<p class="muted">-</p>'}</div>
      <div class="card stack"><h3>วัตถุดิบใกล้หมด</h3>${d.low_stock.map(i => `<div class="row between"><span>${esc(i.name)}</span><b class="err">${i.stock} ${esc(i.unit)}</b></div>`).join('') || '<p class="muted">สต็อกปกติ</p>'}
        <h3>วันนี้</h3><div class="row between"><span>จองโต๊ะรอดำเนินการ</span><b>${d.reservations}</b></div><div class="row between"><span>คิวที่รออยู่</span><b>${d.queue}</b></div></div></div>`;
  $$('.bar i').forEach(i => { i.style.width = i.dataset.w + '%'; });
  $('#dd').onchange = e => { if (e.target.value && generation === viewGeneration) run(() => dash(e.target.value, generation)); };
}

// ---------- settings ----------
async function settingsView(generation = viewGeneration) {
  const s = (await api('/api/admin/settings')).settings;
  if (generation !== viewGeneration) return;
  const fields = [['vat', 'VAT (%)'], ['service', 'ค่าบริการ (%)'], ['point_per_baht', 'ซื้อกี่บาทได้ 1 แต้ม (ใช้แลกส่วนลด 1 แต้ม = 1 บาท)'], ['egg_price', 'ราคาไข่เพิ่ม'], ['size_price', 'ราคาขนาดพิเศษเพิ่ม']];
  $('#view').innerHTML = `<form id="setForm" class="card stack narrow"><h2>ตั้งค่าร้าน</h2>
    ${fields.map(([k, l]) => `<label>${esc(l)}<input name="${k}" type="number" step="any" min="0" value="${s[k]}"></label>`).join('')}
    <p class="err" id="ferr"></p><button class="btn primary">บันทึก</button></form>`;
  $('#setForm').onsubmit = e => {
    e.preventDefault();
    run(async () => { await api('/api/admin/settings', 'PUT', Object.fromEntries(new FormData(e.target))); toast('บันทึกการตั้งค่าแล้ว'); });
  };
}

// ---------- generic list ----------
async function openTab(t) {
  const generation = ++viewGeneration;
  ++listRequestId;
  if (tableStatusTimer !== null) clearInterval(tableStatusTimer);
  tableStatusTimer = null;
  S = {ent: t, q: '', filter: '', sort: '', order: 'asc', page: 1, rows: []};
  $$('#tabs .btn').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  $('#view').innerHTML = '<p class="muted">กำลังโหลด...</p>';
  if (t === 'dashboard') return dash(new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Bangkok'}), generation);
  if (t === 'settings') return settingsView(generation);
  if (t === 'tables' && role === 'cashier') return cashierTablesView(generation);
  const cfg = ENT[t];
  $('#view').innerHTML = `<div class="row toolbar"><input type="search" id="q" class="grow" placeholder="ค้นหา${esc(cfg.title)}...">
    ${cfg.filter ? `<select id="f"><option value="">ทุกสถานะ</option>${cfg.filter[1].map(([v, l]) => `<option value="${cfg.filter[0]}:${esc(v)}">${esc(l)}</option>`).join('')}</select>` : ''}
    ${cfg.ro ? '' : '<button class="btn primary" data-act="add">+ เพิ่ม</button>'}</div><div id="list"><p class="muted">กำลังโหลด...</p></div>`;
  const state = S;
  let timer;
  $('#q').oninput = e => { clearTimeout(timer); timer = setTimeout(() => { if (generation !== viewGeneration || S !== state) return; state.q = e.target.value; state.page = 1; run(() => list(generation)); }, 300); };
  if ($('#f')) $('#f').onchange = e => { if (generation !== viewGeneration || S !== state) return; state.filter = e.target.value; state.page = 1; run(() => list(generation)); };
  await list(generation);
  if (generation === viewGeneration && S === state && t === 'reservations') {
    tableStatusTimer = setInterval(() => {
      if (!document.hidden && generation === viewGeneration && S === state && !$('dialog[open]')) run(() => list(generation));
    }, 2000);
  } else if (generation === viewGeneration && S === state && t === 'tables' && role === 'admin') {
    tableStatusTimer = setInterval(() => {
      if (!document.hidden && generation === viewGeneration && S === state && !$('dialog[open]')) run(() => list(generation));
    }, 2500);
  }
}

async function list(generation = viewGeneration) {
  if (generation !== viewGeneration) return;
  const state = S;
  const cfg = ENT[state.ent];
  const requestId = ++listRequestId;
  const p = new URLSearchParams({q: state.q, filter: state.filter, sort: state.sort, order: state.order, page: state.page, per: 10});
  const d = await api(`/api/admin/${state.ent}?${p}`);
  if (generation !== viewGeneration || S !== state || requestId !== listRequestId) return;
  state.rows = d.items;
  const head = cfg.cols.map(([k, l, , sortable]) => `<th ${sortable === 0 ? '' : `data-act="sort" data-k="${k}"`}>${esc(l)}${state.sort === k ? (state.order === 'asc' ? ' ▲' : ' ▼') : ''}</th>`).join('') + (cfg.ro ? '' : '<th></th>');
  const rows = d.items.map(r => `<tr>${cfg.cols.map(([k, , f]) => `<td>${(FMT[f] || esc)(r[k])}</td>`).join('')}${cfg.ro ? '' :
    `<td class="right nowrap">${cfg.qr ? `<button class="btn sm" data-act="qr" data-id="${r.id}">QR</button> ` : ''}<button class="btn sm" data-act="edit" data-id="${r.id}">แก้ไข</button> <button class="btn sm danger" data-act="del" data-id="${r.id}">ลบ</button></td>`}</tr>`).join('');
  $('#list').innerHTML = `<div class="tblwrap"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${rows || `<tr><td colspan="9" class="muted">ไม่พบข้อมูล</td></tr>`}</tbody></table></div>
    <div class="row center mt"><button class="btn sm" data-act="page" data-p="${d.page - 1}" ${d.page <= 1 ? 'disabled' : ''}>ก่อนหน้า</button>
    <span class="muted">หน้า ${d.page}/${d.pages} · ${d.total} รายการ</span><button class="btn sm" data-act="page" data-p="${d.page + 1}" ${d.page >= d.pages ? 'disabled' : ''}>ถัดไป</button></div>`;
}

// ---------- form dialog ----------
const recRow = (r = {}) => `<div class="row rec"><select class="grow">${L.ingredients.map(i => `<option value="${i.id}" ${i.id === r.ingredient_id ? 'selected' : ''}>${esc(i.name)} (${esc(i.unit)})</option>`).join('')}</select>
  <input type="number" step="any" min="0" value="${r.qty ?? ''}" placeholder="ปริมาณ" class="w6"><button type="button" class="btn sm danger" data-act="rec-del">×</button></div>`;

function fieldHTML([k, l, t, opts], v) {
  if (t === 'check') return `<label class="check"><input type="checkbox" name="${k}" ${v ? 'checked' : ''}> ${esc(l)}</label>`;
  if (t === 'select') return `<label>${esc(l)}<select name="${k}">${opts().map(([val, txt]) => `<option value="${esc(val)}" ${String(v ?? '') === String(val) ? 'selected' : ''}>${esc(txt)}</option>`).join('')}</select></label>`;
  if (t === 'image') return `<label>${esc(l)}<input type="file" accept="image/png,image/jpeg,image/webp" data-up="${k}"><input type="hidden" name="${k}" value="${esc(v || '')}"><small class="muted up-note">${v ? 'มีรูปแล้ว' : 'ยังไม่มีรูป'}</small></label>`;
  if (t === 'recipe') return `<div class="stack"><b>${esc(l)}</b><div id="recipe" class="stack">${(v || []).map(recRow).join('')}</div><button type="button" class="btn sm" data-act="rec-add">+ วัตถุดิบ</button></div>`;
  return `<label>${esc(l)}<input name="${k}" type="${t}" value="${esc(v ?? '')}" ${t === 'number' ? 'step="any"' : ''} ${t === 'password' ? 'autocomplete="new-password"' : ''}></label>`;
}

function formDialog(ent, row) {
  const cfg = ENT[ent], v = row || cfg.def || {};
  const d = document.createElement('dialog');
  d.className = 'modal';
  d.innerHTML = `<form class="stack scroll"><h3>${row ? 'แก้ไข' : 'เพิ่ม'}${esc(cfg.title)}</h3>${cfg.fields.map(f => fieldHTML(f, v[f[0]])).join('')}
    <p class="err" id="ferr"></p><div class="row between"><button type="button" class="btn" data-act="close-dlg">ยกเลิก</button><button class="btn primary">บันทึก</button></div></form>`;
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  $('form', d).onsubmit = async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const saveButton = $('button.primary', form);
    const imageInputs = $$('[data-up]', form);
    if (imageInputs.some(input => input.dataset.uploading === 'true')) {
      $('#ferr', d).textContent = 'กำลังอัปโหลดรูป กรุณารอสักครู่';
      return;
    }
    if (imageInputs.some(input => input.dataset.uploadFailed === 'true')) {
      $('#ferr', d).textContent = 'อัปโหลดรูปไม่สำเร็จ กรุณาเลือกรูปใหม่แล้วรอให้อัปโหลดเสร็จก่อนบันทึก';
      return;
    }
    const body = {};
    for (const [k, , t] of cfg.fields) {
      if (t === 'recipe') body[k] = $$('.rec', d).map(r => ({ingredient_id: $('select', r).value, qty: $('input', r).value}));
      else if (t === 'check') body[k] = $(`[name="${k}"]`, d).checked;
      else body[k] = $(`[name="${k}"]`, d).value;
    }
    try {
      form.dataset.saving = 'true';
      saveButton.disabled = true;
      await api(`/api/admin/${ent}${row ? '/' + row.id : ''}`, row ? 'PUT' : 'POST', body);
      d.close(); toast('บันทึกแล้ว');
      await refreshLookups(); await list();
    } catch (err) { $('#ferr', d).textContent = err.message; }
    finally {
      delete form.dataset.saving;
      saveButton.disabled = imageInputs.some(input => input.dataset.uploading === 'true');
    }
  };
  d.showModal();
}

document.addEventListener('change', e => {
  const f = e.target.closest('[data-up]');
  if (!f || !f.files[0]) return;
  const dlg = f.closest('dialog');
  (async () => {
    const form = f.closest('form');
    const saveButton = $('button.primary', form);
    const note = $('.up-note', f.parentElement);
    f.dataset.uploading = 'true';
    f.dataset.uploadFailed = 'false';
    saveButton.disabled = true;
    $('#ferr', dlg).textContent = '';
    if (note) note.textContent = 'กำลังอัปโหลดรูป...';
    try {
      const fd = new FormData(); fd.append('file', f.files[0]);
      const r = await api('/api/admin/upload', 'POST', fd);
      $(`[name="${f.dataset.up}"]`, dlg).value = r.name;
      if (note) note.textContent = 'อัปโหลดรูปแล้ว';
    } catch (err) {
      f.dataset.uploadFailed = 'true';
      if (note) note.textContent = 'อัปโหลดไม่สำเร็จ กรุณาเลือกรูปใหม่';
      $('#ferr', dlg).textContent = err.message;
    } finally {
      delete f.dataset.uploading;
      saveButton.disabled = form.dataset.saving === 'true' || $$('[data-up]', form).some(input => input.dataset.uploading === 'true');
    }
  })();
});

function qrDialog(t) {
  const link = `${location.origin}/t/${t.id}?k=${t.qr_token}`;
  const qr = qrcode(0, 'M'); qr.addData(link); qr.make();
  const d = document.createElement('dialog');
  d.className = 'modal';
  d.innerHTML = `<div class="stack c"><h2>โต๊ะ ${esc(t.name)}</h2><img class="qr" alt="QR Code โต๊ะ ${esc(t.name)}" src="${qr.createDataURL(8, 2)}">
    <p class="muted">สแกนเพื่อสั่งอาหาร หรือกรอกรหัสโต๊ะ <b>${esc(t.access_code || '—')}</b><br><small>${esc(link)}</small></p>
    <div class="row between noprint"><button class="btn" data-act="close-dlg">ปิด</button><button class="btn primary" data-print>พิมพ์</button></div></div>`;
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  d.showModal();
}

on({
  tab: d => openTab(d.t),
  sort: d => { S.order = S.sort === d.k && S.order === 'asc' ? 'desc' : 'asc'; S.sort = d.k; return list(); },
  page: d => { S.page = +d.p; return list(); },
  add: async () => {
    const generation = viewGeneration, entity = S.ent;
    await ensureLookups();
    if (generation === viewGeneration && entity === S.ent) formDialog(entity, null);
  },
  edit: async d => {
    const generation = viewGeneration, entity = S.ent, row = S.rows.find(r => r.id === +d.id);
    await ensureLookups();
    if (generation === viewGeneration && entity === S.ent && row) formDialog(entity, row);
  },
  del: async d => {
    if (!confirm('ยืนยันการลบ?')) return;
    await api(`/api/admin/${S.ent}/${d.id}`, 'DELETE'); toast('ลบแล้ว');
    await refreshLookups(); await list();
  },
  qr: d => qrDialog(S.rows.find(r => r.id === +d.id)),
  'rec-add': () => $('#recipe').insertAdjacentHTML('beforeend', recRow()),
  'rec-del': (_, b) => b.closest('.rec').remove(),
  'close-dlg': (_, b) => b.closest('dialog').close(),
});

run(async () => {
  $('#tabs').innerHTML = TABS.map(([k, l]) => `<button class="btn sm" data-act="tab" data-t="${k}">${esc(l)}</button>`).join('');
  startEvents();
  run(ensureLookups);
  const requestedTab = new URLSearchParams(location.search).get('tab');
  const initialTab = TABS.some(([key]) => key === requestedTab) ? requestedTab : TABS[0][0];
  await openTab(initialTab);
});
