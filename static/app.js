/* VPN Prime Panel — App JS */

// Copy text from input to clipboard
function copyText(inputId) {
  const el = document.getElementById(inputId);
  if (!el) return;
  el.select();
  el.setSelectionRange(0, 99999);
  navigator.clipboard.writeText(el.value).then(() => {
    el.classList.add('copy-success');
    const origBorder = el.style.borderColor;
    el.style.borderColor = '#28a745';
    setTimeout(() => {
      el.classList.remove('copy-success');
      el.style.borderColor = origBorder;
    }, 1000);
    // Show small toast if available
    showToast('Скопировано!', 'success');
  }).catch(() => {
    document.execCommand('copy');
    showToast('Скопировано!', 'success');
  });
}

// Show QR code in modal
function showQR(url, title) {
  const modal = document.getElementById('qrModal');
  const img = document.getElementById('qrImage');
  const loading = document.getElementById('qrLoading');
  const label = document.getElementById('qrModalLabel');

  if (!modal || !img) return;

  if (label) label.textContent = 'QR-код: ' + title;
  img.style.display = 'none';
  if (loading) loading.style.display = 'block';

  const bsModal = new bootstrap.Modal(modal);
  bsModal.show();

  img.onload = function () {
    if (loading) loading.style.display = 'none';
    img.style.display = 'block';
  };
  img.onerror = function () {
    if (loading) loading.textContent = 'Ошибка загрузки QR-кода';
  };
  img.src = url;
}

// Simple toast notification
function showToast(message, type) {
  const container = getOrCreateToastContainer();
  const id = 'toast-' + Date.now();
  const bgClass = type === 'success' ? 'bg-success' : type === 'danger' ? 'bg-danger' : 'bg-secondary';
  const html = `
    <div id="${id}" class="toast align-items-center text-white ${bgClass} border-0" role="alert" aria-live="assertive">
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  container.insertAdjacentHTML('beforeend', html);
  const toastEl = document.getElementById(id);
  const toast = new bootstrap.Toast(toastEl, { delay: 2500 });
  toast.show();
  toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

function getOrCreateToastContainer() {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    container.style.zIndex = '9999';
    document.body.appendChild(container);
  }
  return container;
}

// Auto-dismiss alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function () {
  const alerts = document.querySelectorAll('.alert.alert-dismissible');
  alerts.forEach(function (alert) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      if (bsAlert) bsAlert.close();
    }, 5000);
  });

  // Uppercase promo code inputs on type
  document.querySelectorAll('input[name="promo_code"], input[name="code"]').forEach(function (el) {
    el.addEventListener('input', function () {
      const pos = el.selectionStart;
      el.value = el.value.toUpperCase();
      el.setSelectionRange(pos, pos);
    });
  });
});
