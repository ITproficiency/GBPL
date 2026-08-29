const POLL_MS = 3000;
const chartPoints = [];

const els = {
  distance: document.getElementById("distance"),
  brightness: document.getElementById("brightness"),
  sittingMinutes: document.getElementById("sittingMinutes"),
  blinkRate: document.getElementById("blinkRate"),
  headPose: document.getElementById("headPose"),
  riskLevel: document.getElementById("riskLevel"),
  lastUpdate: document.getElementById("lastUpdate"),
  alertList: document.getElementById("alertList"),
  adviceContent: document.getElementById("adviceContent"),
  advicePanel: document.getElementById("advicePanel"),
  analyzeBtn: document.getElementById("analyzeBtn"),
  analyzeStatus: document.getElementById("analyzeStatus"),
  rulesInfo: document.getElementById("rulesInfo"),
  insightBtn: document.getElementById("insightBtn"),
  insightStatus: document.getElementById("insightStatus"),
  insightContent: document.getElementById("insightContent"),
  headerRiskPill: document.getElementById("headerRiskPill"),
};

const ctx = document.getElementById("sensorChart").getContext("2d");
const chart = new Chart(ctx, {
  type: "line",
  data: {
    labels: [],
    datasets: [
      { label: "Distance (cm)", data: [], borderColor: "#3b82f6", tension: 0.3, yAxisID: "y" },
      { label: "Brightness (lux)", data: [], borderColor: "#f59e0b", tension: 0.3, yAxisID: "y1" },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: "#8b9cb3" } } },
    scales: {
      x: { ticks: { color: "#8b9cb3", maxTicksLimit: 8 }, grid: { color: "#2d3a4f" } },
      y: { position: "left", ticks: { color: "#8b9cb3" }, grid: { color: "#2d3a4f" } },
      y1: { position: "right", ticks: { color: "#8b9cb3" }, grid: { drawOnChartArea: false } },
    },
  },
});

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString();
}

function formatHeadPose(pitch, roll, yaw) {
  if (pitch == null && roll == null && yaw == null) return "--";
  const fmt = (v) => (v == null ? "-" : Math.round(v) + "°");
  return `P ${fmt(pitch)} · R ${fmt(roll)} · Y ${fmt(yaw)}`;
}

function renderMarkdownBlock(text) {
  if (!text) return "";
  try {
    return marked.parse(text);
  } catch {
    return `<p>${escapeHtml(text)}</p>`;
  }
}

function renderMarkdownInline(text) {
  if (!text) return "";
  try {
    return marked.parseInline(text);
  } catch {
    return escapeHtml(text);
  }
}

function renderAdvice(advice) {
  if (!advice?.summary) return;
  els.advicePanel.classList.add("has-advice");
  const recs = (advice.recommendations || []).map((r) => `<li>${renderMarkdownInline(r)}</li>`).join("");
  els.adviceContent.innerHTML = `
    <div class="advice-summary">${renderMarkdownBlock(advice.summary)}</div>
    <ul class="advice-recs">${recs}</ul>
    <p class="advice-meta">${advice.model_name || "LLM"} · ${formatTime(advice.created_at || new Date().toISOString())}</p>`;
}

function renderInsight(item) {
  if (!item?.summary) return;
  const s = item.stats || {};
  const pct = s.risk_level_pct || {};
  els.insightContent.innerHTML = `
    <div class="advice-summary">${renderMarkdownBlock(item.summary)}</div>
    <div class="insight-stats">
      <span>Readings: ${s.reading_count ?? "-"}</span>
      <span>Window: ${s.window_minutes ?? "-"} min</span>
      <span>Risk: normal ${pct.normal ?? 0}% · warning ${pct.warning ?? 0}% · high ${pct.high ?? 0}%</span>
    </div>
    <p class="advice-meta">${item.model_name || "LLM"} · ${formatTime(item.created_at || new Date().toISOString())}</p>`;
}

