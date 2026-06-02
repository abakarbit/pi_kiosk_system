'use strict';

// ─── API Endpoints ───────────────────────────────────────────
const API = {
  COLOR_READ:      '/api/color/read',
  COLOR_CALIBRATE: '/api/color/calibrate',
  UPS_LATEST:      '/api/ups/latest',
  LED_STATUS:      '/api/led/status',
  LED_TOGGLE:      '/api/led/toggle',
  WIFI_SCAN:       '/api/wifi/scan',
  WIFI_CONNECT:    '/api/wifi/connect',
  WIFI_STATUS:     '/api/wifi/status',
};

const POLL_COLOR_MS = 500;
const POLL_UPS_MS   = 180_000;
const POLL_WIFI_MS  =  30_000;

let _selectedSsid         = null;
let _selectedSsidNeedsPwd = false;
let _colorPollTimer       = null;

// ─── Utilities ───────────────────────────────────────────────

async function apiFetch(url, opts = {}) {
  try {
    const res  = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
    return json;
  } catch (e) {
    console.error(`[apiFetch] ${url} →`, e.message);
    return null;
  }
}

function showToast(message, type = 'success') {
  const icons = { success: 'check-circle', danger: 'exclamation-circle', warning: 'exclamation-triangle', info: 'info-circle' };
  const id    = `toast-${Date.now()}`;
  document.getElementById('toastContainer').insertAdjacentHTML('beforeend', `
    <div id="${id}" class="toast align-items-center text-bg-${type} border-0"
         role="alert" aria-live="assertive" aria-atomic="true">
      <div class="d-flex">
        <div class="toast-body">
          <i class="fas fa-${icons[type] ?? 'info-circle'} me-2"></i>${message}
        </div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto"
                data-bs-dismiss="toast" aria-label="Tutup"></button>
      </div>
    </div>`);
  const el = document.getElementById(id);
  const t  = new bootstrap.Toast(el, { delay: 3500 });
  t.show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

function startClock() {
  const el   = document.getElementById('headerClock');
  const tick = () => {
    el.textContent = new Date().toLocaleTimeString('id-ID', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  };
  tick();
  setInterval(tick, 1000);
}

function fmtTime(ts) {
  if (!ts) return '--';
  try {
    return new Date(ts.replace(' ', 'T')).toLocaleTimeString('id-ID', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ─── View Navigation ─────────────────────────────────────────

const VIEW_TITLES = {
  home:      '<i class="fas fa-microchip" style="color:var(--cyan);font-size:0.82rem;"></i>&ensp;Pi Kiosk',
  sensor:    '<i class="fas fa-eye-dropper" style="color:var(--cyan);font-size:0.82rem;"></i>&ensp;Pembacaan Sensor',
  calibrate: '<i class="fas fa-sliders" style="color:var(--purple);font-size:0.82rem;"></i>&ensp;Kalibrasi',
};

function showView(name) {
  document.querySelectorAll('.view').forEach(el => el.classList.add('d-none'));
  document.getElementById(`view-${name}`).classList.remove('d-none');
  document.getElementById('navTitle').innerHTML = VIEW_TITLES[name] ?? 'Pi Kiosk';
  document.getElementById('btnBack').classList.toggle('d-none', name === 'home');

  // Kelola polling sensor warna: aktif hanya saat di view sensor
  if (name === 'sensor') {
    pollColor();
    _colorPollTimer = setInterval(pollColor, POLL_COLOR_MS);
  } else {
    if (_colorPollTimer) { clearInterval(_colorPollTimer); _colorPollTimer = null; }
  }
}

// ─── Color Sensor ────────────────────────────────────────────

async function pollColor() {
  const json  = await apiFetch(API.COLOR_READ);
  const badge = document.getElementById('colorPollBadge');
  if (!json) {
    badge.textContent = '● Error';
    badge.className   = 'sensor-status-chip chip-error';
    return;
  }
  const { r, g, b, c, hex } = json.data;
  const preview = document.getElementById('colorPreview');
  preview.style.backgroundColor = hex;
  const hexLabel       = document.getElementById('colorHexLabel');
  hexLabel.textContent = hex;
  const brightness     = (c > 0) ? ((r + g + b) / c) : 0;
  hexLabel.style.color = brightness > 1.2 ? '#1a1a1a' : '#f8f9fa';
  document.getElementById('valR').textContent = r;
  document.getElementById('valG').textContent = g;
  document.getElementById('valB').textContent = b;
  document.getElementById('valC').textContent = c;
  badge.textContent = '● Live';
  badge.className   = 'sensor-status-chip chip-live';
}

// ─── Kalibrasi ───────────────────────────────────────────────

async function saveCalibration() {
  const profileName = document.getElementById('calProfileName').value.trim();
  const rScale = parseFloat(document.getElementById('calRScale').value);
  const gScale = parseFloat(document.getElementById('calGScale').value);
  const bScale = parseFloat(document.getElementById('calBScale').value);

  if (!profileName) { showToast('Nama profil tidak boleh kosong.', 'warning'); return; }
  if ([rScale, gScale, bScale].some(v => isNaN(v) || v < 0.1 || v > 10.0)) {
    showToast('Nilai scale harus antara 0.1 – 10.0.', 'warning'); return;
  }

  const btn = document.getElementById('btnSaveCalibration');
  btn.disabled  = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Menyimpan...';

  const json = await apiFetch(API.COLOR_CALIBRATE, {
    method: 'POST',
    body:   JSON.stringify({ profile_name: profileName, r_scale: rScale, g_scale: gScale, b_scale: bScale }),
  });

  btn.disabled  = false;
  btn.innerHTML = '<i class="fas fa-save me-2"></i>Simpan & Aktifkan';

  if (json) {
    showToast(`Kalibrasi '${profileName}' berhasil disimpan.`, 'success');
    showView('home');
  } else {
    showToast('Gagal menyimpan kalibrasi.', 'danger');
  }
}

// ─── UPS Battery ─────────────────────────────────────────────

async function pollUps() {
  const json = await apiFetch(API.UPS_LATEST);
  if (!json || !json.data) {
    document.getElementById('navBatteryPct').textContent  = '--%';
    document.getElementById('upsPercentage').textContent  = '--%';
    document.getElementById('upsTimestamp').textContent   = 'Menunggu data...';
    return;
  }

  const { voltage, current, power, percentage, timestamp } = json.data;
  const [iconCls, colorCls] = _batteryIconClass(percentage);

  // Navbar
  document.getElementById('navBatteryPct').textContent  = `${Math.round(percentage)}%`;
  document.getElementById('navBatteryIcon').className   = `fas ${iconCls} ${colorCls}`;

  // Battery modal
  document.getElementById('upsVoltage').textContent     = voltage.toFixed(2);
  document.getElementById('upsCurrent').textContent     = current.toFixed(3);
  document.getElementById('upsPower').textContent       = power.toFixed(2);
  document.getElementById('upsPercentage').textContent  = `${percentage.toFixed(1)}%`;
  document.getElementById('upsTimestamp').textContent   = fmtTime(timestamp);
  const modalIco = document.getElementById('batteryModalIcon');
  if (modalIco) modalIco.className = `fas ${iconCls} me-2 ${colorCls}`;

  // Progress bar
  const bar = document.getElementById('upsProgressBar');
  bar.style.width = `${Math.min(100, percentage)}%`;
  bar.setAttribute('aria-valuenow', percentage);
  bar.classList.remove('bg-success', 'bg-warning', 'bg-danger');
  if      (percentage >= 60) bar.classList.add('bg-success');
  else if (percentage >= 25) bar.classList.add('bg-warning');
  else                       bar.classList.add('bg-danger');
}

function _batteryIconClass(pct) {
  if (pct >= 80) return ['fa-battery-full',          'batt-ok'];
  if (pct >= 60) return ['fa-battery-three-quarters', 'batt-ok'];
  if (pct >= 40) return ['fa-battery-half',           'batt-mid'];
  if (pct >= 15) return ['fa-battery-quarter',        'batt-low'];
  return              ['fa-battery-empty',            'batt-low'];
}

// ─── LED Control ─────────────────────────────────────────────

function updateLedUi(status) {
  const icon = document.getElementById('navLedIcon');
  const btn  = document.getElementById('btnNavLed');
  icon.className = 'fas fa-lightbulb';
  btn.classList.toggle('pill-on', status === 'on');
}

async function fetchLedStatus() {
  const json = await apiFetch(API.LED_STATUS);
  if (json) updateLedUi(json.data.status);
}

async function handleLedToggle() {
  const btn = document.getElementById('btnNavLed');
  btn.disabled = true;
  const json   = await apiFetch(API.LED_TOGGLE, { method: 'POST' });
  btn.disabled = false;
  if (json) {
    updateLedUi(json.data.status);
    showToast(json.message, 'success');
  } else {
    showToast('Gagal kontrol LED.', 'danger');
  }
}

// ─── WiFi Manager ────────────────────────────────────────────

function renderNetworkList(networks) {
  const container = document.getElementById('wifiNetworkList');
  if (!networks.length) {
    container.innerHTML = `
      <div class="text-muted text-center py-4 small">
        <i class="fas fa-exclamation-circle me-2"></i>Tidak ada jaringan ditemukan.
      </div>`;
    return;
  }
  container.innerHTML = networks.map(net => {
    const safeSsid    = escHtml(net.ssid);
    const signalBars  = _signalBars(net.signal);
    const lockIcon    = net.security !== 'Open'
      ? '<i class="fas fa-lock ms-1 small text-warning"></i>'
      : '<i class="fas fa-lock-open ms-1 small text-success"></i>';
    const activeBadge = net.in_use
      ? '<span class="badge bg-success ms-2" style="font-size:0.6rem;">Aktif</span>' : '';
    return `
      <div class="wifi-item ${net.in_use ? 'wifi-item-active' : ''}"
           data-ssid="${safeSsid}"
           onclick="selectSsid(${JSON.stringify(net.ssid)}, ${net.security !== 'Open'})">
        <div class="d-flex justify-content-between align-items-center">
          <div><span class="wifi-ssid">${safeSsid}</span>${activeBadge}${lockIcon}</div>
          <div class="text-end">
            <span class="signal-bars">${signalBars}</span>
            <span class="ms-1 small text-muted">${net.signal}%</span>
            <div class="small text-muted" style="font-size:0.65rem;">${escHtml(net.security)}</div>
          </div>
        </div>
      </div>`;
  }).join('');
}

function _signalBars(signal) {
  const filled = Math.round((signal / 100) * 5);
  return Array.from({ length: 5 }, (_, i) =>
    `<span class="${i < filled ? 'text-success' : 'text-secondary'}">▮</span>`
  ).join('');
}

function selectSsid(ssid, requiresPassword) {
  _selectedSsid         = ssid;
  _selectedSsidNeedsPwd = requiresPassword;
  document.querySelectorAll('.wifi-item').forEach(el => {
    el.classList.toggle('wifi-item-selected', el.dataset.ssid === ssid);
  });
  document.getElementById('selectedSsidDisplay').textContent = ssid;
  const pwdInput       = document.getElementById('wifiPassword');
  pwdInput.value       = '';
  pwdInput.placeholder = requiresPassword ? 'Masukkan password...' : 'Jaringan terbuka (tanpa password)';
  if (requiresPassword) pwdInput.focus();
  document.getElementById('btnWifiConnect').disabled = false;
}

async function scanWifi() {
  const btn       = document.getElementById('btnWifiScan');
  const container = document.getElementById('wifiNetworkList');
  btn.disabled  = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Scanning...';
  container.innerHTML = `
    <div class="text-muted text-center py-3 small">
      <span class="spinner-border spinner-border-sm me-2"></span>Mencari jaringan...
    </div>`;
  const json = await apiFetch(API.WIFI_SCAN);
  btn.disabled  = false;
  btn.innerHTML = '<i class="fas fa-search me-1"></i>Scan Jaringan';
  if (json) {
    renderNetworkList(json.data);
    showToast(json.message, 'info');
  } else {
    container.innerHTML = `
      <div class="text-danger text-center py-3 small">
        <i class="fas fa-exclamation-triangle me-2"></i>Scan gagal. Cek NetworkManager.
      </div>`;
    showToast('Gagal scan WiFi.', 'danger');
  }
}

async function connectWifi() {
  if (!_selectedSsid) { showToast('Pilih jaringan terlebih dahulu.', 'warning'); return; }
  const password = document.getElementById('wifiPassword').value;
  if (_selectedSsidNeedsPwd && !password) {
    showToast('Jaringan ini membutuhkan password.', 'warning');
    document.getElementById('wifiPassword').focus();
    return;
  }
  const btn = document.getElementById('btnWifiConnect');
  btn.disabled  = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Menghubungkan...';
  const json = await apiFetch(API.WIFI_CONNECT, {
    method: 'POST',
    body:   JSON.stringify({ ssid: _selectedSsid, password }),
  });
  btn.disabled  = false;
  btn.innerHTML = '<i class="fas fa-link me-1"></i>Hubungkan';
  if (json) {
    showToast(json.message, 'success');
    document.getElementById('wifiPassword').value = '';
    await fetchWifiStatus();
    await scanWifi();
  } else {
    showToast(`Gagal terhubung ke '${_selectedSsid}'.`, 'danger');
  }
}

async function fetchWifiStatus() {
  const json = await apiFetch(API.WIFI_STATUS);
  if (!json) return;
  const { connected, ssid } = json.data;
  const navIcon = document.getElementById('navWifiIcon');
  const navSsid = document.getElementById('navSsid');
  const btn     = document.getElementById('btnNavWifi');
  const badge   = document.getElementById('wifiStatusBadge');
  if (connected) {
    navIcon.className   = 'fas fa-wifi batt-ok';
    navSsid.textContent = ssid;
    btn.classList.add('pill-wifi-on');
    badge.className     = 'badge bg-success ms-2';
    badge.innerHTML     = `<i class="fas fa-circle me-1"></i>${escHtml(ssid)}`;
  } else {
    navIcon.className   = 'fas fa-wifi';
    navSsid.textContent = '--';
    btn.classList.remove('pill-wifi-on');
    badge.className     = 'badge bg-secondary ms-2';
    badge.innerHTML     = '<i class="fas fa-circle me-1"></i>Tidak Terhubung';
  }
}

// ─── Init ────────────────────────────────────────────────────

function init() {
  startClock();

  // Navigasi
  document.getElementById('btnGoSensor')
    .addEventListener('click', () => showView('sensor'));
  document.getElementById('btnGoCalibrate')
    .addEventListener('click', () => showView('calibrate'));
  document.getElementById('btnBack')
    .addEventListener('click', () => showView('home'));

  // LED navbar toggle
  document.getElementById('btnNavLed')
    .addEventListener('click', handleLedToggle);

  // Kalibrasi
  document.getElementById('btnSaveCalibration')
    .addEventListener('click', saveCalibration);

  // WiFi
  document.getElementById('btnWifiScan')
    .addEventListener('click', scanWifi);
  document.getElementById('btnWifiConnect')
    .addEventListener('click', connectWifi);
  document.getElementById('wifiPassword')
    .addEventListener('keydown', e => { if (e.key === 'Enter') connectWifi(); });
  document.getElementById('btnShowPassword').addEventListener('click', () => {
    const inp  = document.getElementById('wifiPassword');
    const icon = document.querySelector('#btnShowPassword i');
    const show = inp.type === 'password';
    inp.type       = show ? 'text' : 'password';
    icon.className = show ? 'fas fa-eye-slash' : 'fas fa-eye';
  });

  // Data awal
  fetchLedStatus();
  fetchWifiStatus();
  pollUps();

  // Polling interval (UPS & WiFi, color hanya saat view sensor aktif)
  setInterval(pollUps, POLL_UPS_MS);
  setInterval(fetchWifiStatus, POLL_WIFI_MS);

  console.info('[Pi Kiosk] Ready');
}

document.addEventListener('DOMContentLoaded', init);

