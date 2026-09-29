'use strict';
const box = $('#cust'), TID = box.dataset.table, KEY = box.dataset.key;
const LBL = {pending: 'รอทำ', cooking: 'กำลังทำ', ready: 'พร้อมเสิร์ฟ', served: 'เสิร์ฟแล้ว'};
const url = (p = '') => `/api/public/order/${TID}${p}`;
let menu, cart = [];

function drawCart() {
  $('#cart').innerHTML = cart.length ? cart.map((c, i) => `<div class="line"><div class="grow"><b>${c.qty} ${esc(c.unit || 'จาน')} · ${esc(c.name)}</b>
    <div class="muted">${esc(Object.values(c.options).filter(v => v && v !== true).join(' · '))}${c.options.egg ? ' · เพิ่มไข่' : ''}${c.note ? ' · ' + esc(c.note) : ''}</div></div>
    <button class="btn sm danger" data-act="remove" data-i="${i}">ลบ</button></div>`).join('') : '<p class="muted">ยังไม่ได้เลือกอาหาร</p>';
  $('#send').disabled = !cart.length;
}

async function status() {
  let d;
  try { d = await api(url('?k=' + encodeURIComponent(KEY))); }
  catch (e) {
    if (e.status === 404 || e.status === 410) { location.replace('/table-code?expired=1'); return; }
    throw e;
  }
  $('#send').disabled = !cart.length || d.billing;
  $('#bill').disabled = d.billing || !d.items.length;
  $('#status').innerHTML = d.items.length ? d.items.map(i => `<div class="line"><div class="grow"><b>${i.qty} ${esc(i.unit || 'จาน')} · ${esc(i.name)}</b><div class="muted">${esc(i.options)}</div></div>
    <span class="pill ${i.status}">${LBL[i.status]}</span></div>`).join('') + `<div class="right"><b>รวม ฿${baht(d.subtotal)}</b></div>` +
    (d.billing ? '<p class="muted">พนักงานกำลังเตรียมบิลให้คุณ</p>' : '') : '<p class="muted">ยังไม่มีรายการ</p>';
}

on({
  remove: d => { cart.splice(+d.i, 1); drawCart(); },
  send: async () => {
    await api(url(), 'POST', {k: KEY, items: cart});
    cart = []; drawCart(); toast('ส่งออเดอร์ให้ครัวแล้ว'); await status();
  },
  bill: async () => {
    if (!confirm('เรียกพนักงานมาเช็คบิล?')) return;
    await api(url('/bill'), 'POST', {k: KEY}); toast('แจ้งพนักงานแล้ว'); await status();
  },
});
run(async () => {
  menu = await api('/api/menu');
  menuPicker($('#menu'), menu, m => itemDialog(m, menu, line => { cart.push({...line, name: m.name, unit: m.unit}); drawCart(); }));
  drawCart();
  poll(status);
});
