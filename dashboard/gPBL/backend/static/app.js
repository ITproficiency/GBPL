const POLL_MS = 3000;
const chartPoints = [];
let isManuallyStopped = false;

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
  pitchVal: document.getElementById("pitchVal"),
  rollVal: document.getElementById("rollVal"),
  yawVal: document.getElementById("yawVal"),
  pitchChip: document.getElementById("pitchChip"),
  rollChip: document.getElementById("rollChip"),
  yawChip: document.getElementById("yawChip"),
  poseStatusBadge: document.getElementById("poseStatusBadge"),
  focusPostureStatus: document.getElementById("focusPostureStatus"),
  focusPostureIcon: document.getElementById("focusPostureIcon"),
  focusPitchChip: document.getElementById("focusPitchChip"),
  focusRollChip: document.getElementById("focusRollChip"),
  focusYawChip: document.getElementById("focusYawChip"),
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

  // Auto-connect video feed if AI tracking is active when user opens web page
  if (typeof cameraStreamImg !== "undefined" && !isBrowserWebcamActive && !isManuallyStopped) {
    const hasAIData = data.head_pitch_deg !== undefined || data.ear !== undefined || data.camera_distance_cm !== undefined;
    if (hasAIData) {
      if (!cameraStreamImg.src || !cameraStreamImg.src.includes("/api/video_feed")) {
        cameraStreamImg.src = "/api/video_feed?" + Date.now();
      }
      cameraStreamImg.style.display = "block";
      if (typeof camFallbackOverlay !== "undefined") camFallbackOverlay.style.display = "none";
      if (typeof cameraStatus !== "undefined" && cameraStatus.textContent !== "ESP32 STREAM") {
        cameraStatus.textContent = "ESP32 STREAM";
        cameraStatus.style.color = "#60a5fa";
      }
      if (currentTrackingState !== "running") {
        updateAIStatusUI("running", "ESP32-CAM");
      }
    }
  }

  // Head Pose & Rotation Angle Telemetry
  const th = data.head_pose_thresholds || {
    pitch_down_max_deg: 5.0,
    pitch_up_max_deg: 5.0,
    roll_max_deg: 10.0,
    yaw_max_deg: 20.0,
  };

  const pitch = data.head_pitch_deg;
  const roll = data.head_roll_deg;
  const yaw = data.head_yaw_deg;

  if (els.pitchVal) els.pitchVal.textContent = pitch != null ? (pitch > 0 ? "+" : "") + Number(pitch).toFixed(1) + "°" : "--";
  if (els.rollVal) els.rollVal.textContent = roll != null ? (roll > 0 ? "+" : "") + Number(roll).toFixed(1) + "°" : "--";
  if (els.yawVal) els.yawVal.textContent = yaw != null ? (yaw > 0 ? "+" : "") + Number(yaw).toFixed(1) + "°" : "--";

  // Individual chip state evaluation
  let pitchState = "ok";
  if (pitch != null) {
    if (pitch > th.pitch_down_max_deg || pitch < -th.pitch_up_max_deg) pitchState = "danger";
    else if (Math.abs(pitch) > 3.5) pitchState = "warning";
  }
  if (els.pitchChip) els.pitchChip.className = "pose-chip " + pitchState;

  let rollState = "ok";
  if (roll != null) {
    if (Math.abs(roll) > th.roll_max_deg) rollState = "danger";
    else if (Math.abs(roll) > 7.0) rollState = "warning";
  }
  if (els.rollChip) els.rollChip.className = "pose-chip " + rollState;

  let yawState = "ok";
  if (yaw != null) {
    if (Math.abs(yaw) > th.yaw_max_deg) yawState = "danger";
    else if (Math.abs(yaw) > 15.0) yawState = "warning";
  }
  if (els.yawChip) els.yawChip.className = "pose-chip " + yawState;

  // Posture status badge
  const postStatus = data.posture_status || (pitchState === "danger" || rollState === "danger" || yawState === "danger" ? "DANGER" : ((pitchState === "warning" || rollState === "warning" || yawState === "warning") ? "WARNING" : "GOOD"));

  if (els.poseStatusBadge) {
    let badgeText = postStatus;
    if (pitch != null && pitch > th.pitch_down_max_deg) badgeText = "HEAD TOO LOW";
    else if (pitch != null && pitch < -th.pitch_up_max_deg) badgeText = "HEAD TOO HIGH";
    else if (roll != null && Math.abs(roll) > th.roll_max_deg) badgeText = "HEAD TILTED";
    else if (yaw != null && Math.abs(yaw) > th.yaw_max_deg) badgeText = "HEAD TURNED";

    els.poseStatusBadge.textContent = badgeText;
    els.poseStatusBadge.className = "pose-status-badge status-" + postStatus.toLowerCase();
  }

  // Update Focus Hub posture banner
  if (els.focusPostureStatus) {
    els.focusPostureStatus.textContent = postStatus;
    els.focusPostureStatus.className = "posture-val status-" + postStatus.toLowerCase();
  }
  if (els.focusPostureIcon) {
    els.focusPostureIcon.textContent = postStatus === "DANGER" ? "⚠️" : (postStatus === "WARNING" ? "⚡" : "🛡️");
  }
  if (els.focusPitchChip) {
    els.focusPitchChip.textContent = "P: " + (pitch != null ? (pitch > 0 ? "+" : "") + Number(pitch).toFixed(1) + "°" : "--");
    els.focusPitchChip.className = "mini-chip " + pitchState;
  }
  if (els.focusRollChip) {
    els.focusRollChip.textContent = "R: " + (roll != null ? (roll > 0 ? "+" : "") + Number(roll).toFixed(1) + "°" : "--");
    els.focusRollChip.className = "mini-chip " + rollState;
  }
  if (els.focusYawChip) {
    els.focusYawChip.textContent = "Y: " + (yaw != null ? (yaw > 0 ? "+" : "") + Number(yaw).toFixed(1) + "°" : "--");
    els.focusYawChip.className = "mini-chip " + yawState;
  }

  if (els.riskLevel) {
    els.riskLevel.textContent = data.risk_level.toUpperCase();
    els.riskLevel.className = "value risk-" + data.risk_level;
  }
  if (els.lastUpdate) els.lastUpdate.textContent = "Updated " + formatTime(data.timestamp);
  if (els.headerRiskPill) {
    els.headerRiskPill.textContent = data.risk_level.toUpperCase();
    els.headerRiskPill.className = "risk-pill risk-" + data.risk_level;
  }

  // Update Green & Red LED Warning Badge
  const ledDotGreen = document.getElementById("ledDotGreen");
  const ledStatusText = document.getElementById("ledStatusText");
  if (ledDotGreen && ledStatusText) {
    const isGood = data.posture_status === "GOOD";
    ledDotGreen.className = "led-dot " + (isGood ? "green" : "red");
    ledStatusText.textContent = isGood ? "Green LED ON" : "Red LED ALERT!";
    ledStatusText.style.color = isGood ? "#10b981" : "#ef4444";
  }

  const msgs = (data.warning_messages || []).filter(m => m && !m.includes("PostureCare targets"));
  if (msgs.length === 0) {
    els.alertList.innerHTML = '<li class="empty">All readings within PostureCare targets</li>';
  } else {
    els.alertList.innerHTML = msgs.map(m => {
      const isHeadPose = m.toLowerCase().includes("head") || m.toLowerCase().includes("tilted") || m.toLowerCase().includes("turn");
      const icon = isHeadPose ? "👤" : (m.toLowerCase().includes("close") ? "📏" : "⚠️");
      return `
        <li class="alert-item ${data.risk_level}">
          <div class="alert-item-header">
            <strong>${icon} ${data.risk_level.toUpperCase()}</strong>
          </div>
          <div class="msg">${m}</div>
        </li>
      `;
    }).join("");
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

  // 3. Render 3D Head Orientation Axis Follow on Camera Feed Canvas
  const poseCanvas = document.getElementById("cameraPoseCanvas");
  if (poseCanvas) {
    drawHeadAxesOnCanvas(
      poseCanvas,
      data.head_pitch_deg,
      data.head_yaw_deg,
      data.head_roll_deg,
      data.nose_x,
      data.nose_y,
      isAxesFollowEnabled
    );
  }
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
  const d = rules.distance_cm || { target_min: 50, target_max: 70 };
  const b = rules.brightness_lux || { target_min: 300 };
  const s = rules.sitting_minutes || { max_continuous: 20 };
  const hp = rules.head_pose || { pitch_down_max_deg: 5, roll_max_deg: 10, yaw_max_deg: 20 };
  els.rulesInfo.innerHTML =
    `PostureCare rules: distance ${d.target_min}-${d.target_max} cm · ` +
    `light ${b.target_min}+ lux · sitting max ${s.max_continuous} min · ` +
    `head pose: pitch ±${hp.pitch_down_max_deg || 5}° · roll ±${hp.roll_max_deg || 10}° · yaw ±${hp.yaw_max_deg || 20}°`;
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
  cameraStreamImg.onload = () => {
    if (camFallbackOverlay && !isBrowserWebcamActive) {
      camFallbackOverlay.style.display = "none";
    }
    if (cameraStatus && !isBrowserWebcamActive) {
      cameraStatus.textContent = "ESP32 STREAM";
      cameraStatus.style.color = "#60a5fa";
    }
  };
  cameraStreamImg.onerror = () => {
    if (!isBrowserWebcamActive && currentTrackingState === "failed") {
      if (camFallbackOverlay) {
        camFallbackOverlay.style.display = "flex";
        camFallbackOverlay.innerHTML = `
          <p style="color:#f87171; font-weight: 600; font-size: 14px; margin-bottom: 6px;">⚠️ Không thể kết nối tới luồng ESP32-CAM</p>
          <small style="color:#cbd5e1; font-size: 12px; line-height: 1.5; display: block; max-width: 480px;">
            Vui lòng kiểm tra: <br/>
            1️⃣ ESP32-S3 đã được cấp nguồn điện chưa. <br/>
            2️⃣ Máy tính và ESP32-S3 có đang bắt <strong>CÙNG 1 MẠNG WI-FI</strong> không. <br/>
            3️⃣ Cú pháp địa chỉ IP đã đúng chưa (vd: <code>http://192.168.1.15:81/stream</code>).
          </small>
        `;
      }
      if (cameraStatus) {
        cameraStatus.textContent = "OFFLINE / UNREACHABLE";
        cameraStatus.style.color = "#f87171";
      }
      updateAIStatusUI("failed");
    }
  };
}

