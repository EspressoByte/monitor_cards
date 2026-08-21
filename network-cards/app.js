// Network Cards — web UI. Devices and live status come from the Python backend.

const POLL_MS = 5000; // how often to refresh status from /api/status
const FLIP_RESET_MS = 60000; // auto-return a flipped card to front after this

const grid = document.getElementById("grid");
const toast = document.getElementById("toast");

// Pinned devices stay visible and sorted to the very top. Persisted in the
// browser so pins survive reloads (no backend needed).
const PIN_KEY = "networkcards.pins";
function loadPins() {
  try { return JSON.parse(localStorage.getItem(PIN_KEY)) || []; } catch { return []; }
}
const pinned = new Set(loadPins());
function savePins() {
  try { localStorage.setItem(PIN_KEY, JSON.stringify([...pinned])); } catch {}
}
const PIN_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1 0 .707c-.48.48-1.072.588-1.503.588-.177 0-.335-.018-.46-.039l-3.134 3.134a5.927 5.927 0 0 1 .16 1.013c.046.702-.032 1.687-.72 2.375a.5.5 0 0 1-.707 0l-2.829-2.828-3.182 3.182c-.195.195-1.219.902-1.414.707-.195-.195.512-1.22.707-1.414l3.182-3.182-2.828-2.829a.5.5 0 0 1 0-.707c.688-.688 1.673-.766 2.375-.72.43.034.787.103 1.013.16l3.134-3.133a2.772 2.772 0 0 1-.04-.461c0-.43.108-1.022.589-1.503a.5.5 0 0 1 .353-.146z"/></svg>';

// Hidden (buried) devices sink to the bottom. Mutually exclusive with pinned.
const BURIED_KEY = "networkcards.buried";
function loadBuried() {
  try { return JSON.parse(localStorage.getItem(BURIED_KEY)) || []; } catch { return []; }
}
const buried = new Set(loadBuried());
function saveBuried() {
  try { localStorage.setItem(BURIED_KEY, JSON.stringify([...buried])); } catch {}
}
const HIDE_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M13.359 11.238C15.06 9.72 16 8 16 8s-3-5.5-8-5.5a7.028 7.028 0 0 0-2.79.588l.77.771A5.944 5.944 0 0 1 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13.134 13.134 0 0 1 14.828 8c-.058.087-.122.183-.195.288-.335.48-.83 1.12-1.465 1.755-.165.165-.337.328-.517.486l.708.709z"/><path d="M11.297 9.176a3.5 3.5 0 0 0-4.474-4.474l.823.823a2.5 2.5 0 0 1 2.829 2.829l.822.822zm-2.943 1.299.822.822a3.5 3.5 0 0 1-4.474-4.474l.823.823a2.5 2.5 0 0 0 2.829 2.829z"/><path d="M3.35 5.47c-.18.16-.353.322-.518.487A13.134 13.134 0 0 0 1.172 8l.195.288c.335.48.83 1.12 1.465 1.755C4.121 11.332 5.881 12.5 8 12.5c.716 0 1.39-.133 2.02-.36l.77.772A7.029 7.029 0 0 1 8 13.5C3 13.5 0 8 0 8s.939-1.721 2.641-3.238l.708.709zm10.296 8.884-12-12 .708-.708 12 12-.708.708z"/></svg>';

// One account/username applied to every device's SSH (persisted per browser).
const ACCOUNT_KEY = "networkcards.account";
function getAccount() {
  return (localStorage.getItem(ACCOUNT_KEY) || "").trim() || "admin";
}
function initAccount() {
  const input = document.getElementById("account");
  input.value = localStorage.getItem(ACCOUNT_KEY) || "";
  input.addEventListener("input", () => {
    localStorage.setItem(ACCOUNT_KEY, input.value.trim());
  });
}

function statusLabel(s) {
  return { up: "Online", warn: "Degraded", down: "Offline" }[s] || s;
}

// Per-type device logos (extracted from logos.pptx), keyed by device `type`.
const TYPE_ICONS = {
  "Router": "icons/router.png",
  "Switch": "icons/switch.png",
  "Firewall": "icons/firewall.png",
  "Server": "icons/server.png",
  "Access Point": "icons/access-point.svg",
  "Wireless Controller": "icons/wireless-controller.png",
  "F5": "icons/f5.png",
  "Website": "icons/website.svg",
};

