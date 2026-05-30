// keycloak-js 26.x is ESM-only (the bundled adapter at /auth/js/keycloak.js
// was dropped from Keycloak in 25+). The adapter is now self-hosted as a
// sibling file and imported here, which is why this script is loaded with
// type="module" in index.html.
import Keycloak from "./keycloak.js";

// Keycloak config. Realm and client must match iot-realm.json in Keycloak.
const KEYCLOAK_CONFIG = {
  url: "https://www.lab.local/auth",
  realm: "iot",
  clientId: "dashboard-client",
};

const API_BASE = "https://www.lab.local";
const SENSOR_DATA_ENDPOINT = `${API_BASE}/api/v1/sensor-data`;

// In live mode (no "to" date) we reload once per minute.
const LIVE_POLL_INTERVAL_MS = 60_000;

// If the access token has less than 70 seconds left, we refresh it.
// The check itself runs every minute.
const TOKEN_MIN_VALIDITY_SECONDS = 70;
const TOKEN_REFRESH_INTERVAL_MS = 60_000;


// Grab the DOM elements once so we don't have to keep calling getElementById.
const dom = {
  loading:    document.getElementById("loading-state"),
  app:        document.getElementById("app-container"),
  username:   document.getElementById("username"),
  logoutBtn:  document.getElementById("logout-btn"),
  refreshBtn: document.getElementById("refresh-btn"),
  dateFrom:   document.getElementById("date-from"),
  dateTo:     document.getElementById("date-to"),
  liveBadge:  document.getElementById("live-badge"),
  errorBox:   document.getElementById("error-message"),
  errorText:  document.getElementById("error-text"),
  canvas:     document.getElementById("iotChart"),
};

// Module-wide state.
let keycloak     = null;
let chart        = null;
let livePollId   = null;   // setInterval handle of the live poller
let liveSinceIso = null;   // "from" timestamp that live mode is anchored to


document.addEventListener("DOMContentLoaded", initApp);

async function initApp() {
  // keycloak.js is pulled in via <script> in index.html and exposes
  // the global Keycloak constructor.
  if (typeof Keycloak !== "function") {
    showFatalError(
      "Keycloak-Bibliothek konnte nicht geladen werden. " +
      "Bitte Netzwerk- und Reverse-Proxy-Konfiguration prüfen."
    );
    return; 
  }

  keycloak = new Keycloak(KEYCLOAK_CONFIG);

  try {
    const authenticated = await keycloak.init({
      onLoad: "login-required",   // redirect to KC login if there's no session
      checkLoginIframe: false,    // avoids third-party-cookie problems
      pkceMethod: "S256",         // PKCE for the authorization code flow
    });

    // With "login-required" this branch shouldn't really hit, but just in case.
    if (!authenticated) {
      keycloak.login();
      return;
    }
  } catch (err) {
    showFatalError(
      "Authentifizierung an Keycloak fehlgeschlagen. " +
      "Bitte später erneut versuchen."
    );
    console.error("[keycloak.init]", err);
    return;
  }

  // Client-side role check. The real authorization happens in the Zig
  // backend on every /api/v1/* request; this is only so unauthorized
  // users don't see a half-broken UI.
  if (!hasRequiredRole("dashboard-user")) {
    showFatalError(
      "Ihr Benutzerkonto besitzt nicht die erforderliche Rolle " +
      "'dashboard-user'. Bitte wenden Sie sich an Ihren Administrator."
    );
    return;
  }

  // Login worked, hide the spinner, show the app.
  // classList.add("d-none") instead of style.display = "none" because the
  // loader has Bootstrap's d-flex class, whose `display: flex !important`
  // beats an inline `display: none` set without !important.
  dom.loading.classList.add("d-none");
  dom.app.style.display = "block";

  setupUI();
  initChart();

  // Pre-fill "last hour" so the user immediately sees something.
  prefillDefaultTimeRange();

  // Token refresh keeps running in the background until logout / tab close.
  startTokenAutoRefresh();

  // Kick off the first load.
  loadData();
}


// Checks whether the given realm role is in the token.
// Claims live under realm_access.roles[].
function hasRequiredRole(role) {
  const roles = keycloak.realmAccess?.roles || [];
  return roles.includes(role);
}

function startTokenAutoRefresh() {
  setInterval(async () => {
    try {
      const refreshed = await keycloak.updateToken(TOKEN_MIN_VALIDITY_SECONDS);
      if (refreshed) {
        console.debug("[keycloak] access token refreshed");
      }
    } catch (err) {
      console.warn("[keycloak] token refresh failed, forcing re-login", err);
      keycloak.login();
    }
  }, TOKEN_REFRESH_INTERVAL_MS);
}