let currentTrackingState = "idle";
let currentTrackingSource = "None";

const aiStatusDot = document.getElementById("aiStatusDot");
const aiStatusText = document.getElementById("aiStatusText");
const aiSourceBadge = document.getElementById("aiSourceBadge");

function updateAIStatusUI(state, sourceLabel) {
  currentTrackingState = state;
  if (sourceLabel) currentTrackingSource = sourceLabel;

  if (aiStatusDot) {
    if (state === "running") {
      aiStatusDot.style.background = "#34d399";
      aiStatusDot.style.boxShadow = "0 0 10px #34d399";
    } else if (state === "connecting") {
      aiStatusDot.style.background = "#f59e0b";
      aiStatusDot.style.boxShadow = "0 0 10px #f59e0b";
    } else if (state === "failed") {
      aiStatusDot.style.background = "#f87171";
      aiStatusDot.style.boxShadow = "0 0 10px #f87171";
    } else {
      aiStatusDot.style.background = "#94a3b8";
      aiStatusDot.style.boxShadow = "none";
    }
  }

  if (aiStatusText) {
    if (state === "running") {
      aiStatusText.textContent = "🟢 AI Tracking Engine: ACTIVE & RUNNING";
      aiStatusText.style.color = "#34d399";
    } else if (state === "connecting") {
      aiStatusText.textContent = "🟡 AI Tracking Engine: INITIALIZING / CONNECTING...";
      aiStatusText.style.color = "#f59e0b";
    } else if (state === "failed") {
      aiStatusText.textContent = "🔴 AI Tracking Engine: CONNECTION FAILED (Check ESP32 IP / Power)";
      aiStatusText.style.color = "#f87171";
    } else {
      aiStatusText.textContent = "⚪ AI Tracking Engine: IDLE / STOPPED";
      aiStatusText.style.color = "#cbd5e1";
    }
  }

  if (aiSourceBadge) {
    aiSourceBadge.textContent = state !== "idle" ? `📷 Source: ${currentTrackingSource}` : "Source: None";
    aiSourceBadge.style.color = state === "running" ? "#60a5fa" : (state === "connecting" ? "#f59e0b" : "#94a3b8");
  }
}

