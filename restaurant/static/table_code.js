(() => {
  const startButton = document.getElementById('startQrScan');
  const stopButton = document.getElementById('stopQrScan');
  const scanner = document.getElementById('qrScanner');
  const video = document.getElementById('qrVideo');
  const status = document.getElementById('qrScanStatus');
  const codeInput = document.getElementById('tableCode');
  const form = codeInput?.form;
  if (!startButton || !stopButton || !scanner || !video || !status || !codeInput || !form) return;

  let stream = null;
  let detector = null;
  let frame = null;
  let scanning = false;
  let lastMessage = '';

  function stopScanner() {
    scanning = false;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
    scanner.hidden = true;
    startButton.hidden = false;
  }

  function showStatus(message) {
    if (message !== lastMessage) status.textContent = lastMessage = message;
  }

  function handleQrValue(value) {
    const scanned = value.trim();
    if (/^\d{6}$/.test(scanned)) {
      stopScanner();
      codeInput.value = scanned;
      form.requestSubmit();
      return true;
    }

    try {
      const url = new URL(scanned, window.location.origin);
      if (url.origin === window.location.origin && /^\/t\/\d+$/.test(url.pathname) && url.searchParams.has('k')) {
        stopScanner();
        window.location.assign(url.href);
        return true;
      }
    } catch (_) {
      // Keep scanning when the QR content is not a URL.
    }

    showStatus('QR นี้ไม่ใช่รหัสหรือลิงก์โต๊ะของร้าน ลองสแกนอีกครั้ง');
    return false;
  }

  async function scanFrame() {
    if (!scanning) return;
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      try {
        const codes = await detector.detect(video);
        if (codes.length && handleQrValue(codes[0].rawValue)) return;
      } catch (_) {
        showStatus('อ่าน QR ไม่สำเร็จ กรุณาจัดให้อยู่ในกรอบกล้อง');
      }
    }
    if (scanning) frame = requestAnimationFrame(scanFrame);
  }

  startButton.addEventListener('click', async () => {
    if (!('BarcodeDetector' in window)) {
      scanner.hidden = false;
      startButton.hidden = true;
      showStatus('เบราว์เซอร์นี้ยังไม่รองรับการสแกน QR ด้วยกล้อง กรุณาใช้ Chrome หรือกรอกรหัส 6 หลัก');
      stopButton.focus();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      scanner.hidden = false;
      startButton.hidden = true;
      showStatus('เปิดกล้องไม่ได้ กรุณาเข้าเว็บไซต์ผ่าน HTTPS หรือกรอกรหัส 6 หลัก');
      stopButton.focus();
      return;
    }

    scanner.hidden = false;
    startButton.hidden = true;
    showStatus('กำลังขออนุญาตใช้กล้อง...');
    try {
      detector = new BarcodeDetector({ formats: ['qr_code'] });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      video.srcObject = stream;
      await video.play();
      scanning = true;
      showStatus('หันกล้องไปที่ QR ของโต๊ะ');
      frame = requestAnimationFrame(scanFrame);
    } catch (error) {
      stopScanner();
      scanner.hidden = false;
      startButton.hidden = true;
      showStatus(error.name === 'NotAllowedError'
        ? 'ไม่ได้รับอนุญาตให้ใช้กล้อง กรุณาอนุญาตสิทธิ์กล้องแล้วลองอีกครั้ง'
        : 'เปิดกล้องไม่สำเร็จ กรุณาลองใหม่หรือกรอกรหัส 6 หลัก');
      stopButton.focus();
    }
  });

  stopButton.addEventListener('click', stopScanner);
  window.addEventListener('pagehide', stopScanner);
})();
