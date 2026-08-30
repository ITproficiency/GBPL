const POLL_MS = 3000;
const chartPoints = [];

const els = {
  distance: document.getElementById("distance"),
  ultrasonicDistance: document.getElementById("ultrasonicDistance"),
  earValue: document.getElementById("earValue"),
  earThreshold: document.getElementById("earThreshold"),
  blinks: document.getElementById("blinks"),
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

// 1. Ocular & Blink Telemetry Chart (EAR & Blink Count)
const ctxOcular = document.getElementById("ocularChart").getContext("2d");
const ocularChart = new Chart(ctxOcular, {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Eye Aspect Ratio (EAR)",
        data: [],
        borderColor: "#a78bfa",
        backgroundColor: "rgba(167, 139, 250, 0.15)",
        fill: true,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        yAxisID: "y",
      },
      {
        label: "Blink Rate / Counter",
        data: [],
        borderColor: "#34d399",
        backgroundColor: "transparent",
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        yAxisID: "y1",
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: "#8b9cb3" } } },
    scales: {
      x: { ticks: { color: "#8b9cb3", maxTicksLimit: 8 }, grid: { color: "#2d3a4f" } },
      y: { position: "left", ticks: { color: "#a78bfa" }, grid: { color: "#2d3a4f" }, title: { display: true, text: "EAR", color: "#a78bfa" } },
      y1: { position: "right", ticks: { color: "#34d399" }, grid: { drawOnChartArea: false }, title: { display: true, text: "blinks", color: "#34d399" } },
    },
  },
});