function resetTrackingState() {
  chartPoints.length = 0;
  if (els.distance) els.distance.textContent = "--";
  if (els.earValue) els.earValue.textContent = "--";
  if (els.blinks) els.blinks.textContent = "0";
  if (els.blinkRate) els.blinkRate.textContent = "0";
  if (els.headPose) els.headPose.textContent = "--";
  if (els.riskLevel) {
    els.riskLevel.textContent = "NORMAL";
    els.riskLevel.className = "value risk-normal";
  }
  if (els.headerRiskPill) {
    els.headerRiskPill.textContent = "NORMAL";
    els.headerRiskPill.className = "risk-pill risk-normal";
  }
  if (els.alertList) {
    els.alertList.innerHTML = '<li class="empty">All readings within PostureCare targets</li>';
  }
  updateAIStatusUI("idle", "None");
  if (typeof ocularChart !== "undefined") {
    ocularChart.data.labels = [];
    ocularChart.data.datasets[0].data = [];
    ocularChart.data.datasets[1].data = [];
    ocularChart.update("none");
  }
  if (typeof distanceChart !== "undefined") {
    distanceChart.data.labels = [];
    distanceChart.data.datasets[0].data = [];
    distanceChart.data.datasets[1].data = [];
    distanceChart.update("none");
  }
}