// SSH launch target OS. macOS opens an ssh:// link, which the browser hands to
// the OS handler (Terminal). Windows is a placeholder for now — the toggle just
// shows the logo and the SSH action is a no-op until it's built. Persisted per
// browser; defaults to macOS.
const SSH_OS_KEY = "networkcards.sshOs";
const APPLE_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M11.182.008C11.148-.03 9.923.023 8.857 1.18c-1.066 1.156-.902 2.482-.878 2.516.024.034 1.52.087 2.475-1.258.955-1.345.762-2.391.728-2.43Zm3.314 11.733c-.048-.096-2.325-1.234-2.113-3.422.212-2.189 1.675-2.789 1.698-2.854.023-.065-.597-.79-1.254-1.157a3.692 3.692 0 0 0-1.563-.434c-.108-.003-.483-.095-1.254.116-.508.139-1.653.589-1.968.607-.316.018-1.256-.522-2.267-.665-.647-.125-1.333.131-1.824.328-.49.196-1.422.754-2.074 2.237-.652 1.482-.311 3.83-.067 4.56.244.729.625 1.924 1.273 2.796.576.984 1.34 1.667 1.659 1.899.319.232 1.219.386 1.843.067.502-.308 1.408-.485 1.766-.472.357.013 1.061.154 1.782.539.571.197 1.111.115 1.652-.105.541-.221 1.324-1.059 2.238-2.758.347-.79.505-1.217.473-1.282Z"/></svg>';
const WINDOWS_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M6.555 1.375 0 2.237v5.45h6.555V1.375zM0 13.795l6.555.933V8.313H0v5.482zm7.278-5.4.026 6.378L16 16V8.395H7.278zM16 0 7.33 1.244v6.414H16V0z"/></svg>';
let sshOs = localStorage.getItem(SSH_OS_KEY) || "mac";

function initSshOs() {
  const btn = document.getElementById("ssh-os-toggle");
  const sync = () => {
    if (sshOs === "win") {
      btn.innerHTML = WINDOWS_SVG + "<span>Windows</span>";
      btn.title = "SSH target: Windows (not set up yet) — click to switch to macOS";
    } else {
      btn.innerHTML = APPLE_SVG + "<span>macOS</span>";
      btn.title = "SSH target: macOS (opens Terminal) — click to switch to Windows";
    }
  };
  sync();
  btn.addEventListener("click", () => {
    sshOs = sshOs === "win" ? "mac" : "win";
    localStorage.setItem(SSH_OS_KEY, sshOs);
    sync();
  });
}

// Global toggle: mask the IP shown on card fronts (e.g. for a shared monitor),
// while keeping the real IP in card.dataset.ip for SSH / mgmt links.
const HIDE_IP_KEY = "networkcards.hideIps";
let hideIps = localStorage.getItem(HIDE_IP_KEY) === "true";
const IP_MASK = "•••.•••.•••.•••";

function refreshIpMasking() {
  for (const card of grid.querySelectorAll(".card")) {
    const el = card.querySelector(".d-ip");
    // Only mask real IPs; websites show a URL (dataset.ip is empty) — leave it.
    if (el && card.dataset.ip) el.textContent = hideIps ? IP_MASK : card.dataset.ip;
  }
}
function initHideIp() {
  const btn = document.getElementById("f-hide-ip");
  btn.innerHTML = HIDE_SVG;
  const sync = () => {
    btn.classList.toggle("active", hideIps);
    btn.title = hideIps ? "Show IP addresses" : "Hide IP addresses";
  };
  sync();
  btn.addEventListener("click", () => {
    hideIps = !hideIps;
    localStorage.setItem(HIDE_IP_KEY, String(hideIps));
    sync();
    refreshIpMasking();
  });
}

// --- "Activated" timestamp + live downtime timer (shown when not Online) ----
function fmtTs(t) {
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const hh = String(t.getHours()).padStart(2, "0");
  const mm = String(t.getMinutes()).padStart(2, "0");
  return `${M[t.getMonth()]} ${t.getDate()} · ${hh}:${mm}`;
}
function fmtDur(ms) {
  let s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600);  s -= h * 3600;
  const m = Math.floor(s / 60);    s -= m * 60;
  return `${d}d ${h}h ${m}m ${s}s`;
}
// Counter color escalates with downtime: yellow after 15 min, red after 30 min.
function downtimeColor(ms) {
  const mins = ms / 60000;
  return mins >= 30 ? "var(--down)" : mins >= 15 ? "var(--warn)" : "";
}
// card.dataset.since holds epoch ms (or "") when the device left Online.
function updateDowntime(card) {
  const box = card.querySelector(".downtime");
  const sinceMs = Number(card.dataset.since);
  if (card.dataset.status === "up" || !sinceMs) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.querySelector(".dt-activated").textContent = "Activated - " + fmtTs(new Date(sinceMs));
  const timer = box.querySelector(".dt-timer");
  const ms = Date.now() - sinceMs;
  timer.textContent = fmtDur(ms);
  timer.style.color = downtimeColor(ms);
}
// One shared 1s timer updates every visible downtime counter.
function tickTimers() {
  for (const el of document.querySelectorAll(".downtime:not(.hidden) .dt-timer")) {
    const sinceMs = Number(el.closest(".card").dataset.since);
    if (!sinceMs) continue;
    const ms = Date.now() - sinceMs;
    el.textContent = fmtDur(ms);
    el.style.color = downtimeColor(ms);
  }
}