// 2. Distance Telemetry Chart (AI Camera vs Ultrasonic Sensor)
const ctxDistance = document.getElementById("distanceChart").getContext("2d");
const distanceChart = new Chart(ctxDistance, {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Camera Distance AI (cm)",
        data: [],
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.15)",
        fill: true,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        yAxisID: "y",
      },
      {
        label: "Ultrasonic Sensor (cm)",
        data: [],
        borderColor: "#f59e0b",
        backgroundColor: "transparent",
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        yAxisID: "y1",
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: "#8b9cb3" } } },
    scales: {
      x: { ticks: { color: "#8b9cb3", maxTicksLimit: 8 }, grid: { color: "#2d3a4f" } },
      y: { position: "left", ticks: { color: "#38bdf8" }, grid: { color: "#2d3a4f" }, title: { display: true, text: "AI (cm)", color: "#38bdf8" } },
      y1: { position: "right", ticks: { color: "#f59e0b" }, grid: { drawOnChartArea: false }, title: { display: true, text: "Sensor (cm)", color: "#f59e0b" } },
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
  if (els.distance) els.distance.textContent = data.distance_cm != null ? Math.round(data.distance_cm) : "--";
  if (els.ultrasonicDistance) els.ultrasonicDistance.textContent = data.ultrasonic_distance_cm != null ? Math.round(data.ultrasonic_distance_cm) : "--";
  if (els.earValue) els.earValue.textContent = data.ear != null ? Number(data.ear).toFixed(3) : "--";
  if (els.earThreshold) els.earThreshold.textContent = data.ear_threshold != null ? Number(data.ear_threshold).toFixed(3) : "0.294";
  if (els.blinks) els.blinks.textContent = data.blinks != null ? data.blinks : (data.blink_rate_bpm != null ? Math.round(data.blink_rate_bpm) : "--");
  if (els.brightness) els.brightness.textContent = data.brightness_lux != null ? Math.round(data.brightness_lux) : "--";
  if (els.sittingMinutes) els.sittingMinutes.textContent = data.sitting_minutes ?? "--";
  if (els.blinkRate) els.blinkRate.textContent = data.blink_rate_bpm != null ? data.blink_rate_bpm.toFixed(1) : "--";
  if (els.headPose) els.headPose.textContent = formatHeadPose(data.head_pitch_deg, data.head_roll_deg, data.head_yaw_deg);
  
  if (els.riskLevel) {
    els.riskLevel.textContent = data.risk_level.toUpperCase();
    els.riskLevel.className = "value risk-" + data.risk_level;
  }
  if (els.lastUpdate) els.lastUpdate.textContent = "Updated " + formatTime(data.timestamp);
  if (els.headerRiskPill) {
    els.headerRiskPill.textContent = data.risk_level.toUpperCase();
    els.headerRiskPill.className = "risk-pill risk-" + data.risk_level;
  }

  const msgs = (data.warning_messages || []).filter(m => m && !m.includes("PostureCare targets"));
  if (msgs.length === 0) {
    els.alertList.innerHTML = '<li class="empty">All readings within PostureCare targets</li>';
  } else {
    els.alertList.innerHTML = msgs.map(m => `
      <li class="alert-item ${data.risk_level}">
        <strong>${data.risk_level.toUpperCase()}</strong>
        <div class="msg">${m}</div>
      </li>
    `).join("");
  }

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
  const timeLabels = chartPoints.map((r) => formatTime(r.timestamp));

  // 1. Update Ocular & Blink Chart (EAR & Blinks)
  ocularChart.data.labels = timeLabels;
  ocularChart.data.datasets[0].data = chartPoints.map((r) => r.ear);
  ocularChart.data.datasets[1].data = chartPoints.map((r) => r.blinks ?? r.blink_rate_bpm);
  ocularChart.update("none");

  // 2. Update Distance Chart (Camera AI & Ultrasonic Sensor)
  distanceChart.data.labels = timeLabels;
  distanceChart.data.datasets[0].data = chartPoints.map((r) => r.distance_cm);
  distanceChart.data.datasets[1].data = chartPoints.map((r) => r.ultrasonic_distance_cm ?? r.distance_cm);
  distanceChart.update("none");
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

/* ---------------- Live Camera Stream Controls ---------------- */
const cameraStreamImg = document.getElementById("cameraStreamImg");
const browserWebcamVideo = document.getElementById("browserWebcamVideo");
const camFallbackOverlay = document.getElementById("camFallbackOverlay");
const streamUrlInput = document.getElementById("streamUrlInput");
const connectCamBtn = document.getElementById("connectCamBtn");
const toggleWebcamBtn = document.getElementById("toggleWebcamBtn");
const cameraStatus = document.getElementById("cameraStatus");
let isBrowserWebcamActive = false;
let webcamStream = null;

if (cameraStreamImg) {
  cameraStreamImg.onerror = () => {
    if (!isBrowserWebcamActive && camFallbackOverlay) {
      camFallbackOverlay.style.display = "flex";
      if (cameraStatus) {
        cameraStatus.textContent = "OFFLINE";
        cameraStatus.style.color = "#f87171";
      }
    }
  };
  cameraStreamImg.onload = () => {
    if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
    if (cameraStatus) {
      cameraStatus.textContent = "ESP32 STREAM";
      cameraStatus.style.color = "#60a5fa";
    }
  };
}

if (connectCamBtn) {
  connectCamBtn.addEventListener("click", () => {
    if (isBrowserWebcamActive) stopBrowserWebcam();
    const url = streamUrlInput ? streamUrlInput.value.trim() : "";
    if (url && cameraStreamImg) {
      cameraStreamImg.style.display = "block";
      cameraStreamImg.src = url;
      if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
      if (cameraStatus) {
        cameraStatus.textContent = "CONNECTING...";
        cameraStatus.style.color = "#f59e0b";
      }
    }
  });
}

function stopBrowserWebcam() {
  if (webcamStream) {
    webcamStream.getTracks().forEach((t) => t.stop());
    webcamStream = null;
  }
  if (browserWebcamVideo) browserWebcamVideo.style.display = "none";
  isBrowserWebcamActive = false;
}

if (toggleWebcamBtn) {
  toggleWebcamBtn.addEventListener("click", async () => {
    if (isBrowserWebcamActive) {
      stopBrowserWebcam();
      if (cameraStreamImg) cameraStreamImg.style.display = "block";
      toggleWebcamBtn.textContent = "Use Browser Webcam";
      if (cameraStatus) {
        cameraStatus.textContent = "ESP32 STREAM";
        cameraStatus.style.color = "#60a5fa";
      }
    } else {
      try {
        webcamStream = await navigator.mediaDevices.getUserMedia({ video: true });
        if (browserWebcamVideo) {
          browserWebcamVideo.srcObject = webcamStream;
          browserWebcamVideo.style.display = "block";
        }
        if (cameraStreamImg) cameraStreamImg.style.display = "none";
        if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
        isBrowserWebcamActive = true;
        toggleWebcamBtn.textContent = "Switch to ESP32 Stream";
        if (cameraStatus) {
          cameraStatus.textContent = "BROWSER WEBCAM";
          cameraStatus.style.color = "#34d399";
        }
      } catch (err) {
        alert("Could not access browser webcam: " + err.message);
      }
    }
  });
}

const stopCamBtn = document.getElementById("stopCamBtn");
if (stopCamBtn) {
  stopCamBtn.addEventListener("click", () => {
    stopBrowserWebcam();
    if (cameraStreamImg) {
      cameraStreamImg.src = "";
      cameraStreamImg.style.display = "none";
    }
    if (camFallbackOverlay) camFallbackOverlay.style.display = "flex";
    if (cameraStatus) {
      cameraStatus.textContent = "OFFLINE";
      cameraStatus.style.color = "#f87171";
    }
    if (toggleWebcamBtn) toggleWebcamBtn.textContent = "Use Browser Webcam";
  });
}

/* ---------------- Pro Camera Overlay Actions ---------------- */
const camMirrorBtn = document.getElementById("camMirrorBtn");
const camSnapshotBtn = document.getElementById("camSnapshotBtn");
const camFullscreenBtn = document.getElementById("camFullscreenBtn");
const cameraFeedContainer = document.getElementById("cameraFeedContainer");
let isMirrored = false;

if (camMirrorBtn) {
  camMirrorBtn.addEventListener("click", () => {
    isMirrored = !isMirrored;
    if (cameraStreamImg) cameraStreamImg.classList.toggle("cam-mirrored", isMirrored);
    if (browserWebcamVideo) browserWebcamVideo.classList.toggle("cam-mirrored", isMirrored);
    camMirrorBtn.classList.toggle("active", isMirrored);
  });
}

if (camFullscreenBtn && cameraFeedContainer) {
  camFullscreenBtn.addEventListener("click", () => {
    if (!document.fullscreenElement) {
      cameraFeedContainer.requestFullscreen().catch((err) => {
        alert("Could not enter fullscreen: " + err.message);
      });
    } else {
      document.exitFullscreen();
    }
  });
}

if (camSnapshotBtn) {
  camSnapshotBtn.addEventListener("click", () => {
    const activeEl = isBrowserWebcamActive ? browserWebcamVideo : cameraStreamImg;
    if (!activeEl) return;

    try {
      const canvas = document.createElement("canvas");
      canvas.width = activeEl.videoWidth || activeEl.naturalWidth || 640;
      canvas.height = activeEl.videoHeight || activeEl.naturalHeight || 480;
      const ctx = canvas.getContext("2d");

      if (isMirrored) {
        ctx.translate(canvas.width, 0);
        ctx.scale(-1, 1);
      }
      ctx.drawImage(activeEl, 0, 0, canvas.width, canvas.height);

      const link = document.createElement("a");
      link.download = `posturecare-snapshot-${Date.now()}.png`;
      link.href = canvas.toDataURL("image/png");
      link.click();
    } catch (err) {
      alert("Snapshot error: " + err.message);
    }
  });
}