function setupUI() {
  // Pull the username from the ID token (preferred_username claim).
  // keycloak-js already decodes the token and exposes it as tokenParsed.
  const username =
    keycloak.tokenParsed?.preferred_username ||
    keycloak.tokenParsed?.name ||
    "Unbekannt";
  dom.username.textContent = username;

  dom.logoutBtn.addEventListener("click", () => {
    stopLiveMode();
    keycloak.logout({ redirectUri: window.location.origin });
  });

  dom.refreshBtn.addEventListener("click", loadData);

  // Pressing Enter inside either date field also triggers a reload.
  [dom.dateFrom, dom.dateTo].forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") loadData();
    });
  });
}

function prefillDefaultTimeRange() {
  const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000);
  dom.dateFrom.value = toLocalInputValue(oneHourAgo);
  // dateTo intentionally left empty → live mode is the default
}


async function loadData() {
  hideError();

  const fromValue = dom.dateFrom.value;
  const toValue   = dom.dateTo.value;

  if (!fromValue) {
    showError("Bitte geben Sie ein 'Von'-Datum an.");
    return;
  }

  const fromIso = new Date(fromValue).toISOString();
  const toIso   = toValue ? new Date(toValue).toISOString() : null;

  // An empty "to" means live mode, otherwise static range.
  if (toIso === null) {
    enterLiveMode(fromIso);
  } else {
    exitLiveMode();
  }

  await fetchAndRender(fromIso, toIso, false);
}