function buildCard(dev) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.hostname = dev.hostname;
  card.dataset.type = dev.type;
  card.dataset.site = dev.site;
  card.dataset.ip = dev.ip || "";   // empty for url-only websites (skips IP masking)
  card.dataset.url = dev.url || "";
  card.dataset.status = dev.status;
  // Lowercased blob of every searchable field, for the free-text search box.
  // serial/wlc are only set on APs built from the 9800 RESTCONF collector; they
  // make a card findable by serial number or by which controller it's joined to.
  card.dataset.search = [dev.hostname, dev.type, dev.vendor, dev.model, dev.desc,
    dev.location, dev.site, dev.ip, dev.url, dev.serial, dev.wlc]
    .filter(Boolean).join(" ").toLowerCase();
  // Address shown on the card: a website shows its URL, everything else its IP.
  const addr = dev.url || dev.ip || "";
  const backAddrLabel = dev.url ? "URL" : "Mgmt IP";
  const backAddr = dev.url || dev.mgmtIp || "";
  card.dataset.since = dev.since ? String(dev.since * 1000) : "";
  card.dataset.changed = dev.changed ? String(dev.changed * 1000) : "";
  if (pinned.has(dev.hostname)) card.classList.add("pinned");
  if (buried.has(dev.hostname)) card.classList.add("buried");

  card.innerHTML = `
    <div class="card-inner">
      <section class="face front">
        <div class="d-body">
          <div class="d-head">
            <div class="hostname" title="${dev.hostname}">${dev.hostname}</div>
          </div>
          <div class="dev-type">${dev.type} · ${dev.vendor}</div>
          <div class="d-ip" title="${addr}">${addr}</div>
          <div class="downtime hidden"><span class="dt-activated"></span><span class="dt-timer"></span></div>
          <div class="d-foot">
            ${TYPE_ICONS[dev.type] ? `<img class="dev-logo" src="${TYPE_ICONS[dev.type]}" alt="${dev.type}" title="${dev.type}">` : ""}
            <hr>
            <div class="d-footrow">
              <div class="d-controls">
                <button class="pin" type="button" title="Pin to top" aria-label="Pin device">${PIN_SVG}</button>
                <button class="hide-btn" type="button" title="Hide (send to bottom)" aria-label="Hide device">${HIDE_SVG}</button>
              </div>
              <div class="d-corner"><span class="dot ${dev.status}"></span><span class="status-label">${statusLabel(dev.status)}</span> · <span class="site">SITE ${dev.site}</span></div>
            </div>
          </div>
        </div>
      </section>

      <section class="face back">
        <button class="copy master-copy" type="button" title="Copy whole card (text only)" aria-label="Copy whole card">⧉</button>
        <h3>⚙️ <span>${dev.hostname}</span><button class="copy" type="button" data-copy="${dev.hostname}" title="Copy hostname" aria-label="Copy hostname">⧉</button></h3>
        <div class="rows">
          <div class="row"><span class="k">Site ID</span><span class="v">${dev.site}</span></div>
          <div class="row"><span class="k">${backAddrLabel}</span><span class="v val-copy"><button class="copy" type="button" data-copy="${backAddr}" title="Copy ${backAddrLabel}" aria-label="Copy ${backAddrLabel}">⧉</button><span>${backAddr}</span></span></div>
          ${dev.serial
            ? `<div class="row"><span class="k">Serial</span><span class="v val-copy"><button class="copy" type="button" data-copy="${dev.serial}" title="Copy serial" aria-label="Copy serial">⧉</button><span>${dev.serial}</span></span></div>`
            : `<div class="row"><span class="k">Vendor</span><span class="v">${dev.vendor}</span></div>`}
          <div class="row"><span class="k">Model</span><span class="v">${dev.model}</span></div>
          <div class="row"><span class="k">Location</span><span class="v">${dev.location}</span></div>
          <div class="row"><span class="k">Desc.</span><span class="v">${dev.desc}</span></div>
        </div>
      </section>
    </div>
  `;

  // Distinguish single-click (flip) from double-click (SSH).
  // A single click waits briefly; a second click within the window cancels it.
  let clickTimer = null;
  let flipResetTimer = null;
  const DBL_MS = 250;

  // Flip, and auto-return to front after FLIP_RESET_MS. Flipping back early
  // cancels the timer.
  function setFlipped(flipped) {
    card.classList.toggle("flipped", flipped);
    clearTimeout(flipResetTimer);
    if (flipped) {
      flipResetTimer = setTimeout(() => card.classList.remove("flipped"), FLIP_RESET_MS);
    }
  }

  card.addEventListener("click", () => {
    if (clickTimer) return; // a dblclick is forming
    clickTimer = setTimeout(() => {
      setFlipped(!card.classList.contains("flipped"));
      clickTimer = null;
    }, DBL_MS);
  });

  card.addEventListener("dblclick", (e) => {
    if (clickTimer) {
      clearTimeout(clickTimer);
      clickTimer = null;
    }
    // Ctrl (or Cmd on macOS) + double-click opens the mgmt web UI instead of SSH.
    if (e.ctrlKey || e.metaKey) openMgmt(dev);
    else sshConnect(dev);
  });

  // Per-field copy buttons: copy one value without flipping or SSHing the card.
  card.querySelectorAll(".copy:not(.master-copy)").forEach((btn) => {
    btn.addEventListener("dblclick", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyToClipboard(btn.dataset.copy, btn);
    });
  });

  // Master copy: copies the whole card as plain text (no icons/symbols).
  const masterBtn = card.querySelector(".master-copy");
  masterBtn.addEventListener("dblclick", (e) => e.stopPropagation());
  masterBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const text = [
      dev.hostname,
      `Site ID: ${dev.site}`,
      `${backAddrLabel}: ${backAddr}`,
      dev.serial ? `Serial: ${dev.serial}` : `Vendor: ${dev.vendor}`,
      `Model: ${dev.model}`,
      `Location: ${dev.location}`,
      `Desc: ${dev.desc}`,
    ].join("\n");
    copyToClipboard(text, masterBtn);
  });

  // Pin toggle: keeps the device at the top (and visible) regardless of
  // sort/filter. Stops propagation so it never flips or SSHes the card.
  const pinBtn = card.querySelector(".pin");
  pinBtn.addEventListener("dblclick", (e) => e.stopPropagation());
  pinBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (pinned.has(dev.hostname)) {
      pinned.delete(dev.hostname);
    } else {
      pinned.add(dev.hostname);
      buried.delete(dev.hostname); // pin and hide are mutually exclusive
      card.classList.remove("buried");
      saveBuried();
    }
    card.classList.toggle("pinned", pinned.has(dev.hostname));
    savePins();
    applyFilters();
    applySort();
  });

  // Hide: send the card to the bottom (mutually exclusive with pin).
  const hideBtn = card.querySelector(".hide-btn");
  hideBtn.addEventListener("dblclick", (e) => e.stopPropagation());
  hideBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (buried.has(dev.hostname)) {
      buried.delete(dev.hostname);
    } else {
      buried.add(dev.hostname);
      pinned.delete(dev.hostname);
      card.classList.remove("pinned");
      savePins();
    }
    card.classList.toggle("buried", buried.has(dev.hostname));
    saveBuried();
    applyFilters();
    applySort();
  });

  updateDowntime(card);
  return card;
}

