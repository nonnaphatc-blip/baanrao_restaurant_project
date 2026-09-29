'use strict';
const NEXT = {pending: ['cooking', 'เริ่มทำ'], cooking: ['ready', 'เสร็จแล้ว'], ready: ['served', 'เสิร์ฟแล้ว']};
let tickets = [], filter = 'all';

function draw() {
  const html = tickets.map(t => {
    const items = t.items.filter(i => filter === 'all' || i.status === filter);
    if (!items.length) return '';
    return `<article class="card ticket"><div class="row between"><h3>โต๊ะ ${esc(t.table)}</h3><span class="muted">${esc(t.since.slice(11, 16))}</span></div>
      ${items.map(i => `<div class="krow ${i.status}"><div><b>${i.qty}× ${esc(i.name)}</b>
        ${i.options ? `<div class="muted">${esc(i.options)}</div>` : ''}${i.note ? `<div class="note">📝 ${esc(i.note)}</div>` : ''}
        <small class="muted">${i.age} นาทีที่แล้ว${i.source === 'customer' ? ' · ลูกค้าสั่งเอง' : ''}</small></div>
        <button class="btn sm ${i.status === 'cooking' ? 'primary' : ''}" data-act="next" data-o="${t.order_id}" data-i="${i.id}" data-s="${NEXT[i.status][0]}">${NEXT[i.status][1]}</button></div>`).join('')}</article>`;
  }).join('');
  $('#tickets').innerHTML = html || '<p class="muted">ยังไม่มีออเดอร์</p>';
}

async function load() { tickets = (await api('/api/kitchen')).tickets; draw(); }

on({
  filter: d => { filter = d.f; $$('#filters .btn').forEach(b => b.classList.toggle('on', b.dataset.f === filter)); draw(); },
  next: async d => { await api(`/api/orders/${d.o}/items/${d.i}`, 'PATCH', {status: d.s}); await load(); },
});
poll(load);
startEvents();
