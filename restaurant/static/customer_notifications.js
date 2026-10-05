'use strict';
const root = $('#customerReservationNotifications');
async function loadReservationNotifications() {
  const result = await api('/api/customer/reservation-notifications');
  root.innerHTML = result.events.length ? result.events.map(e => `
    <article class="card">
      <b>${esc(e.text)}</b>
      <p class="muted">การจองวันที่ ${esc(e.date)} เวลา ${esc(e.time)} · ${esc(e.party)} คน</p>
      <small class="muted">${esc(e.at)}</small>
    </article>`).join('') : '<p class="card muted">ยังไม่มีการแจ้งเตือนการจอง</p>';
}
poll(loadReservationNotifications, 15000);
