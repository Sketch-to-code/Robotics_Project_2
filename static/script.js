'use strict';

const $ = id => document.getElementById(id);
const N_SLOTS = 3;

let settings = {};
let simulate = true;
let mode = 'manual';          // 'manual' | 'camera'
let busy = false;
let canPlace = false;
let state = emptyState();
let lastPose = null;

function emptyState() {
  return { target: [], filled: [], queue: [], next_digit: null, feed: null };
}

function esc(t) {
  return String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------
async function api(url, opts) {
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    return { status: 'error', message: 'Cannot reach the dashboard server. Is app.py still running?' };
  }
  try {
    return await res.json();
  } catch (e) {
    return { status: 'error', message: `Server error (${res.status}). Check the terminal that runs app.py.` };
  }
}

function postJson(url, body) {
  return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function say(html, kind) {
  const el = $('message');
  el.className = 'message ' + (kind || '');
  el.innerHTML = html;
}

function setBusy(b) {
  busy = b;
  refreshButtons();
}

function refreshButtons() {
  ['startBtn', 'resetBtn', 'homeBtn', 'scanBtn', 'armGo', 'readBtn'].forEach(id => { $(id).disabled = busy; });
  $('nextBtn').disabled = busy || !canPlace;
}

function setChip(id, text, cls, title) {
  const el = $(id);
  el.textContent = text;
  el.className = 'chip' + (cls ? ' ' + cls : '');
  el.title = title || 'Click to re-check';
}

// ---------------------------------------------------------------------
// settings, simulate toggle, status chips
// ---------------------------------------------------------------------
async function loadSettings() {
  const data = await api('/api/settings');
  if (data.status !== 'ok') return;
  settings = data.settings;
  $('phoneIp').value = settings.phone_ip || '';
  $('phonePort').value = settings.phone_port || 8080;
  $('cameraRotation').value = String(settings.camera_rotation || 0);
  $('armSerialPort').value = settings.arm_serial_port || '';
  applySimulate();
  applyRotation();
  showRoiInfo();
}

function applySimulate() {
  simulate = settings.simulate_arm !== false;
  const b = $('simToggle');
  b.textContent = simulate ? 'SIMULATING' : 'LIVE ARM';
  b.classList.toggle('live', !simulate);
}

async function toggleSimulate() {
  if (busy) return;
  if (simulate && !confirm('Go LIVE? The arm will really move. Keep hands and cubes clear, and keep a hand near the servo power switch.')) return;
  const data = await postJson('/api/settings', { simulate_arm: !simulate });
  if (data.status === 'ok') {
    settings = data.settings;
    applySimulate();
    refreshHealth();
  } else {
    say(esc(data.message || 'Could not change the mode.'), 'err');
  }
}

function openSettings() { $('settingsStatus').textContent = ''; $('settingsOverlay').classList.remove('hidden'); }
function closeSettings() { $('settingsOverlay').classList.add('hidden'); }

async function saveSettings() {
  $('settingsStatus').textContent = 'Saving...';
  const data = await postJson('/api/settings', {
    phone_ip: $('phoneIp').value.trim(),
    phone_port: parseInt($('phonePort').value, 10) || 8080,
    camera_rotation: parseInt($('cameraRotation').value, 10) || 0,
    arm_serial_port: $('armSerialPort').value.trim(),
  });
  if (data.status === 'ok') {
    settings = data.settings;
    $('settingsStatus').textContent = 'Saved.';
    applyRotation();
    refreshHealth();
  } else {
    $('settingsStatus').textContent = data.message || 'Could not save.';
  }
}

async function refreshHealth() {
  setChip('chipArm', 'ARM ...', '');
  if (mode === 'camera') setChip('chipCam', 'CAMERA ...', '');
  const data = await api('/api/health' + (mode === 'camera' ? '?camera=1' : ''));
  if (data.status !== 'ok') {
    setChip('chipArm', 'ARM ?', 'bad', data.message);
    return;
  }
  const a = data.arm || {};
  if (a.state === 'simulated') setChip('chipArm', 'ARM SIMULATED', 'warn', 'Simulate is on: nothing is sent to the arm.');
  else if (a.state === 'connected') setChip('chipArm', 'ARM CONNECTED', 'ok');
  else setChip('chipArm', 'ARM NOT CONNECTED', 'bad', a.message || '');
  if (data.camera) {
    const c = data.camera;
    if (c.state === 'ready') setChip('chipCam', 'CAMERA READY', 'ok');
    else if (c.state === 'unset') setChip('chipCam', 'CAMERA NOT SET', 'warn', c.message);
    else setChip('chipCam', 'CAMERA OFFLINE', 'bad', c.message || '');
  }
}

// ---------------------------------------------------------------------
// mode
// ---------------------------------------------------------------------
function setMode(m, silent) {
  mode = m;
  try { localStorage.setItem('sortbenchMode', m); } catch (e) { /* storage may be blocked */ }
  $('modeManual').classList.toggle('active', m === 'manual');
  $('modeCamera').classList.toggle('active', m === 'camera');
  $('manualInput').classList.toggle('hidden', m !== 'manual');
  $('cameraInput').classList.toggle('hidden', m !== 'camera');
  $('cameraCard').classList.toggle('hidden', m !== 'camera');
  $('chipCam').classList.toggle('hidden', m !== 'camera');
  if (!silent) {
    if (state.target.length) resetSession(true);
    else renderState(state);
    refreshHealth();
  }
}

// ---------------------------------------------------------------------
// the sort session
// ---------------------------------------------------------------------
function parseDigits(text) {
  const parts = String(text).split(/[\s,;]+/).filter(Boolean);
  if (parts.length !== N_SLOTS || !parts.every(p => /^\d$/.test(p))) return null;
  return parts.map(Number);
}

function nextSlotOf(st) {
  if (st.next_digit === null || st.next_digit === undefined) return -1;
  return st.target.findIndex((d, i) => d === st.next_digit && !st.filled[i]);
}

function renderState(st) {
  state = (st && st.target) ? { ...emptyState(), ...st } : emptyState();
  const t = state.target, f = state.filled;
  const nextSlot = nextSlotOf(state);

  let tiles = '';
  for (let i = 0; i < N_SLOTS; i++) {
    if (!t.length) {
      tiles += `<div class="slot empty"><div class="slot-label">SLOT ${i + 1}</div><div class="slot-digit">-</div><div class="slot-state">empty</div></div>`;
    } else {
      const cls = f[i] ? 'filled' : (i === nextSlot ? 'next' : '');
      const label = f[i] ? 'placed' : (i === nextSlot ? 'next' : 'waiting');
      tiles += `<div class="slot ${cls}"><div class="slot-label">SLOT ${i + 1}</div><div class="slot-digit">${t[i]}</div><div class="slot-state">${label}</div></div>`;
    }
  }
  $('slots').innerHTML = tiles;

  const done = t.length > 0 && f.every(Boolean);
  canPlace = t.length > 0 && !done;
  let prompt, label = 'PLACE CUBE';
  if (!t.length) {
    prompt = mode === 'manual' ? 'Type the digits above and press START.' : 'Scan the cubes, check the digits, then press START.';
  } else if (done) {
    prompt = '<b>All cubes are sorted.</b> Press RESET to start a new round.';
  } else if (state.next_digit !== null && state.next_digit !== undefined) {
    prompt = `Put cube <b>${state.next_digit}</b> on the pick spot, then press PLACE CUBE. It goes to slot <b>${nextSlot + 1}</b>.`;
    label = `PLACE CUBE ${state.next_digit}`;
  } else {
    prompt = 'Put <b>one</b> cube on the pick spot, then press PLACE CUBE. The camera reads which one it is.';
  }
  $('prompt').innerHTML = prompt;
  $('nextBtn').textContent = label;
  refreshButtons();
}

async function startSession() {
  if (busy) return;
  const raw = mode === 'manual' ? $('digitsManual').value : $('digitsCamera').value;
  const digits = parseDigits(raw);
  if (!digits) {
    say('Enter exactly 3 digits (0 to 9), for example <b>3, 8, 2</b>' + (mode === 'camera' ? ', or press SCAN CUBES first.' : '.'), 'err');
    return;
  }
  setBusy(true);
  say('<span class="spin"></span>Planning the slots...', 'note');
  const data = await api(`/api/handoff/start?mode=${encodeURIComponent($('sortMode').value)}&digits=${digits.join(',')}&feed=${mode}`);
  setBusy(false);
  if (data.status !== 'ok') {
    say(esc(data.message || 'Could not start.'), 'err');
    return;
  }
  renderState(data);
  say(simulate ? 'Ready. Simulate is ON, so the arm will not move.' : 'Ready.', 'note');
}

async function placeNext() {
  if (busy || !canPlace) return;
  setBusy(true);
  say(simulate ? '<span class="spin"></span>Simulating...'
               : '<span class="spin"></span>Moving the arm. This takes about 30 seconds, keep clear of the arm.', 'note');
  const data = await api('/api/handoff/next');
  if (data.status === 'placed' || data.status === 'simulated') {
    const how = mode === 'camera' ? ` (camera read it with ${data.confidence}% confidence)` : '';
    const sim = data.status === 'simulated'
      ? '<br>SIMULATED: the arm did NOT move. Click SIMULATING in the top bar to go live.' : '';
    say(`Cube <b>${data.digit}</b> went to slot <b>${data.slot + 1}</b>${how}.${sim}`, data.status === 'simulated' ? 'warn' : 'ok');
  } else if (data.status === 'done') {
    say('All slots are full.', 'ok');
  } else if (data.status === 'busy') {
    say('The arm is still moving. Wait a moment.', 'warn');
  } else {
    let extra = '';
    if (data.centroids && data.centroids.length) {
      extra = `<br><span class="note">Cubes seen at (px): ${esc(JSON.stringify(data.centroids))}. Use Camera tools, SET PICK AREA, so only the pick spot counts.</span>`;
    }
    say(esc(data.message || 'Something went wrong.') + extra, 'err');
  }
  const st = await api('/api/handoff/state');
  renderState(st.status === 'ok' ? st : state);
  setBusy(false);
}

async function resetSession(silent) {
  if (busy) return;
  await api('/api/handoff/reset');
  renderState(null);
  say(silent ? '' : 'Reset.', 'note');
}

async function homeArm() {
  if (busy) return;
  if (!simulate && !confirm('Move the arm back to HOME? It raises, rotates back and opens the gripper (a held cube will drop).')) return;
  setBusy(true);
  say('<span class="spin"></span>Returning the arm to HOME...', 'note');
  const data = await api('/api/handoff/home');
  const kind = data.status === 'ok' ? 'ok' : (data.status === 'error' ? 'err' : 'warn');
  say(esc(data.message || ''), kind);
  setBusy(false);
}

// ---------------------------------------------------------------------
// camera mode: scan, live view, pick area
// ---------------------------------------------------------------------
async function scanCubes(fromUpload) {
  if (busy) return;
  setBusy(true);
  $('scanInfo').innerHTML = '<span class="spin"></span>Looking at the tray...';
  const data = await api('/api/handoff/scan' + (fromUpload ? '?source=upload' : ''));
  setBusy(false);
  if (data.status !== 'ok') {
    $('scanInfo').innerHTML = `<span style="color:var(--red)">${esc(data.message || 'Scan failed.')}</span>`;
    return;
  }
  $('digitsCamera').value = data.digits.join(', ');
  $('scanImg').src = data.annotated_image_url + '?_=' + Date.now();
  $('scanImg').classList.remove('hidden');
  const warnings = [];
  if (!data.count_ok) warnings.push(`Expected ${data.expected} cubes but found ${data.digits.length}. Fix the digits below, or move the cubes and scan again.`);
  if (data.low_confidence && data.low_confidence.length) {
    warnings.push('Not sure about: ' + data.low_confidence.map(c => `${c.digit} (${c.confidence}%)`).join(', ') + '. Check these digits.');
  }
  $('scanInfo').innerHTML = `Found <b>${data.digits.length}</b> cube(s): ${esc(data.digits.join(', '))}.` +
    (warnings.length ? `<br><span style="color:var(--amber)">${esc(warnings.join(' '))}</span>` : '');
}

async function scanPhotoFile() {
  const file = $('photoFile').files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  $('scanInfo').innerHTML = '<span class="spin"></span>Uploading...';
  const up = await api('/api/upload', { method: 'POST', body: fd });
  if (up.status !== 'ok') {
    $('scanInfo').textContent = 'Upload failed: ' + (up.message || '');
    return;
  }
  await scanCubes(true);
}

function applyRotation() {
  const deg = parseInt(settings.camera_rotation, 10) || 0;
  $('liveFeed').style.transform = deg ? `rotate(${deg}deg)` : 'none';
}

function toggleLive() {
  const wrap = $('liveWrap');
  if (!wrap.classList.contains('hidden')) {
    $('liveFeed').src = '';
    wrap.classList.add('hidden');
    $('liveBtn').textContent = 'SHOW LIVE VIEW';
    return;
  }
  const ip = settings.phone_ip;
  if (!ip) {
    say('Enter the phone IP in SETTINGS first.', 'err');
    openSettings();
    return;
  }
  $('liveFeed').src = `http://${ip}:${settings.phone_port || 8080}/video?_=${Date.now()}`;
  wrap.classList.remove('hidden');
  $('liveBtn').textContent = 'HIDE LIVE VIEW';
  applyRotation();
}

// pick area: drag a box on a snapshot; saved in image pixels
let roiDrag = null;       // {x, y} where the drag started, in displayed pixels
let roiShown = null;      // {x0, y0, x1, y1} in displayed pixels

function showRoiInfo() {
  const r = settings.handoff_roi;
  $('roiInfo').textContent = (r && r.length === 4)
    ? `Pick area set: x ${r[0]} to ${r[2]}, y ${r[1]} to ${r[3]} (image pixels). Only a cube inside it counts.`
    : 'No pick area set: the camera accepts a cube anywhere in view, which only works while one cube is visible.';
}

async function startRoi() {
  $('roiInfo').innerHTML = '<span class="spin"></span>Taking a snapshot...';
  const data = await api('/api/camera/snapshot');
  if (data.status !== 'ok') {
    $('roiInfo').innerHTML = `<span style="color:var(--red)">${esc(data.message || 'Snapshot failed.')}</span>`;
    return;
  }
  roiShown = null;
  $('roiBox').classList.add('hidden');
  $('roiImg').src = data.image_url + '?_=' + Date.now();
  $('roiStage').classList.remove('hidden');
  $('roiActions').classList.remove('hidden');
  $('roiInfo').textContent = 'Drag a box around ONLY the spot where you will put each cube, then press SAVE PICK AREA.';
}

function roiPoint(e) {
  const r = $('roiImg').getBoundingClientRect();
  return { x: Math.min(Math.max(e.clientX - r.left, 0), r.width), y: Math.min(Math.max(e.clientY - r.top, 0), r.height) };
}

function drawRoi(a, b) {
  roiShown = { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) };
  const box = $('roiBox');
  box.style.left = roiShown.x0 + 'px';
  box.style.top = roiShown.y0 + 'px';
  box.style.width = (roiShown.x1 - roiShown.x0) + 'px';
  box.style.height = (roiShown.y1 - roiShown.y0) + 'px';
  box.classList.remove('hidden');
}

