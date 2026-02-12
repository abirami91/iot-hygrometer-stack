// ---------- small utils ----------
const fmt = (x) => (x == null ? "—" : x);
const numOrNull = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};
function nowISOclock() {
  const d = new Date();
  return d.toLocaleString();
}
function setBadge(text, color = "bg-slate-200 text-slate-700") {
  const el = document.getElementById("badgeStatus");
  if (!el) return;
  el.className = `px-2 py-1 rounded-full text-xs ${color}`;
  el.textContent = text;
}
function updateCard(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.transition = "opacity .25s";
  el.style.opacity = 0;
  setTimeout(() => { el.textContent = text; el.style.opacity = 1; }, 150);
}
function peakIndex(arr) {
  let idx = -1, best = -Infinity;
  for (let i = 0; i < arr.length; i++) {
    const v = Number(arr[i]);
    if (Number.isFinite(v) && v > best) { best = v; idx = i; }
  }
  return idx;
}
function setPeakBadges(tLabel, tVal, hLabel, hVal) {
  const tBox = document.getElementById("statTemp");
  const hBox = document.getElementById("statHum");
  if (tBox) tBox.querySelector("div:nth-child(2)").textContent =
    tLabel && Number.isFinite(tVal) ? `${tLabel} • ${tVal.toFixed(2)} °C` : "—";
  if (hBox) hBox.querySelector("div:nth-child(2)").textContent =
    hLabel && Number.isFinite(hVal) ? `${hLabel} • ${hVal.toFixed(0)} %` : "—";
}

// ---------- setup UI (screen switching) ----------
function showSetupScreen() {
  const s = document.getElementById("setup-screen");
  const d = document.getElementById("dashboard-screen");
  if (s) s.style.display = "block";
  if (d) d.style.display = "none";
}

let selectedDeviceMac = null;


function showDashboardScreen() {
  const s = document.getElementById("setup-screen");
  const d = document.getElementById("dashboard-screen");
  if (s) s.style.display = "none";
  if (d) d.style.display = "block";
}


function renderDeviceList(devices) {
  const box = document.getElementById("deviceList");
  if (!box) return;

  if (!devices || devices.length === 0) {
    box.innerHTML = `<div class="text-slate-600">No BLE devices found. Move the sensor closer and try again.</div>`;
    return;
  }

  const rows = devices.map(d => {
    const mac = d.mac || "";
    const name = d.name || "(unknown)";
    const isSelected = selectedDeviceMac && mac === selectedDeviceMac;

    return `
      <div class="flex items-center justify-between gap-2 py-2 border-b border-slate-100 last:border-b-0">
        <div class="min-w-0">
          <div class="font-mono text-xs text-slate-700">${mac}</div>
          <div class="text-sm text-slate-900 truncate">${name}</div>
          <div class="text-xs text-slate-500">RSSI: ${typeof d.rssi === "number" ? d.rssi : "—"}</div>
        </div>
        <button class="btnSelectDevice px-3 py-2 rounded-lg border
                       ${isSelected ? "bg-emerald-50 border-emerald-300 text-emerald-900" : "border-slate-300"}"
                data-mac="${mac}"
                data-name="${d.name || ""}"
                ${isSelected ? "disabled" : ""}>
          ${isSelected ? "Selected" : "Select"}
        </button>
      </div>
    `;
  }).join("");

  box.innerHTML = rows;

  box.querySelectorAll(".btnSelectDevice").forEach(btn => {
    btn.addEventListener("click", async () => {
      const mac = btn.getAttribute("data-mac");
      const name = btn.getAttribute("data-name") || null;
      await selectDevice(mac, name);
    });
  });
}


async function scanDevices() {
  const status = document.getElementById("scanStatus");
  if (status) status.textContent = "Scanning… (10s)";

  try {
    const r = await fetch("/api/setup/devices", { cache: "no-store" });
    if (!r.ok) throw new Error("Scan API not ready");
    const data = await r.json(); // { devices: [...] }
    renderDeviceList(data.devices || []);
    if (status) status.textContent = "Scan complete.";
  } catch {
    renderDeviceList([]);
    if (status) status.textContent = "Scan API not implemented yet (next step).";
  }
}