if (connectCamBtn) {
  connectCamBtn.addEventListener("click", async () => {
    isManuallyStopped = false;
    if (isBrowserWebcamActive) stopBrowserWebcam();
    resetTrackingState();
    const url = streamUrlInput ? streamUrlInput.value.trim() : "";
    if (!url) {
      alert("Vui lòng nhập địa chỉ IP của ESP32 Camera (ví dụ: http://192.168.1.50:81/stream)");
      return;
    }
    if (cameraStreamImg) {
      cameraStreamImg.style.display = "block";
      if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
      if (cameraStatus) {
        cameraStatus.textContent = "ESP32 STREAM";
        cameraStatus.style.color = "#60a5fa";
      }
      updateAIStatusUI("connecting", "ESP32-CAM (" + url.replace("http://", "") + ")");
      try {
        await fetch("/api/tracking/stop", { method: "POST" });
        const res = await fetch("/api/tracking/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: url }),
        });
        const data = await res.json();
        if (data.status === "ok") {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
        } else {
          updateAIStatusUI("failed", "ESP32-CAM (" + url.replace("http://", "") + ")");
        }
      } catch (e) {
        console.error("Failed to start tracking:", e);
        updateAIStatusUI("failed", "ESP32-CAM (" + url.replace("http://", "") + ")");
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
    isManuallyStopped = false;
    resetTrackingState();
    try { await fetch("/api/tracking/stop", { method: "POST" }); } catch (e) {}
    if (isBrowserWebcamActive) {
      isBrowserWebcamActive = false;
      toggleWebcamBtn.textContent = "Use Local Webcam";
      const url = streamUrlInput ? streamUrlInput.value.trim() : "http://192.168.1.39:80/stream";
      updateAIStatusUI("connecting", "ESP32-CAM (" + url.replace("http://", "") + ")");
      if (cameraStatus) {
        cameraStatus.textContent = "ESP32 STREAM";
        cameraStatus.style.color = "#60a5fa";
      }
      try {
        await fetch("/api/tracking/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: url }),
        });
        if (cameraStreamImg) {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
          cameraStreamImg.style.display = "block";
        }
      } catch (e) {}
    } else {
      isBrowserWebcamActive = true;
      toggleWebcamBtn.textContent = "Switch to ESP32 Stream";
      if (cameraStatus) {
        cameraStatus.textContent = "LOCAL WEBCAM (DEVICE 0)";
        cameraStatus.style.color = "#34d399";
      }
      updateAIStatusUI("connecting", "Local Webcam (Device 0)");
      try {
        const res = await fetch("/api/tracking/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "0" }),
        });
        const data = await res.json();
        if (data.status === "ok" && cameraStreamImg) {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
          cameraStreamImg.style.display = "block";
          if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
          updateAIStatusUI("running", "Local Webcam (Device 0)");
        }
      } catch (err) {
        alert("Could not start local webcam tracking: " + err.message);
        updateAIStatusUI("failed", "Local Webcam (Device 0)");
      }
    }
  });
}

