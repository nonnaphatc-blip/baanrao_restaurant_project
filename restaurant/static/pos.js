'use strict';
const LBL = {free: 'ว่าง', occupied: 'มีลูกค้า', billing: 'รอเช็คบิล', pending: 'รอทำ', cooking: 'กำลังทำ', ready: 'พร้อมเสิร์ฟ', served: 'เสิร์ฟแล้ว'};
let menu, tables = [], cur = null, order = null;
const picked = new Set();
const table = () => tables.find(t => t.id === cur);

async function loadTables() {
  tables = (await api('/api/tables')).tables;
  $('#tables').innerHTML = tables.map(t => `<button class="card tbl-card ${t.status} ${t.id === cur ? 'sel' : ''}" data-act="open" data-id="${t.id}">
    <b>${esc(t.name)}</b><span class="pill ${t.status}">${LBL[t.status]}</span>
    <small class="muted">${t.count ? `${t.count} รายการ · ฿${baht(t.total)}` : `${t.seats} ที่นั่ง`}</small></button>`).join('');
  if (cur) await loadOrder();
}

async function loadOrder() {
  order = await api(`/api/orders/${cur}`);
  const t = table();
  if (!t) return;
  $('#tstatus').className = 'pill ' + t.status;
  $('#tstatus').textContent = LBL[t.status];
  $('#items').innerHTML = order.items.length ? order.items.map(i => `<div class="line">
    <label class="check"><input type="checkbox" data-pick="${i.id}" ${picked.has(i.id) ? 'checked' : ''} aria-label="เลือกเพื่อแยกบิล"></label>
    <div class="grow"><b>${esc(i.name)}</b> <span class="pill ${i.status}">${LBL[i.status]}</span>
      <div class="muted">${esc(i.options)}${i.note ? ' · 📝 ' + esc(i.note) : ''}</div></div>
    <div class="qty">${i.status === 'pending' && i.qty > 1 ? `<button class="btn sm" data-act="qty" data-i="${i.id}" data-q="${i.qty - 1}">−</button>` : ''}<b>${i.qty} ${esc(i.unit || 'จาน')}</b>
      ${i.status === 'pending' ? `<button class="btn sm" data-act="qty" data-i="${i.id}" data-q="${i.qty + 1}">+</button>` : ''}</div>
    <div class="right">฿${baht(i.amount)}</div>
    <button class="btn sm danger" data-act="cancel" data-i="${i.id}" aria-label="ยกเลิกรายการ">×</button></div>`).join('') : '<p class="muted">ยังไม่มีรายการ</p>';
  $('#sub').textContent = '฿' + baht(order.subtotal);
}