async function copyToClipboard(text, btn) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      // Fallback for non-secure contexts (older browsers / file://).
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    flashCopied(btn);
  } catch (e) {
    console.warn("copy failed:", e);
  }
}

function flashCopied(btn) {
  if (btn.dataset.busy) return;
  btn.dataset.busy = "1";
  const original = btn.textContent;
  btn.textContent = "✓";
  btn.classList.add("copied");
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove("copied");
    delete btn.dataset.busy;
  }, 1200);
}

let toastTimer = null;
function showToast(text, ms = 2200) {
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), ms);
}

function sshConnect(dev) {
  if (sshOs === "win") {
    // Windows launch isn't built yet — the toggle just shows the logo.
    showToast("🪟 Windows SSH isn't set up yet");
    return;
  }
  // macOS: hand an ssh:// link to the OS, which opens it in Terminal. No
  // backend needed — the browser invokes the registered ssh:// handler.
  // For a url-only website, fall back to the URL's host so the default
  // double-click policy still has a target.
  let host = dev.ip;
  if (!host && dev.url) { try { host = new URL(dev.url).hostname; } catch {} }
  if (!host) { showToast("⚠ No host to SSH to"); return; }
  const url = `ssh://${getAccount()}@${host}`;
  window.location.href = url;
  showToast(`▶ ${url}`);
}

function openMgmt(dev) {
  // A website opens its own URL; other devices open their mgmt web UI by IP.
  const url = dev.url || `https://${dev.mgmtIp}`;
  window.open(url, "_blank", "noopener");
  showToast(`🌐 ${url}`);
}