const stopCamBtn = document.getElementById("stopCamBtn");
if (stopCamBtn) {
  stopCamBtn.addEventListener("click", async () => {
    isManuallyStopped = true;
    stopBrowserWebcam();
    resetTrackingState();
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
    updateAIStatusUI("idle", "None");
    // Stop Python AI Tracking process
    try { await fetch("/api/tracking/stop", { method: "POST" }); } catch (e) {}
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

/* ---------------- 3D Head Orientation Axis Follow Overlay ---------------- */
let isAxesFollowEnabled = true;
const camAxesToggleBtn = document.getElementById("camAxesToggleBtn");

if (camAxesToggleBtn) {
  camAxesToggleBtn.addEventListener("click", () => {
    isAxesFollowEnabled = !isAxesFollowEnabled;
    camAxesToggleBtn.classList.toggle("active", isAxesFollowEnabled);
    const canvas = document.getElementById("cameraPoseCanvas");
    if (canvas && !isAxesFollowEnabled) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  });
}

/**
 * Draw 3D Head Pose Coordinate Axes (X: Red, Y: Green, Z: Blue)
 * Anchored to the nose tip (noseNormX, noseNormY) and dynamically rotating
 * according to calibrated Pitch, Yaw, Roll angles (matching blink_counter_and_EAR_plot.py).
 */
function drawHeadAxesOnCanvas(canvas, pitchDeg, yawDeg, rollDeg, noseNormX, noseNormY, isEnabled) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const width = rect.width || 640;
  const height = rect.height || 480;

  if (canvas.width !== Math.round(width) || canvas.height !== Math.round(height)) {
    canvas.width = Math.round(width);
    canvas.height = Math.round(height);
  }

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!isEnabled || pitchDeg == null || yawDeg == null || rollDeg == null) return;

  // Origin point based on normalized nose coordinates
  const nx = noseNormX != null && !isNaN(noseNormX) ? Number(noseNormX) : 0.5;
  const ny = noseNormY != null && !isNaN(noseNormY) ? Number(noseNormY) : 0.55;

  const originX = isMirrored ? (1.0 - nx) * canvas.width : nx * canvas.width;
  const originY = ny * canvas.height;

  // Convert angles to radians
  const p = (Number(pitchDeg) * Math.PI) / 180.0;
  const y = (Number(yawDeg) * Math.PI) / 180.0;
  const r = (Number(rollDeg) * Math.PI) / 180.0;

  // Combined rotation matrix R = Rz * Rx * Ry
  function rotateVector(vx, vy, vz) {
    // 1. Ry * v (around Y / Yaw)
    const ry_x = Math.cos(y) * vx + Math.sin(y) * vz;
    const ry_y = vy;
    const ry_z = -Math.sin(y) * vx + Math.cos(y) * vz;

    // 2. Rx * (Ry * v) (around X / Pitch)
    const rx_x = ry_x;
    const rx_y = Math.cos(p) * ry_y - Math.sin(p) * ry_z;
    const rx_z = Math.sin(p) * ry_y + Math.cos(p) * ry_z;

    // 3. Rz * (Rx * Ry * v) (around Z / Roll)
    const rz_x = Math.cos(r) * rx_x - Math.sin(r) * rx_y;
    const rz_y = Math.sin(r) * rx_x + Math.cos(r) * rx_y;
    const rz_z = rx_z;

    return [rz_x, rz_y, rz_z];
  }

  const length = Math.min(canvas.width, canvas.height) * 0.18;
  const sign = isMirrored ? -1 : 1;

  // 3 standard axes in space:
  // X: [length, 0, 0] (Red - right)
  // Y: [0, length, 0] (Green - down)
  // Z: [0, 0, -length] (Blue - out from nose toward camera)
  const ax_x = rotateVector(length, 0, 0);
  const ax_y = rotateVector(0, length, 0);
  const ax_z = rotateVector(0, 0, -length);

  const endX = { x: originX + ax_x[0] * sign, y: originY + ax_y[1] };
  const endY = { x: originX + ax_y[0] * sign, y: originY + ax_y[1] };
  const endZ = { x: originX + ax_z[0] * sign, y: originY + ax_z[1] };

  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  function drawAxis(end, color, label) {
    ctx.beginPath();
    ctx.moveTo(originX, originY);
    ctx.lineTo(end.x, end.y);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.stroke();

    // Axis label
    ctx.fillStyle = color;
    ctx.font = "bold 13px system-ui, sans-serif";
    ctx.shadowBlur = 4;
    ctx.fillText(label, end.x + (end.x >= originX ? 6 : -14), end.y + (end.y >= originY ? 12 : -5));
  }

  // Draw X (Red), Y (Green), Z (Blue)
  drawAxis(endX, "#ef4444", "X");
  drawAxis(endY, "#22c55e", "Y");
  drawAxis(endZ, "#38bdf8", "Z");

  // Draw bright origin point at nose tip
  ctx.beginPath();
  ctx.arc(originX, originY, 5, 0, 2 * Math.PI);
  ctx.fillStyle = "#ffffff";
  ctx.shadowColor = "#38bdf8";
  ctx.shadowBlur = 10;
  ctx.fill();
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.restore();
}

/* ---------------- Calibrate Gốc Tọa Độ (Head Pose Zero Reference) ---------------- */
function showToast(message, isSuccess = true) {
  let toast = document.getElementById("pcToastNotification");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "pcToastNotification";
    toast.className = "pc-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = "pc-toast show " + (isSuccess ? "success" : "error");
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => {
    toast.className = "pc-toast";
  }, 3500);
}