async function saveRoi() {
  const img = $('roiImg');
  const r = img.getBoundingClientRect();
  if (!roiShown || (roiShown.x1 - roiShown.x0) < 5 || (roiShown.y1 - roiShown.y0) < 5) {
    $('roiInfo').textContent = 'Drag a box on the picture first.';
    return;
  }
  const sx = img.naturalWidth / r.width, sy = img.naturalHeight / r.height;
  const roi = [roiShown.x0 * sx, roiShown.y0 * sy, roiShown.x1 * sx, roiShown.y1 * sy].map(Math.round);
  const data = await postJson('/api/settings', { handoff_roi: roi });
  if (data.status === 'ok') {
    settings = data.settings;
    showRoiInfo();
    $('roiStage').classList.add('hidden');
    $('roiActions').classList.add('hidden');
  } else {
    $('roiInfo').textContent = data.message || 'Could not save the pick area.';
  }
}

async function clearRoi() {
  const data = await postJson('/api/settings', { handoff_roi: null });
  if (data.status === 'ok') {
    settings = data.settings;
    showRoiInfo();
  }
}

// ---------------------------------------------------------------------
// manual arm control
// ---------------------------------------------------------------------
function armMsg(text, kind) {
  const el = $('armMsg');
  el.textContent = text;
  el.style.color = kind === 'err' ? 'var(--red)' : (kind === 'ok' ? 'var(--green)' : (kind === 'warn' ? 'var(--amber)' : ''));
}