async function fetchAndRender(fromIso, toIso, append) {
  // Make sure the token still has enough lifetime before firing the request.
  try {
    await keycloak.updateToken(TOKEN_MIN_VALIDITY_SECONDS);
  } catch {
    keycloak.login();
    return;
  }

  const url = new URL(SENSOR_DATA_ENDPOINT);
  url.searchParams.set("from", fromIso);
  if (toIso) url.searchParams.set("to", toIso);
  // Ask the server for the grouped format if it supports it
  // (one row per timestamp with temperature + humidity together).
  url.searchParams.set("format", "grouped");

  let response;
  try {
    response = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${keycloak.token}`,
        "Accept":        "application/json",
      },
    });
  } catch (networkErr) {
    showError("Netzwerkfehler beim Abrufen der Sensordaten.");
    console.error("[fetch]", networkErr);
    return;
  }

  // 401 = token rejected by the server → force a fresh login round-trip.
  if (response.status === 401) {
    console.warn("[api] 401 Unauthorized — re-authenticating");
    keycloak.login();
    return;
  }

  // 403 = authenticated but not authorized (role / audience mismatch).
  if (response.status === 403) {
    showError(
      "Zugriff verweigert (403): Die erforderliche Rolle fehlt oder das " +
      "Token ist für diese API nicht gültig."
    );
    return;
  }

  if (!response.ok) {
    showError(`Server-Fehler ${response.status} beim Abrufen der Sensordaten.`);
    return;
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    showError("Antwort konnte nicht als JSON gelesen werden.");
    return;
  }

  const points = normalizePayload(payload);
  renderChart(points, append);
  updateStatusBadge(points.length, toIso === null);
}

// Brings both possible response formats into the same shape:
// { t, temperature, humidity }, sorted ascending by time.
//
//   grouped: [ { timestamp, temperature, humidity }, ... ]
//   raw:     [ { recorded_at, sensor_id, value, unit }, ... ]
//
// The raw form comes straight from the DB, where each timestamp has two rows
// (sensor01_temperature and sensor01_humidity). We zip them back together here.
function normalizePayload(payload) {
  // Some servers wrap the array in an envelope like { data: [...] }.
  let rows = Array.isArray(payload) ? payload : payload.data || payload.rows || [];

  if (rows.length === 0) return [];

  // Detect the format by looking at the first row.
  const sample = rows[0];

  if ("temperature" in sample || "humidity" in sample) {
    // Already grouped — just normalize the timestamp.
    return rows
      .map((r) => ({
        t:           new Date(r.timestamp || r.recorded_at).getTime(),
        temperature: numOrNull(r.temperature),
        humidity:    numOrNull(r.humidity),
      }))
      .filter((p) => Number.isFinite(p.t))
      .sort((a, b) => a.t - b.t);
  }

  // Raw format: bucket by timestamp, then assign temperature / humidity
  // based on the sensor_id suffix (or the unit as a fallback).
  const byTs = new Map();
  for (const row of rows) {
    const ts = new Date(row.recorded_at).getTime();
    if (!Number.isFinite(ts)) continue;

    const bucket = byTs.get(ts) || { t: ts, temperature: null, humidity: null };
    const id = String(row.sensor_id || "");

    if (id.endsWith("_temperature") || row.unit === "C") {
      bucket.temperature = numOrNull(row.value);
    } else if (id.endsWith("_humidity") || row.unit === "%") {
      bucket.humidity = numOrNull(row.value);
    }

    byTs.set(ts, bucket);
  }

  return [...byTs.values()].sort((a, b) => a.t - b.t);
}

function numOrNull(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}


function enterLiveMode(fromIso) {
  if (livePollId !== null) return;   // already running
  liveSinceIso = fromIso;
  dom.liveBadge.style.display = "inline-flex";

  livePollId = setInterval(() => {
    // In live mode we always query from liveSinceIso up to "now".
    // For simplicity we re-render the whole window every time;
    // for very large ranges incremental fetches would be smarter.
    fetchAndRender(liveSinceIso, null, false).catch((e) =>
      console.error("[live-poll]", e)
    );
  }, LIVE_POLL_INTERVAL_MS);
}

function exitLiveMode() {
  stopLiveMode();
  dom.liveBadge.style.display = "none";
}

function stopLiveMode() {
  if (livePollId !== null) {
    clearInterval(livePollId);
    livePollId = null;
  }
}


function initChart() {
  const ctx = dom.canvas.getContext("2d");

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Temperatur (°C)",
          data: [],
          yAxisID: "yTemp",
          borderColor:     "#0d6efd",
          backgroundColor: "rgba(13, 110, 253, 0.12)",
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.3,
          fill: true,
        },
        {
          label: "Luftfeuchte (%)",
          data: [],
          yAxisID: "yHum",
          borderColor:     "#fd7e14",
          backgroundColor: "rgba(253, 126, 20, 0.10)",
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.3,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          labels: { usePointStyle: true, boxWidth: 8, padding: 16 },
        },
        tooltip: {
          backgroundColor: "rgba(33, 37, 41, 0.95)",
          padding: 12,
          titleFont: { weight: "600" },
          callbacks: {
            title: (items) => {
              if (!items.length) return "";
              return new Date(items[0].parsed.x).toLocaleString("de-DE");
            },
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: {
            tooltipFormat: "dd.MM.yyyy HH:mm:ss",
            displayFormats: {
              minute: "HH:mm",
              hour:   "dd.MM HH:mm",
              day:    "dd.MM.yyyy",
            },
          },
          ticks: { maxRotation: 0, autoSkip: true, autoSkipPadding: 24 },
          grid:  { color: "rgba(0,0,0,0.05)" },
        },
        yTemp: {
          type: "linear",
          position: "left",
          title: { display: true, text: "Temperatur (°C)", color: "#0d6efd" },
          grid:  { color: "rgba(13, 110, 253, 0.08)" },
        },
        yHum: {
          type: "linear",
          position: "right",
          title: { display: true, text: "Luftfeuchte (%)", color: "#fd7e14" },
          grid:  { drawOnChartArea: false },
          min: 0, max: 100,
        },
      },
    },
  });
}

function renderChart(points, append) {
  if (!chart) return;

  if (!append) {
    chart.data.labels = points.map((p) => p.t);
    chart.data.datasets[0].data = points.map((p) => ({ x: p.t, y: p.temperature }));
    chart.data.datasets[1].data = points.map((p) => ({ x: p.t, y: p.humidity }));
  } else {
    for (const p of points) {
      chart.data.labels.push(p.t);
      chart.data.datasets[0].data.push({ x: p.t, y: p.temperature });
      chart.data.datasets[1].data.push({ x: p.t, y: p.humidity });
    }
  }
  chart.update();
}


// enter/exitLiveMode() own the LIVE badge visibility; here we just log
// and re-assert it for safety.
function updateStatusBadge(count, isLive) {
  if (isLive) {
    dom.liveBadge.style.display = "inline-flex";
  }
  console.info(
    `[chart] ${count} data point(s) rendered ` +
    `(${isLive ? "live mode" : "static range"})`
  );
}

function showError(message) {
  dom.errorText.textContent = message;
  dom.errorBox.classList.remove("d-none");
}

function hideError() {
  dom.errorBox.classList.add("d-none");
}

function showFatalError(message) {
  // Replaces the spinner with an error message; the app itself stays hidden.
  dom.loading.innerHTML = `
    <div class="text-center px-4">
      <i class="bi bi-shield-exclamation text-danger" style="font-size: 3rem;"></i>
      <h4 class="mt-3 text-dark">Zugriff nicht möglich</h4>
      <p class="text-muted mt-2" style="max-width: 480px;">${escapeHtml(message)}</p>
    </div>
  `;
}


// Converts a Date into the format <input type="datetime-local"> expects:
// YYYY-MM-DDTHH:mm in the browser's local timezone.
function toLocalInputValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}