function openPanel() {
  const t = table();
  const others = tables.filter(x => x.id !== cur).map(x => `<option value="${x.id}">${esc(x.name)} (${LBL[x.status]})</option>`).join('');
  $('#panel').hidden = false;
  $('#panel').innerHTML = `<div class="row between"><h2>โต๊ะ ${esc(t.name)}</h2><span id="tstatus" class="pill"></span></div>
    <div class="row"><button class="btn sm" data-act="status" data-s="free">ว่าง</button><button class="btn sm" data-act="status" data-s="occupied">มีลูกค้า</button><button class="btn sm" data-act="status" data-s="billing">รอเช็คบิล</button></div>
    <div id="items" class="stack"></div>
    <div class="row between"><span class="muted">ติ๊กรายการเพื่อแยกบิล</span><b id="sub"></b></div>
    <div class="row"><button class="btn primary" data-act="add">+ เพิ่มเมนู</button><button class="btn" data-act="checkout">เช็คบิล</button></div>
    <div class="row"><select id="to" class="grow"><option value="">ย้าย / รวมไปโต๊ะ...</option>${others}</select>
      <button class="btn sm" data-act="move">ย้าย</button><button class="btn sm" data-act="merge">รวม</button></div>`;
  $('#panel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function payData() {
  const f = new FormData($('#payForm'));
  return {item_ids: [...picked], discount_type: f.get('discount_type'), discount_value: f.get('discount_value'),
          member: f.get('member'), redeem_points: f.get('redeem_points'), method: f.get('method')};
}

async function preview() {
  const box = $('#sum');
  try {
    const b = (await api(`/api/orders/${order.order_id}/checkout`, 'POST', {...payData(), preview: true})).bill;
    box.className = 'stack';
    box.innerHTML = `<div class="row between"><span>รวม</span><span>${baht(b.subtotal)}</span></div>
      <div class="row between"><span>ส่วนลด</span><span>-${baht(b.discount)}</span></div>
      <div class="row between"><span>ใช้แต้ม</span><span>-${baht(b.redeem)}</span></div>
      <div class="row between"><span>ค่าบริการ ${b.service_rate}%</span><span>${baht(b.service)}</span></div>
      <div class="row between"><span>VAT ${b.vat_rate}%</span><span>${baht(b.vat)}</span></div>
      <div class="row between"><h3>ยอดสุทธิ</h3><h3>฿${baht(b.total)}</h3></div>
      ${b.member ? `<small class="muted">สมาชิกได้รับ ${b.earned} แต้ม</small>` : ''}`;
    $('#payBtn').disabled = false;
  } catch (e) {
    box.className = 'err'; box.textContent = e.message; $('#payBtn').disabled = true;
  }
}

on({
  open: async d => { cur = +d.id; picked.clear(); openPanel(); await loadTables(); },
  status: async d => { await api(`/api/tables/${cur}`, 'PATCH', {status: d.s}); await loadTables(); },
  add: () => $('#menuDlg').showModal(),
  qty: async d => { await api(`/api/orders/${order.order_id}/items/${d.i}`, 'PATCH', {qty: +d.q}); await loadTables(); },
  cancel: async d => {
    if (!confirm('ยกเลิกรายการนี้?')) return;
    await api(`/api/orders/${order.order_id}/items/${d.i}`, 'PATCH', {status: 'cancelled'}); picked.delete(+d.i); await loadTables();
  },
  move: () => moveTo(false),
  merge: () => moveTo(true),
  checkout: () => {
    if (!order.order_id || !order.items.length) return toast('โต๊ะนี้ยังไม่มีรายการ', true);
    $('#payBox').innerHTML = `<h3>เช็คบิล โต๊ะ ${esc(table().name)}${picked.size ? ` (แยกบิล ${picked.size} รายการ)` : ''}</h3>
      <form id="payForm" class="stack">
        <div class="row"><select name="discount_type" class="grow"><option value="amount">ส่วนลด (บาท)</option><option value="percent">ส่วนลด (%)</option></select>
          <input name="discount_value" type="number" min="0" step="any" value="0" class="grow" aria-label="ส่วนลด"></div>
        <div class="row"><input name="member" class="grow" placeholder="ชื่อผู้ใช้สมาชิก (ถ้ามี)" maxlength="20">
          <input name="redeem_points" type="number" min="0" value="0" class="grow" aria-label="ใช้แต้ม"></div>
        <select name="method"><option value="cash">เงินสด</option><option value="card">บัตร</option><option value="qr">QR โอนเงิน</option></select></form>
      <div id="sum"></div>
      <div class="row between"><button class="btn" data-act="closePay">ปิด</button><button class="btn primary" id="payBtn" data-act="pay">ชำระเงิน</button></div>`;
    $('#payDlg').showModal();
    return preview();
  },
  closePay: () => $('#payDlg').close(),
  pay: async () => {
    const b = (await api(`/api/orders/${order.order_id}/checkout`, 'POST', payData())).bill;
    picked.clear();
    $('#payBox').innerHTML = `<h3>ชำระเงินแล้ว ✓</h3><p>ยอด ฿${baht(b.total)} · ${esc(b.receipt_no)}</p>
      <div class="row between"><a class="btn primary" target="_blank" rel="noopener" href="/receipt/${b.id}">ดูใบเสร็จ / พิมพ์</a><button class="btn" data-act="closePay">ปิด</button></div>`;
    await loadTables();
  },
});

async function moveTo(merge) {
  const to = +$('#to').value;
  if (!to) return toast('กรุณาเลือกโต๊ะปลายทาง', true);
  await api(`/api/tables/${cur}/move`, 'POST', {to, merge});
  cur = to; picked.clear(); openPanel(); await loadTables();
  toast(merge ? 'รวมโต๊ะแล้ว' : 'ย้ายโต๊ะแล้ว');
}

document.addEventListener('change', e => {
  const p = e.target.closest('[data-pick]');
  if (p) { const id = +p.dataset.pick; p.checked ? picked.add(id) : picked.delete(id); }
});
document.addEventListener('input', e => { if (e.target.closest('#payForm')) run(preview); });

run(async () => {
  menu = await api('/api/menu');
  menuPicker($('#menuBox'), menu, m => itemDialog(m, menu, line => run(async () => {
    await api(`/api/orders/${cur}/items`, 'POST', line);
    await loadTables();
  })));
  poll(loadTables);
  startEvents();
});