function renderPose(limits) {
  ['base', 'shoulder', 'elbow', 'gripper'].forEach(j => {
    $('pose_' + j).textContent = (lastPose && lastPose[j] !== undefined) ? lastPose[j] + '°' : '-';
    if (limits && limits[j]) $('range_' + j).textContent = `${limits[j][0]} to ${limits[j][1]}`;
  });
}

function parseArmCommand(text) {
  const re = /(base|shoulder|elbow|gripper|grip)\s*[=:]?\s*(-?\d+(?:\.\d+)?)/gi;
  const out = {};
  let m;
  while ((m = re.exec(text)) !== null) {
    let j = m[1].toLowerCase();
    if (j === 'grip') j = 'gripper';
    out[j] = parseFloat(m[2]);
  }
  return out;
}

async function runArmCommand() {
  const joints = parseArmCommand($('armCmd').value);
  if (!Object.keys(joints).length) {
    armMsg('Type something like: base 56, shoulder 40', 'err');
    return;
  }
  await moveArm(joints);
}

async function moveArm(joints) {
  if (busy) return;
  const qs = Object.entries(joints).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  setBusy(true);
  armMsg('Moving...', '');
  const data = await api('/api/arm/move?' + qs);
  setBusy(false);
  if (data.pose) { lastPose = data.pose; renderPose(); }
  if (data.status === 'moved') {
    const fmt = o => ['base', 'shoulder', 'elbow', 'gripper'].filter(k => k in o).map(k => `${k} ${o[k]}`).join(', ');
    const what = fmt(data.moved);
    const cl = data.clamped && Object.keys(data.clamped).length
      ? ' (limited to the safe range: ' + fmt(data.clamped) + ')' : '';
    armMsg('Moved: ' + what + cl, cl ? 'warn' : 'ok');
  } else if (data.status === 'simulated') {
    armMsg('Simulate is ON, so the arm did not move. Click SIMULATING in the top bar to go live.', 'warn');
  } else {
    armMsg(data.message || 'Move failed.', 'err');
  }
}