// ---- Notifications -------------------------------------------------------
// Browser desktop notification on status change, with an in-page toast
// fallback (so it works even before/without notification permission).
let firstPoll = true; // don't alert on the baseline sweep

// Master alert toggle (persisted). Lets an engineer mute everything quickly
// during a big incident so they aren't flooded with notifications.
const ALERTS_KEY = "networkcards.alerts";
let alertsEnabled = localStorage.getItem(ALERTS_KEY) !== "false"; // default on
const alertsBtn = document.getElementById("alerts-toggle");

function updateAlertsBtn() {
  alertsBtn.textContent = alertsEnabled ? "🔔 Alerts On" : "🔕 Alerts Off";
  alertsBtn.classList.toggle("muted", !alertsEnabled);
}

async function requestPermissionIfNeeded() {
  if ("Notification" in window && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch {}
  }
}

function initHelp() {
  const btn = document.getElementById("help-btn");
  const pop = document.getElementById("help-popover");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    pop.classList.toggle("hidden");
  });
  pop.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => pop.classList.add("hidden"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") pop.classList.add("hidden");
  });
}

function initAlerts() {
  updateAlertsBtn();
  alertsBtn.addEventListener("click", async () => {
    alertsEnabled = !alertsEnabled;
    localStorage.setItem(ALERTS_KEY, String(alertsEnabled));
    updateAlertsBtn();
    if (alertsEnabled) await requestPermissionIfNeeded();
  });
  if (alertsEnabled) requestPermissionIfNeeded();
}

// Synthesize alert sounds in code (no audio files / libraries). Browsers block
// audio until a user gesture, so the context is created/resumed on first input.
let audioCtx = null;
function getAudio() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  } catch { return null; }
}
document.addEventListener("pointerdown", getAudio, { once: true });

function beep(freq, startOffset, duration, type = "sine", vol = 0.14) {
  const ctx = audioCtx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  osc.connect(gain).connect(ctx.destination);
  const t = ctx.currentTime + startOffset;
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.linearRampToValueAtTime(vol, t + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + duration);
  osc.start(t);
  osc.stop(t + duration + 0.02);
}

function playAlertSound(state) {
  if (!getAudio()) return;
  if (state === "down") {          // urgent descending two-tone
    beep(622, 0, 0.18, "square"); beep(466, 0.2, 0.3, "square");
  } else if (state === "warn") {   // single mid beep
    beep(523, 0, 0.2, "triangle");
  } else {                          // recovered: pleasant ascending two-tone
    beep(523, 0, 0.14); beep(784, 0.14, 0.24);
  }
}

function notifyChange(card, newState) {
  if (!alertsEnabled) return; // muted (silences sound too)
  const host = card.dataset.hostname;
  if (buried.has(host)) return; // hidden by this engineer -> stay silent about it

  const meta = `${card.dataset.type} · ${card.dataset.ip || card.dataset.url}`;
  const verb = newState === "down" ? "is DOWN"
    : newState === "warn" ? "is DEGRADED"
    : "RECOVERED (Online)";
  const dot = newState === "down" ? "🔴" : newState === "warn" ? "🟡" : "🟢";
  const title = `${dot} ${host} ${verb}`;
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body: meta, tag: host }); } catch {}
  }
  showToast(`${title} — ${meta}`, 5000);
  playAlertSound(newState);
}

// ---- Session change history ----------------------------------------------
// In-memory only: the last HISTORY_MAX status changes, newest first. Not
// persisted anywhere, so a page reload starts the log empty (by design).
const HISTORY_MAX = 100;
const changeLog = [];
const historyPop = document.getElementById("history-popover");