/** Display only — all risk logic comes from backend processing.py */
function renderSensor(data) {
  els.distance.textContent = Math.round(data.distance_cm);
  els.brightness.textContent = Math.round(data.brightness_lux);
  els.sittingMinutes.textContent = data.sitting_minutes ?? "--";
  els.blinkRate.textContent = data.blink_rate_bpm != null ? data.blink_rate_bpm.toFixed(1) : "--";
  els.headPose.textContent = formatHeadPose(data.head_pitch_deg, data.head_roll_deg, data.head_yaw_deg);
  els.riskLevel.textContent = data.risk_level.toUpperCase();
  els.riskLevel.className = "value risk-" + data.risk_level;
  els.lastUpdate.textContent = "Updated " + formatTime(data.timestamp);
  els.headerRiskPill.textContent = data.risk_level.toUpperCase();
  els.headerRiskPill.className = "risk-pill risk-" + data.risk_level;

  els.alertList.innerHTML = data.risk_level === "normal"
    ? '<li class="empty">All readings within PostureCare targets</li>'
    : `<li class="alert-item ${data.risk_level}">
        <strong>${data.risk_level.toUpperCase()}</strong>
        <div class="msg">${(data.warning_messages || []).join("; ")}</div>
        ${data.sitting_minutes ? `<div class="msg">Sitting: ${data.sitting_minutes} min</div>` : ""}
      </li>`;

  // Use backend flag — same rule as POST /api/analyze
  const eligible =
    data.llm_eligible ?? ["warning", "high"].includes(data.risk_level);
  els.analyzeBtn.disabled = !eligible;

  // focusTimer/notifyRiskHigh live in focus.js (loaded before this file) —
  // guarded in case focus.js ever fails to load, so posture polling still works.
  if (typeof focusTimer !== "undefined" && focusTimer.mode === "focus" && data.risk_level === "high") {
    notifyRiskHigh(data);
  }

  chartPoints.push(data);
  if (chartPoints.length > 30) chartPoints.shift();
  chart.data.labels = chartPoints.map((r) => formatTime(r.timestamp));
  chart.data.datasets[0].data = chartPoints.map((r) => r.distance_cm);
  chart.data.datasets[1].data = chartPoints.map((r) => r.brightness_lux);
  chart.update("none");
}

async function fetchRules() {
  const res = await fetch("/api/rules");
  if (!res.ok) return;
  const rules = await res.json();
  if (rules.insight_window_minutes && typeof applyBackendDefaultFocusMinutes === "function") {
    applyBackendDefaultFocusMinutes(rules.insight_window_minutes);
  }
  if (typeof applyCooldownSettings === "function") {
    applyCooldownSettings(rules.analyze_cooldown_sec, rules.insight_cooldown_sec);
  }

  if (!els.rulesInfo) return;
  const d = rules.distance_cm;
  const b = rules.brightness_lux;
  const s = rules.sitting_minutes;
  els.rulesInfo.innerHTML =
    `PostureCare rules: distance ${d.target_min}-${d.target_max} cm · ` +
    `light ${b.target_min}+ lux · sitting max ${s.max_continuous} min`;
}

async function fetchSensor() {
  const res = await fetch("/api/sensor");
  if (!res.ok) {
    els.lastUpdate.textContent = "Backend not running";
    return;
  }
  const data = await res.json();
  if (data) renderSensor(data);
}

async function fetchAdvice() {
  const res = await fetch("/api/advice?limit=1");
  if (!res.ok) return;
  const { items } = await res.json();
  if (items[0]) renderAdvice(items[0]);
}

els.analyzeBtn.addEventListener("click", async () => {
  els.analyzeBtn.disabled = true;
  els.analyzeStatus.textContent = "Analyzing...";
  try {
    const res = await fetch("/api/analyze", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      els.analyzeStatus.textContent = data.detail || "Analyze failed";
    } else if (data.status === "skipped") {
      els.analyzeStatus.textContent = data.message;
      if (data.reading) renderSensor(data.reading);
    } else {
      renderAdvice(data.advice);
      els.analyzeStatus.textContent = "Advice saved";
      if (data.reading) renderSensor(data.reading);
      fetchAdvice();
      if (typeof speak === "function") speak(truncateForSpeech(data.advice.summary));
    }
  } catch {
    els.analyzeStatus.textContent = "Connection error";
  }
  fetchSensor();
});

async function fetchInsight() {
  const res = await fetch("/api/insights?limit=1");
  if (!res.ok) return;
  const { items } = await res.json();
  if (items[0]) renderInsight(items[0]);
}

els.insightBtn.addEventListener("click", async () => {
  els.insightBtn.disabled = true;
  els.insightStatus.textContent = "Generating...";
  try {
    const windowMinutes = typeof settings !== "undefined" ? settings.focusMinutes : null;
    const url = windowMinutes ? `/api/insights?window_minutes=${windowMinutes}` : "/api/insights";
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      els.insightStatus.textContent = data.detail || "Insight failed";
    } else {
      els.insightStatus.textContent = "Insight generated";
      fetchInsight();
      if (typeof speak === "function") speak(truncateForSpeech(data.advice.summary));
    }
  } catch {
    els.insightStatus.textContent = "Connection error";
  }
  els.insightBtn.disabled = false;
});

/* ---------------- Tabs (Focus = simple work view, Details = full monitoring) ---------------- */

function activateTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabName));
  document.querySelectorAll(".tab-view").forEach((v) => v.classList.toggle("active", v.id === tabName + "View"));
  try {
    localStorage.setItem("pc_active_tab", tabName);
  } catch {
    // Private browsing / storage disabled — tab choice just won't persist.
  }
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

(function restoreLastTab() {
  let savedTab = null;
  try {
    savedTab = localStorage.getItem("pc_active_tab");
  } catch {
    // Ignore — default tab (Focus) stays active.
  }
  if (savedTab === "focus" || savedTab === "details") activateTab(savedTab);
})();

async function refresh() {
  await Promise.allSettled([fetchSensor(), fetchAdvice(), fetchInsight()]);
}

fetchRules();
refresh();
setInterval(refresh, POLL_MS);