function jog(joint, delta) {
  if (!lastPose || lastPose[joint] === undefined) {
    armMsg('Press READ ARM first.', 'err');
    return;
  }
  moveArm({ [joint]: lastPose[joint] + delta });
}

async function refreshPose() {
  if (busy) return;
  const data = await api('/api/arm/pose');
  if (data.status === 'ok') {
    lastPose = data.pose;
    renderPose(data.limits);
    armMsg('', '');
  } else {
    if (data.limits) renderPose(data.limits);
    armMsg(data.message || 'Could not read the arm.', data.status === 'simulated' ? 'warn' : 'err');
  }
}

// ---------------------------------------------------------------------
// start-up
// ---------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  $('armCmd').addEventListener('keydown', e => { if (e.key === 'Enter') runArmCommand(); });
  $('armCard').addEventListener('toggle', () => { if ($('armCard').open) refreshPose(); });

  const stage = $('roiStage');
  stage.addEventListener('mousedown', e => { roiDrag = roiPoint(e); drawRoi(roiDrag, roiDrag); e.preventDefault(); });
  window.addEventListener('mousemove', e => { if (roiDrag) drawRoi(roiDrag, roiPoint(e)); });
  window.addEventListener('mouseup', () => { roiDrag = null; });

  await loadSettings();
  let saved = 'manual';
  try { saved = localStorage.getItem('sortbenchMode') || 'manual'; } catch (e) { /* ignore */ }
  setMode(saved === 'camera' ? 'camera' : 'manual', true);
  const st = await api('/api/handoff/state');
  renderState(st.status === 'ok' ? st : null);
  refreshHealth();
});