function fmtClock(ms) {
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
  return `${M[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()} · ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function logChange(card, from, to) {
  changeLog.unshift({
    time: Date.now(),
    hostname: card.dataset.hostname,
    type: card.dataset.type,
    from, to,
  });
  if (changeLog.length > HISTORY_MAX) changeLog.length = HISTORY_MAX;
  if (!historyPop.classList.contains("hidden")) renderHistory(); // live-update if open
}

function renderHistory() {
  if (changeLog.length === 0) {
    historyPop.innerHTML = `<p class="hist-empty">No changes this session yet.</p>`;
    return;
  }
  historyPop.innerHTML =
    `<ul class="hist-list">` +
    changeLog.map((e) => `
      <li class="hist-row">
        <span class="hist-time">${fmtClock(e.time)}</span>
        <span class="hist-detail">
          <span class="hist-host" title="${e.hostname}">${e.hostname}</span>
          <span class="hist-transition">
            <span class="dot ${e.from}"></span>${statusLabel(e.from)}
            <span class="hist-arrow">→</span>
            <span class="dot ${e.to}"></span>${statusLabel(e.to)}
          </span>
        </span>
      </li>`).join("") +
    `</ul>`;
}

// Settings (gear) popover holding the set-once controls (Account, SSH client,
// Hide IPs). Their behavior is wired by initAccount/initSshOs/initHideIp — this
// only opens/closes the menu.
function initSettings() {
  const btn = document.getElementById("settings-btn");
  const pop = document.getElementById("settings-popover");
  btn.addEventListener("click", (e) => { e.stopPropagation(); pop.classList.toggle("hidden"); });
  pop.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => pop.classList.add("hidden"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.classList.add("hidden"); });
}

function initHistory() {
  const btn = document.getElementById("history-btn");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const opening = historyPop.classList.contains("hidden");
    if (opening) renderHistory();
    historyPop.classList.toggle("hidden");
  });
  historyPop.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => historyPop.classList.add("hidden"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") historyPop.classList.add("hidden");
  });
}

// Update each card's status dot + label in place, without disturbing the
// flipped state or rebuilding the DOM.
function applyStatus(statuses) {
  for (const [hostname, info] of Object.entries(statuses)) {
    const state = info.status;
    const card = grid.querySelector(`[data-hostname="${CSS.escape(hostname)}"]`);
    if (!card) continue;
    const prev = card.dataset.status;
    const dot = card.querySelector(".dot");
    const label = card.querySelector(".status-label");
    if (dot) dot.className = `dot ${state}`;
    if (label) label.textContent = statusLabel(state);
    card.dataset.status = state;
    card.dataset.since = info.since ? String(info.since * 1000) : "";
    card.dataset.changed = info.changed ? String(info.changed * 1000) : "";
    updateDowntime(card);
    if (!firstPoll && prev !== state) { notifyChange(card, state); logChange(card, prev, state); }
  }
  firstPoll = false;
  applyFilters(); // a card may now match/leave an active Status filter
  // both these modes depend on live status, so re-sort after a poll
  if (sortMode === "dynamic" || sortMode === "recent") applySort();
}

// ---- Filtering ------------------------------------------------------------
// Type / Status / Site are multi-select toggle chips. Each facet stores the set
// of values the engineer has turned OFF (excluded) — so an empty set = "show
// all", and any device value NOT in the set is shown. Storing exclusions (vs.
// inclusions) means a newly-discovered type/site defaults to visible. Persisted
// per browser, like pins/hides.
const FACETS = ["type", "status", "site"];
const facetValues = { type: [], status: ["up", "warn", "down"], site: [] };
const excluded = {};
const FILTER_KEY = (f) => `networkcards.filter.${f}`;
function loadExcluded(f) {
  try { return new Set(JSON.parse(localStorage.getItem(FILTER_KEY(f))) || []); }
  catch { return new Set(); }
}
function saveExcluded(f) {
  try { localStorage.setItem(FILTER_KEY(f), JSON.stringify([...excluded[f]])); } catch {}
}
FACETS.forEach((f) => { excluded[f] = loadExcluded(f); });

let sortMode = "dynamic";                // set via the Sort popover (see setSort)
const searchEl = document.getElementById("f-search");
const summaryEl = document.getElementById("summary");
const empty = document.getElementById("empty");

// Dynamic sort order: offline floats to the top, then degraded, then online.
const STATUS_RANK = { down: 0, warn: 1, up: 2 };

function chipLabel(facet, v) {
  return facet === "status" ? statusLabel(v) : v;
}

// Render one chip per value into the facet's popover (the master chip is static
// markup in index.html).
function renderFacet(facet) {
  const box = document.querySelector(`#facet-${facet} .chips`);
  box.innerHTML = facetValues[facet].map((v) =>
    `<button type="button" class="chip" data-facet="${facet}" data-val="${v}">` +
    `${facet === "status" ? `<span class="chip-dot ${v}"></span>` : ""}` +
    `${chipLabel(facet, v)}</button>`).join("");
  syncFacet(facet);
}

// Reflect the excluded set into chip styling, the master chip, and the closed
// trigger button's summary/indicator.
function syncFacet(facet) {
  const root = document.getElementById(`facet-${facet}`);
  const ex = excluded[facet];
  root.querySelectorAll(".chips .chip[data-val]").forEach((ch) =>
    ch.classList.toggle("off", ex.has(ch.dataset.val)));
  const vals = facetValues[facet];
  const offCount = vals.filter((v) => ex.has(v)).length;
  const onCount = vals.length - offCount;
  const allOn = offCount === 0;
  const allOff = vals.length > 0 && offCount === vals.length;

  const master = root.querySelector(".chip-master");
  master.textContent = allOff ? "None" : "All";
  master.classList.toggle("off", !allOn);           // lit only when everything's on
  master.classList.toggle("partial", !allOn && !allOff);
  master.title = allOn ? "Turn all off" : "Turn all on";

  // Closed-state trigger: show "All", "None", or "N of M" + an active indicator.
  root.querySelector(".facet-summary").textContent =
    allOn ? "All" : allOff ? "None" : `${onCount} of ${vals.length}`;
  root.querySelector(".facet-btn").classList.toggle("filtered", !allOn);
}

