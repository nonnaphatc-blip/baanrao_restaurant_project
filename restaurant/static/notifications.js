'use strict';

async function loadNotifications() {
  const d = await api('/api/notifications');
  $('#notificationList').innerHTML = d.events.length ? d.events.map(e => `
    <button type="button" class="card notification-row ${e.read ? 'is-read' : 'notification-new'}" data-notification-id="${e.id}" ${e.read ? 'disabled' : ''}>
      <span class="notification-message">${e.read ? '' : '<span class="notification-item-dot" aria-label="ยังไม่ได้อ่าน"></span>'}${esc(e.text)}</span>
      <span class="notification-meta"><small class="muted">${esc(e.at)}</small><small class="notification-state">${e.read ? 'อ่านแล้ว' : 'กดเพื่อทำเครื่องหมายว่าอ่านแล้ว'}</small></span>
    </button>`).join('') : '<p class="muted">ยังไม่มีการแจ้งเตือนย้อนหลัง</p>';
}

run(async () => {
  await loadNotifications();
  startEvents();
  poll(loadNotifications);
});

$('#notificationList').addEventListener('click', e => {
  const button = e.target.closest('[data-notification-id]');
  if (!button || button.disabled) return;
  run(async () => {
    await api(`/api/notifications/${button.dataset.notificationId}/read`, 'POST', {});
    await loadNotifications();
    await refreshNotificationBadge();
  });
});