async function selectDevice(mac, name) {
  const status = document.getElementById("scanStatus");
  if (status) status.textContent = `Saving ${mac}…`;

  try {
    const r = await fetch("/api/setup/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mac, name })
    });
    if (!r.ok) throw new Error(await r.text());

    if (status) status.textContent = "Saved. Reloading…";
    location.reload();
  } catch (e) {
    console.error(e);
    if (status) status.textContent = "Select API not implemented yet (next step).";
  }
}


// expected backend response: { configured: true/false, device?: {...} }
async function getSetupStatus() {
  try {
    const r = await fetch("/api/setup/status", { cache: "no-store" });
    if (!r.ok) return { configured: true }; // keep current behavior if API isn't ready
    return await r.json();
  } catch {
    return { configured: true }; // if endpoint doesn't exist yet, don't block dashboard
  }
}

// register annotation plugin if loaded via <script>
if (window.ChartAnnotation) {
  Chart.register(window.ChartAnnotation);
}

// ---------- latest cards ----------
async function getLatest() {
  const res = await fetch("/api/latest");
  const data = await res.json();
  const r = data.reading;
  if (!r) {
    updateCard("cardTemp", "—");
    updateCard("cardHum", "—");
    updateCard("cardBatt", "—");
    return;
  }
  updateCard("cardTemp", `${r.temp_c?.toFixed?.(2) ?? "—"}°C`);
  updateCard("cardHum",  `${r.humidity_pct?.toFixed?.(2) ?? "—"}%`);
  updateCard("cardBatt", `${r.battery_mv ?? "—"}`);
}