async function triggerCalibrateHeadPose(btn) {
  const originalText = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Calibrating...";
    btn.classList.add("calibrating-pulse");
  }

  try {
    const res = await fetch("/api/calibrate/head-pose", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast("🎯 Đã calibrate gốc tọa độ (0,0,0) thành công!", true);
      if (typeof playTone === "function") playTone(580, 160);
    } else {
      showToast("⚠️ Calibrate lỗi: " + (data.detail || "Không thể gửi yêu cầu"), false);
    }
  } catch (err) {
    showToast("⚠️ Lỗi kết nối: " + err.message, false);
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        btn.classList.remove("calibrating-pulse");
      }, 1000);
    }
  }
}

const cardCalibrateBtn = document.getElementById("cardCalibrateBtn");
if (cardCalibrateBtn) {
  cardCalibrateBtn.addEventListener("click", () => triggerCalibrateHeadPose(cardCalibrateBtn));
}

const camCalibrateBtn = document.getElementById("camCalibrateBtn");
if (camCalibrateBtn) {
  camCalibrateBtn.addEventListener("click", () => triggerCalibrateHeadPose(camCalibrateBtn));
}

const focusCalibrateBtn = document.getElementById("focusCalibrateBtn");
if (focusCalibrateBtn) {
  focusCalibrateBtn.addEventListener("click", () => triggerCalibrateHeadPose(focusCalibrateBtn));
}