// --- Facet dropdown open/close -------------------------------------------
function closeAllFacetPops() {
  document.querySelectorAll(".facet-pop").forEach((p) => p.classList.add("hidden"));
  document.querySelectorAll(".facet-btn").forEach((b) =>
    b.setAttribute("aria-expanded", "false"));
}
function toggleFacetPop(root) {
  const pop = root.querySelector(".facet-pop");
  const opening = pop.classList.contains("hidden");
  closeAllFacetPops();
  if (opening) {
    pop.classList.remove("hidden");
    root.querySelector(".facet-btn").setAttribute("aria-expanded", "true");
  }
}

// Sort uses the same dropdown shell but is single-select: highlight the active
// option and mirror its label into the closed trigger.
function syncSort() {
  const root = document.getElementById("facet-sort");
  root.querySelectorAll(".sort-opt").forEach((c) =>
    c.classList.toggle("off", c.dataset.val !== sortMode));
  const active = root.querySelector(`.sort-opt[data-val="${sortMode}"]`);
  if (active) root.querySelector(".facet-summary").textContent = active.textContent;
}
function setSort(mode) {
  sortMode = mode;
  syncSort();
  applySort();
}

function toggleChip(facet, v) {
  const ex = excluded[facet];
  if (ex.has(v)) ex.delete(v); else ex.add(v);
  saveExcluded(facet);
  syncFacet(facet);
  applyFilters();
}

// Master: all-on -> turn everything off; otherwise -> turn everything on.
function toggleMaster(facet) {
  const ex = excluded[facet];
  if (facetValues[facet].every((v) => !ex.has(v))) {
    facetValues[facet].forEach((v) => ex.add(v));
  } else {
    ex.clear();
  }
  saveExcluded(facet);
  syncFacet(facet);
  applyFilters();
}

function populateFilters(devices) {
  facetValues.type = [...new Set(devices.map((d) => d.type))].sort();
  facetValues.site = [...new Set(devices.map((d) => String(d.site)))].sort();
  // Drop any stored exclusions for values that no longer exist.
  ["type", "site"].forEach((f) => {
    const valid = new Set(facetValues[f]);
    excluded[f] = new Set([...excluded[f]].filter((v) => valid.has(v)));
    saveExcluded(f);
  });
  FACETS.forEach(renderFacet);
  syncSort();

  document.querySelector(".filters").addEventListener("click", (e) => {
    const trigger = e.target.closest(".facet-btn");
    if (trigger) {
      e.stopPropagation();                 // don't let the outside-click handler re-close it
      toggleFacetPop(trigger.closest(".facet"));
      return;
    }
    const sortOpt = e.target.closest(".sort-opt");
    if (sortOpt) { setSort(sortOpt.dataset.val); closeAllFacetPops(); return; }
    const chip = e.target.closest(".chip");
    if (!chip) return;                     // chips stay in the open popover (no close)
    if (chip.dataset.master) toggleMaster(chip.dataset.facet);
    else toggleChip(chip.dataset.facet, chip.dataset.val);
  });
  // Close any open facet dropdown on an outside click or Escape.
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".facet")) closeAllFacetPops();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllFacetPops();
  });
  searchEl.addEventListener("input", applyFilters);
  document.getElementById("f-reset").addEventListener("click", () => {
    FACETS.forEach((f) => { excluded[f].clear(); saveExcluded(f); syncFacet(f); });
    searchEl.value = "";
    applyFilters();
  });
  // Summary pills double as a status filter: clicking one toggles that status
  // chip (they share the excluded.status set, so both stay in sync).
  summaryEl.addEventListener("click", (e) => {
    const pill = e.target.closest(".sum-pill");
    if (pill) toggleChip("status", pill.dataset.status);
  });
}

// Live status counts across the VISIBLE cards only (excludes hidden + filtered-
// out). Rendered as pills; a pill whose status is filtered out shows dimmed.
function updateSummary() {
  const counts = { up: 0, warn: 0, down: 0 };
  grid.querySelectorAll(".card:not(.hidden)").forEach((c) => {
    if (c.dataset.status in counts) counts[c.dataset.status]++;
  });
  summaryEl.innerHTML = ["up", "warn", "down"].map((s) =>
    `<button type="button" class="sum-pill ${s}${excluded.status.has(s) ? " off" : ""}" data-status="${s}">
       <span class="sum-dot ${s}"></span>${counts[s]} ${statusLabel(s)}</button>`).join("");
}