// ---------- insights badge ----------
async function refreshInsightsBadge() {
  try {
    const res = await fetch("/api/insights/latest", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const status = (data.status || "idle").toLowerCase();

    // optional: show hours in the badge text
    const hWarn = data?.last_24h?.hours_humidity_above_warn;
    const hAlert = data?.last_24h?.hours_humidity_above_alert;

    console.log("insights status =", status);

    if (status === "ok") {
      setBadge("OK", "bg-emerald-100 text-emerald-800");
    } else if (status === "warn") {
      const label = (typeof hWarn === "number") ? `WARN (${hWarn}h)` : "WARN";
      setBadge(label, "bg-amber-100 text-amber-800");
    } else if (status === "alert") {
      const label = (typeof hAlert === "number") ? `ALERT (${hAlert}h)` : "ALERT";
      setBadge(label, "bg-rose-100 text-rose-800");
    } else {
      setBadge("Idle", "bg-slate-200 text-slate-700");
    }
  } catch (e) {
    // If insights aren't ready yet (file missing etc.), don't treat as error.
    setBadge("No insights", "bg-slate-200 text-slate-700");
  }
}

// ---------- upload / export ----------
async function uploadCSV(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/upload", { method: "POST", body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function setupUpload() {
  const btn = document.getElementById("uploadBtn");
  const btnExport = document.getElementById("exportBtn");
  const fi  = document.getElementById("fileInput");
  const msg = document.getElementById("uploadMsg");

  if (btn) btn.addEventListener("click", async () => {
    msg.textContent = "";
    if (!fi.files.length) { msg.textContent = "Choose a CSV first."; return; }
    btn.disabled = true;
    btn.textContent = "Uploading…";
    setBadge("Uploading", "bg-blue-100 text-blue-800");

    try {
      const json = await uploadCSV(fi.files[0]);
      msg.textContent = `Uploaded. Inserted ${json.inserted} rows.`;
      await getLatest();
      await loadDay();
      await refreshInsightsBadge();

      setBadge("Updated", "bg-emerald-100 text-emerald-800");
    } catch (e) {
      console.error(e);
      msg.textContent = "Upload failed.";
      setBadge("Error", "bg-rose-100 text-rose-800");
    } finally {
      btn.disabled = false;
      btn.textContent = "Upload";

      // ✅ restore the insights badge instead of forcing Idle
      setTimeout(() => refreshInsightsBadge(), 1500);
    }
  });

  if (btnExport) btnExport.addEventListener("click", async () => {
    const d = document.getElementById("dateInput").value;
    const res = await fetch(`/api/day?date_str=${encodeURIComponent(d)}`);
    if (!res.ok) return alert("Failed to fetch rows.");
    const { rows } = await res.json();
    if (!rows?.length) return alert("No data to export for this day.");
    const header = "timestamp_iso,epoch,temp_c,humidity_pct,battery_mv";
    const lines = rows.map(r => [r.ts_utc, r.epoch, r.temp_c, r.humidity_pct, r.battery_mv].join(","));
    const csv = [header, ...lines].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${d}_readings.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });
}

// ---------- charts ----------
let chartMain, chartBatt, chartBar;

function chartColors() {
  return {
    temp: "#ef4444", // red-500
    hum:  "#3b82f6", // blue-500
    batt: "#a78bfa"  // violet-400
  };
}

function makeMainChart(ctx) {
  const C = chartColors();
  return new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [
      {
        label: "Temperature (°C)",
        data: [],
        borderColor: C.temp,
        backgroundColor: "rgba(239,68,68,0.15)",
        yAxisID: "yTemp",
        tension: 0.3,
        pointRadius: 2,
        borderWidth: 2,
        fill: true
      },
      {
        label: "Humidity (%)",
        data: [],
        borderColor: C.hum,
        backgroundColor: "rgba(59,130,246,0.15)",
        yAxisID: "yHum",
        tension: 0.3,
        pointRadius: 2,
        borderWidth: 2,
        fill: true
      }
    ]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      parsing: false,
      scales: {
        x: {
          type: "category",
          ticks: { autoSkip: true, maxRotation: 45, color: "#4b5563" },
          grid: { color: "rgba(203,213,225,0.3)" }
        },
        yTemp: {
          type: "linear",
          position: "left",
          grid: { color: "rgba(203,213,225,0.3)" },
          title: { display: true, text: "Temperature (°C)", color: "#ef4444" }
        },
        yHum: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          title: { display: true, text: "Humidity (%)", color: "#3b82f6" }
        }
      },
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            title(items) {
              const i = items[0]?.dataIndex;
              return rowsGlobal?.[i]?.ts_utc
                ?.replace("T", " ")
                ?.replace("Z", "") || "";
            },
            
            label(ctx) { return `${ctx.dataset.label}: ${ctx.formattedValue}`; }
          }
        },
        zoom: {
          pan: { enabled: true, mode: "x" },
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" }
        },
        annotation: { annotations: {} }
      }
    }
  });
}

function makeBattChart(ctx) {
  const C = chartColors();
  return new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [
      { label: "Battery (mV)", data: [], borderColor: C.batt, backgroundColor: "transparent", tension: 0.25, pointRadius: 1.5 }
    ]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      parsing: false,
      scales: { x: { type: "category" }, y: { type: "linear" } }
    }
  });
}

function makeBarChart(ctx) {
  const C = chartColors();
  return new Chart(ctx, {
    type: "bar",
    data: { labels: [], datasets: [
      {
        label: "Avg Temp (°C)",
        data: [],
        yAxisID: "yTemp",
        backgroundColor: "rgba(239,68,68,0.35)",
        borderColor: C.temp,
        borderWidth: 1,
        borderSkipped: false
      },
      {
        label: "Avg Humidity (%)",
        data: [],
        yAxisID: "yHum",
        backgroundColor: "rgba(59,130,246,0.35)",
        borderColor: C.hum,
        borderWidth: 1,
        borderSkipped: false
      }
    ]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { type: "category", ticks: { color: "#4b5563" }, grid: { display:false } },
        yTemp: { type: "linear", position: "left", beginAtZero: false, grid: { color:"rgba(203,213,225,0.3)" } },
        yHum:  { type: "linear", position: "right", grid: { drawOnChartArea:false }, suggestedMin: 0, suggestedMax: 100 }
      },
      plugins: { legend: { position: "top" } }
    }
  });
}

