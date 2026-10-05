'use strict';
const reservationForm = $('form[data-api="/api/public/reservations"]');
const reservationPanel = $('#myReservations');
const reservationFeedback = $('[data-form-feedback]', reservationForm);
let activeReservations = [];
let cooldownUntil = 0;

function updateReservationForm() {
  const remaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
  const submit = $('button[type="submit"]', reservationForm) || $('button:not([type])', reservationForm);
  submit.disabled = activeReservations.length > 0 || remaining > 0;
  if (activeReservations.length) {
    reservationFeedback.textContent = 'คุณมีรายการจองอยู่แล้ว ยกเลิกรายการเดิมด้านล่างก่อนจึงจะจองใหม่ได้';
    reservationFeedback.hidden = false;
  } else if (remaining > 0) {
    const minutes = Math.floor(remaining / 60), seconds = remaining % 60;
    reservationFeedback.textContent = `ยกเลิกการจองแล้ว กรุณารอ ${minutes}:${String(seconds).padStart(2, '0')} นาทีก่อนจองใหม่`;
    reservationFeedback.hidden = false;
  } else if (!reservationForm.dataset.submitting) {
    reservationFeedback.hidden = true;
    reservationFeedback.textContent = '';
  }
}

async function refreshReservations() {
  const result = await api('/api/customer/reservations');
  activeReservations = result.reservations;
  cooldownUntil = Date.now() + result.cooldown_seconds * 1000;
  reservationPanel.innerHTML = `<h2>การจองของฉัน</h2>${activeReservations.length ? activeReservations.map(item => `
    <article class="reservation-current">
      <div><b>${esc(item.date)} เวลา ${esc(item.time)}</b><div class="muted">${esc(item.name)} · ${esc(item.party)} คน · ${item.status === 'confirmed' ? 'ยืนยันแล้ว' : 'รอยืนยัน'}</div></div>
      <button class="btn danger" type="button" data-act="cancel-reservation" data-id="${item.id}">ยกเลิกการจอง</button>
    </article>`).join('') : '<p class="muted">ไม่มีรายการจองที่ยังใช้งานอยู่</p>'}
    <p class="muted" data-cooldown-message ${result.cooldown_seconds ? '' : 'hidden'}></p>`;
  updateReservationForm();
}

on({
  'cancel-reservation': async data => {
    if (!confirm('ต้องการยกเลิกรายการจองนี้หรือไม่? หลังยกเลิกต้องรอ 1 นาทีก่อนจองใหม่')) return;
    const result = await api(`/api/customer/reservations/${data.id}/cancel`, 'POST', {});
    toast(result.message);
    await refreshReservations();
  },
});

reservationForm.addEventListener('api:success', () => refreshReservations().catch(error => toast(error.message, true)));
refreshReservations().catch(error => toast(error.message, true));
setInterval(() => {
  updateReservationForm();
  const message = $('[data-cooldown-message]', reservationPanel);
  const remaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
  if (!message) return;
  message.hidden = remaining === 0;
  if (remaining) message.textContent = `จองใหม่ได้ใน ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')} นาที`;
}, 1000);
setInterval(() => {
  if (!document.hidden) refreshReservations().catch(() => {});
}, 10000);