// Reorder the cards in the grid. Hidden (filtered-out) cards are reordered too;
// they just stay hidden. Ties break alphabetically by hostname.
function applySort() {
  const mode = sortMode;
  const byHost = (a, b) =>
    a.dataset.hostname.localeCompare(b.dataset.hostname, undefined, { numeric: true });
  const cards = [...grid.querySelectorAll(".card")];
  cards.sort((a, b) => {
    // Pinned devices always come first; hidden (buried) ones always last.
    const ap = pinned.has(a.dataset.hostname);
    const bp = pinned.has(b.dataset.hostname);
    if (ap !== bp) return ap ? -1 : 1;
    const ah = buried.has(a.dataset.hostname);
    const bh = buried.has(b.dataset.hostname);
    if (ah !== bh) return ah ? 1 : -1;
    if (mode === "recent") {
      // most recently changed status first; never-changed sort last
      const ac = Number(a.dataset.changed) || 0;
      const bc = Number(b.dataset.changed) || 0;
      return (bc - ac) || byHost(a, b);
    }
    if (mode === "dynamic") {
      const r = (STATUS_RANK[a.dataset.status] ?? 9) - (STATUS_RANK[b.dataset.status] ?? 9);
      return r || byHost(a, b);
    }
    if (mode === "site") {
      return a.dataset.site.localeCompare(b.dataset.site, undefined, { numeric: true })
        || byHost(a, b);
    }
    if (mode === "type") {
      return a.dataset.type.localeCompare(b.dataset.type) || byHost(a, b);
    }
    return byHost(a, b); // hostname
  });
  cards.forEach((c) => grid.appendChild(c));
}

function applyFilters() {
  const term = searchEl.value.trim().toLowerCase();
  let visible = 0;
  grid.querySelectorAll(".card").forEach((card) => {
    // Search narrows everything; the chip facets still let pinned cards bypass.
    const matchSearch = !term || card.dataset.search.includes(term);
    const match = matchSearch && (
      pinned.has(card.dataset.hostname) || (
        !excluded.type.has(card.dataset.type) &&
        !excluded.status.has(card.dataset.status) &&
        !excluded.site.has(card.dataset.site)));
    card.classList.toggle("hidden", !match);
    if (match) visible++;
  });
  empty.classList.toggle("hidden", visible !== 0);
  updateSummary();
}

// --- Connection watchdog: loud banner when we can't reach the backend --------
const connBanner = document.getElementById("conn-banner");
let pollFails = 0;
let lastOkAt = Date.now();
const FAIL_THRESHOLD = 2; // consecutive misses (~10s) before sounding the alarm

function markReachable() {
  pollFails = 0;
  lastOkAt = Date.now();
  if (!connBanner.classList.contains("hidden")) {
    connBanner.classList.add("hidden");
    document.body.classList.remove("has-banner");
    document.title = "Network Cards";
  }
}
function markUnreachable() {
  pollFails++;
  if (pollFails < FAIL_THRESHOLD) return;
  if (connBanner.classList.contains("hidden")) {
    connBanner.classList.remove("hidden");
    document.body.classList.add("has-banner");
    document.title = "⚠ DISCONNECTED — Network Cards";
  }
  updateConnBanner();
}
function updateConnBanner() {
  if (connBanner.classList.contains("hidden")) return;
  const secs = Math.round((Date.now() - lastOkAt) / 1000);
  connBanner.textContent =
    `⚠ CANNOT REACH SERVER — status is NOT updating (last contact ${secs}s ago)`;
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    applyStatus(await res.json());
    markReachable();
  } catch (e) {
    markUnreachable();
  }
}

let pollingStarted = false;
async function init() {
  try {
    const res = await fetch("/api/devices", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const devices = await res.json();
    grid.innerHTML = ""; // idempotent: safe if init retried after a failure
    devices.forEach((dev) => grid.appendChild(buildCard(dev)));
    populateFilters(devices);
    applySort();
    applyFilters(); // render the summary counts on first load
    refreshIpMasking();
    initAlerts();
    markReachable();
  } catch (e) {
    console.error("Could not load devices. Run server.py and open via http://", e);
    markUnreachable();
    setTimeout(init, POLL_MS); // keep retrying so it recovers on its own
    return;
  }
  if (!pollingStarted) {
    pollingStarted = true;
    setInterval(pollStatus, POLL_MS);
  }
}

initHelp();
initAccount();
initHideIp();
initSshOs();
initSettings();
initHistory();
setInterval(() => { tickTimers(); updateConnBanner(); }, 1000);
init();