function ensureCharts() {
  const mainCtx = document.getElementById("chartMain")?.getContext("2d");
  const battCtx = document.getElementById("chartBatt")?.getContext("2d");
  const barCtx  = document.getElementById("chartBar")?.getContext("2d");
  if (!chartMain && mainCtx) chartMain = makeMainChart(mainCtx);
  if (!chartBatt && battCtx) chartBatt = makeBattChart(battCtx);
  if (!chartBar  && barCtx)  chartBar  = makeBarChart(barCtx);
}

function setYAxisRange(scale, data) {
  const nums = data.filter((v) => Number.isFinite(v));
  if (!nums.length) { scale.min = undefined; scale.max = undefined; return; }
  const min = Math.min(...nums), max = Math.max(...nums);
  const pad = (max - min || 1) * 0.1;
  scale.min = min - pad;
  scale.max = max + pad;
}

function hourlyAverages(labels, temps, hums) {
  const bucket = new Map();
  for (let i = 0; i < labels.length; i++) {
    const label = labels[i];
    const hourKey = label.slice(0, 13) + ":00";
    const t = Number(temps[i]), h = Number(hums[i]);
    if (!bucket.has(hourKey)) bucket.set(hourKey, { tSum:0, tN:0, hSum:0, hN:0 });
    const b = bucket.get(hourKey);
    if (Number.isFinite(t)) { b.tSum += t; b.tN++; }
    if (Number.isFinite(h)) { b.hSum += h; b.hN++; }
  }
  const hours = Array.from(bucket.keys()).sort();
  const tAvg = hours.map(k => {
    const b = bucket.get(k); return b.tN ? +(b.tSum / b.tN).toFixed(2) : null;
  });
  const hAvg = hours.map(k => {
    const b = bucket.get(k); return b.hN ? +(b.hSum / b.hN).toFixed(2) : null;
  });
  return { hours, tAvg, hAvg };
}

// ---------- load day ----------
async function loadDay() {
  ensureCharts();
  const dateInput = document.getElementById("dateInput");
  const msg = document.getElementById("loadMsg");
  const d = dateInput.value || new Date().toISOString().slice(0,10);

  msg.textContent = "Loading…";
  const res = await fetch(`/api/day?date_str=${encodeURIComponent(d)}`);
  if (!res.ok) { msg.textContent = "Failed to load."; return; }
  const { rows=[] } = await res.json();

  if (!rows.length) {
    chartMain.data.labels = [];
    chartMain.data.datasets.forEach(ds => ds.data = []);
    chartMain.update();
    chartBatt.data.labels = [];
    chartBatt.data.datasets[0].data = [];
    chartBatt.update();
    chartBar.data.labels = [];
    chartBar.data.datasets.forEach(ds => ds.data = []);
    chartBar.update();
    setPeakBadges(null, NaN, null, NaN);
    msg.textContent = `No points for ${d}.`;
    return;
  }

  const labels = rows.map(r => {
    const d = new Date(r.ts_utc);
  
    // format as HH:MM (recommended for charts)
    return d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit"
    });
  });
  
  const temps = rows.map(r => numOrNull(r.temp_c));
  const hums  = rows.map(r => numOrNull(r.humidity_pct));
  const batts = rows.map(r => numOrNull(r.battery_mv));

  chartMain.data.labels = labels;
  chartMain.data.datasets[0].data = temps;
  chartMain.data.datasets[1].data = hums;
  setYAxisRange(chartMain.options.scales.yTemp, temps);
  setYAxisRange(chartMain.options.scales.yHum,  hums);

  const iT = peakIndex(temps);
  const iH = peakIndex(hums);
  const tPeakVal = iT >= 0 ? temps[iT] : null;
  const hPeakVal = iH >= 0 ? hums[iH]  : null;
  const tPeakLabel = iT >= 0 ? labels[iT] : null;
  const hPeakLabel = iH >= 0 ? labels[iH] : null;

  setPeakBadges(tPeakLabel, tPeakVal ?? NaN, hPeakLabel, hPeakVal ?? NaN);

  const anns = {};
  if (iT >= 0) anns.tPeak = {
    type: "line", xMin: labels[iT], xMax: labels[iT],
    borderColor: chartColors().temp, borderWidth: 2,
    label: {
      enabled: true, backgroundColor: "rgba(239,68,68,0.15)", color: "#991b1b",
      content: [`Temp peak`, `${tPeakVal.toFixed(2)} °C`], position: "start", yAdjust: -8
    }
  };
  if (iH >= 0) anns.hPeak = {
    type: "line", xMin: labels[iH], xMax: labels[iH],
    borderColor: chartColors().hum, borderWidth: 2,
    label: {
      enabled: true, backgroundColor: "rgba(59,130,246,0.15)", color: "#1e3a8a",
      content: [`Humidity peak`, `${hPeakVal.toFixed(0)} %`], position: "end", yAdjust: -8
    }
  };
  chartMain.options.plugins.annotation.annotations = anns;
  chartMain.update();

  chartBatt.data.labels = labels;
  chartBatt.data.datasets[0].data = batts;
  setYAxisRange(chartBatt.options.scales.y, batts);
  chartBatt.update();

  // hourly averages bar chart
  const agg = hourlyAverages(labels, temps, hums);
  chartBar.data.labels = agg.hours;
  chartBar.data.datasets[0].data = agg.tAvg;
  chartBar.data.datasets[1].data = agg.hAvg;
  setYAxisRange(chartBar.options.scales.yTemp, agg.tAvg);
  setYAxisRange(chartBar.options.scales.yHum,  agg.hAvg);
  chartBar.update();

  msg.textContent = `Loaded ${rows.length} points for ${d}.`;
  const ts = document.getElementById("lastUpdated");
  if (ts) ts.textContent = nowISOclock();
}

async function loadReports() {
  const statusEl = document.getElementById("reportsStatus");
  const listEl = document.getElementById("reportsList");
  if (!statusEl || !listEl) return;

  statusEl.textContent = "Loading…";
  listEl.innerHTML = "";

  try {
    const res = await fetch("/api/reports", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const reports = Array.isArray(data.reports) ? data.reports : [];
    if (reports.length === 0) {
      statusEl.textContent = "No reports yet.";
      return;
    }

    statusEl.textContent = `${reports.length} file(s)`;

    for (const relPath of reports) {
      const li = document.createElement("li");

      const a = document.createElement("a");
      a.href = `/api/reports/download?path=${encodeURIComponent(relPath)}`;
      a.textContent = relPath.split("/").pop();

      // force download instead of opening in browser
      a.download = relPath.split("/").pop();

      a.style.textDecoration = "underline";


      const meta = document.createElement("span");
      meta.className = "ml-2 text-xs text-slate-500";
      meta.textContent = `(${relPath})`;

      li.appendChild(a);
      li.appendChild(meta);
      listEl.appendChild(li);
    }
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Failed to load reports.";
  }
}






// ---------- init ----------
function initDate() {
  const d = new Date();
  const iso = d.toISOString().slice(0,10);
  const input = document.getElementById("dateInput");
  if (input) input.value = iso;
}

document.addEventListener("DOMContentLoaded", async () => {
  initDate();

  // Wire setup button (safe even if setup screen hidden)
  const scanBtn = document.getElementById("btnScanDevices");
  if (scanBtn) scanBtn.addEventListener("click", scanDevices);

  // Decide which screen to show
  const st = await getSetupStatus();
  // store selected (even when not configured yet)
  selectedDeviceMac = st?.device?.mac || null;
  if (!st.configured) {
    showSetupScreen();
    return; // stop here: no dashboard polling until configured
  }
  showDashboardScreen();

  // existing logic (unchanged)
  setupUpload();
  const loadBtn = document.getElementById("loadBtn");
  if (loadBtn) loadBtn.addEventListener("click", loadDay);
  const resetBtn = document.getElementById("resetZoomBtn");
  if (resetBtn) resetBtn.addEventListener("click", () => {
    if (chartMain && chartMain.resetZoom) chartMain.resetZoom();
  });

  const btn = document.getElementById("btnRefreshReports");
  if (btn) btn.addEventListener("click", loadReports);

  loadReports();

  await getLatest();
  await loadDay();
  await refreshInsightsBadge();

  setInterval(async () => {
    await getLatest();
    await loadDay();
    await refreshInsightsBadge();
  }, 60_000);
});

