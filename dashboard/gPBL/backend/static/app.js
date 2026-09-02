const POLL_MS = 3000;
const SIT_RING_C = 2 * Math.PI * 90;
const GUIDED_STORAGE_KEY = "pc_guided_start";
const SESSION_DISPLAY_NAMES = { calibrating: "STARTING" };
const chartPoints = [];
let isManuallyStopped = false;
let pcRules = {};
let latestSession = null;
let latestReading = null;
let lastAdviceMode = "explain";
let analyzeInFlight = false;
let timelineSessionId = null;
let wizardDidCalibrate = false;
let wizardStep = 1;

const els = {
  distance: document.getElementById("distance"),
  ultrasonicDistance: document.getElementById("ultrasonicDistance"),
  earValue: document.getElementById("earValue"),
  earThreshold: document.getElementById("earThreshold"),
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
  focusSitChip: document.getElementById("focusSitChip"),
  sitTime: document.getElementById("sitTime"),
  sitChip: document.getElementById("sitChip"),
  sitRingProgress: document.getElementById("sitRingProgress"),
  riskLevel: document.getElementById("riskLevel"),
  lastUpdate: document.getElementById("lastUpdate"),
  posturalScore: document.getElementById("posturalScore"),
  posturalFill: document.getElementById("posturalFill"),
  posturalFactors: document.getElementById("posturalFactors"),
  posturalGauge: document.getElementById("posturalGauge"),
  visualScore: document.getElementById("visualScore"),
  visualFill: document.getElementById("visualFill"),
  visualFactors: document.getElementById("visualFactors"),
  visualGauge: document.getElementById("visualGauge"),
  sessionTimeline: document.getElementById("sessionTimeline"),
  distanceDelta: document.getElementById("distanceDelta"),
  sourceLights: document.getElementById("sourceLights"),
  srcSensorsDot: document.getElementById("srcSensorsDot"),
  srcCamDot: document.getElementById("srcCamDot"),
  srcAiDot: document.getElementById("srcAiDot"),
  adviceContent: document.getElementById("adviceContent"),
  advicePanel: document.getElementById("advicePanel"),
  analyzeBtn: document.getElementById("analyzeBtn"),
  analyzeStatus: document.getElementById("analyzeStatus"),
  rulesInfo: document.getElementById("rulesInfo"),
  insightBtn: document.getElementById("insightBtn"),
  insightStatus: document.getElementById("insightStatus"),
  insightContent: document.getElementById("insightContent"),
  sessionReportBtn: document.getElementById("sessionReportBtn"),
  headerRiskPill: document.getElementById("headerRiskPill"),
  sessionStateBadge: document.getElementById("sessionStateBadge"),
  dndChip: document.getElementById("dndChip"),
  sittingDemoInput: document.getElementById("sittingDemoInput"),
  guidedStartOverlay: document.getElementById("guidedStartOverlay"),
};

if (els.sitRingProgress) {
  els.sitRingProgress.style.strokeDasharray = String(SIT_RING_C);
  els.sitRingProgress.style.strokeDashoffset = String(SIT_RING_C);
}

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
        label: "Blink rate (bpm)",
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
      y1: { position: "right", ticks: { color: "#34d399" }, grid: { drawOnChartArea: false }, title: { display: true, text: "bpm", color: "#34d399" } },
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
        yAxisID: "y",
        spanGaps: false,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: "#8b9cb3" } } },
    scales: {
      x: { ticks: { color: "#8b9cb3", maxTicksLimit: 8 }, grid: { color: "#2d3a4f" } },
      y: {
        min: 20,
        max: 120,
        ticks: { color: "#8b9cb3" },
        grid: { color: "#2d3a4f" },
        title: { display: true, text: "cm", color: "#8b9cb3" },
      },
    },
  },
  plugins: [
    {
      id: "distanceDeltaLegend",
      afterUpdate(chart) {
        const ai = chart.data.datasets[0]?.data || [];
        const ultra = chart.data.datasets[1]?.data || [];
        let text = "";
        for (let i = ai.length - 1; i >= 0; i--) {
          if (ai[i] != null && ultra[i] != null && Number.isFinite(ai[i]) && Number.isFinite(ultra[i])) {
            text = `Δ = ${Math.abs(ai[i] - ultra[i]).toFixed(1)} cm`;
            break;
          }
        }
        if (els.distanceDelta) els.distanceDelta.textContent = text;
      },
    },
  ],
});

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString();
}

function formatHeadPose(pitch, roll, yaw) {
  if (pitch == null && roll == null && yaw == null) return "--";
  const fmt = (v) => (v == null ? "-" : Math.round(v) + "°");
  return `P ${fmt(pitch)} · R ${fmt(roll)} · Y ${fmt(yaw)}`;
}

function formatSitClock(totalSeconds) {
  if (typeof formatMMSS === "function") return formatMMSS(totalSeconds);
  const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = (sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function sittingRules(rules) {
  return rules || pcRules || {};
}

function isSittingDemoMode(rules, session) {
  if (session && session.demo_mode != null) return !!session.demo_mode;
  const r = sittingRules(rules);
  if (r.demo_mode != null) return !!r.demo_mode;
  const sitting = r.sitting_minutes || {};
  if (sitting.demo_mode != null) return !!sitting.demo_mode;
  return true;
}

function sittingDemoMinutes(rules, session) {
  const fromSession = Number(session && session.demo_max_minutes);
  if (fromSession > 0) return fromSession;
  const r = sittingRules(rules);
  const fromRules = Number(r.demo_max_minutes);
  if (fromRules > 0) return fromRules;
  return Number((r.sitting_minutes || {}).demo_max_minutes) || 3;
}

function sittingThresholdSec(rules, session) {
  const fromSession = Number(session && session.sitting_threshold_sec);
  if (fromSession > 0) return fromSession;
  const r = sittingRules(rules);
  const fromRules = Number(r.sitting_threshold_sec);
  if (fromRules > 0) return fromRules;
  const sitting = r.sitting_minutes || {};
  if (isSittingDemoMode(r, session)) {
    return (Number(sitting.demo_max_minutes || r.demo_max_minutes) || 3) * 60;
  }
  return (Number(sitting.max_continuous) || 20) * 60;
}

function applySittingDemoMode(demoMode, extra) {
  extra = extra || {};
  const sitting = pcRules.sitting_minutes || {};
  const demoMin = Number(extra.demo_max_minutes || sitting.demo_max_minutes || pcRules.demo_max_minutes) || 3;
  const maxCont = Number(extra.max_continuous || sitting.max_continuous) || 20;
  const threshold = Number(extra.sitting_threshold_sec) || (demoMode ? demoMin * 60 : maxCont * 60);
  pcRules = {
    ...pcRules,
    demo_mode: !!demoMode,
    demo_max_minutes: demoMin,
    sitting_threshold_sec: threshold,
    sitting_minutes: {
      ...sitting,
      demo_mode: !!demoMode,
      demo_max_minutes: demoMin,
      max_continuous: maxCont,
    },
  };
  if (latestSession) {
    latestSession = {
      ...latestSession,
      demo_mode: !!demoMode,
      demo_max_minutes: demoMin,
      sitting_threshold_sec: threshold,
    };
  }
  if (els.sittingDemoInput) els.sittingDemoInput.checked = !!demoMode;
  renderSitting(latestReading, latestSession);
  renderAxisGauges(latestReading, latestSession);
}

function isSessionIdle(session) {
  const state = String(session?.state || "idle").toLowerCase();
  return state === "idle" || state === "ended";
}

function renderSitting(reading, session) {
  const idle = isSessionIdle(session);
  const threshold = sittingThresholdSec(pcRules, session);
  const demo = isSittingDemoMode(pcRules, session);
  const exposure = idle ? null : Number(session && session.exposure_sec);
  const hasExposure = exposure != null && Number.isFinite(exposure);
  const fill = hasExposure && threshold > 0 ? Math.min(1, Math.max(0, exposure / threshold)) : 0;

  if (els.sitTime) els.sitTime.textContent = hasExposure ? formatSitClock(exposure) : "--";
  if (els.sitRingProgress) {
    els.sitRingProgress.style.strokeDasharray = String(SIT_RING_C);
    els.sitRingProgress.style.strokeDashoffset = String(SIT_RING_C * (1 - fill));
  }
  if (els.sitChip) {
    const mins = demo
      ? sittingDemoMinutes(pcRules, session)
      : Number((pcRules.sitting_minutes || {}).max_continuous) || 20;
    els.sitChip.textContent = demo ? `DEMO ${mins} MIN` : `${mins} MIN`;
    els.sitChip.className = "sit-chip" + (demo ? "" : " sit-standard");
  }
  const sitCard = els.sitTime && els.sitTime.closest(".card-sitting");
  if (sitCard) sitCard.classList.toggle("sit-over", hasExposure && exposure >= threshold);

  if (els.focusSitChip) {
    els.focusSitChip.textContent = hasExposure
      ? `Sit ${formatSitClock(exposure)} / ${formatSitClock(threshold)}`
      : "Sit --";
    els.focusSitChip.className = "mini-chip" + (hasExposure && exposure >= threshold ? " danger" : hasExposure ? " ok" : "");
  }
  void reading;
}

function factorInTarget(ok) {
  return ok === true;
}

function evaluateAxisFactors(reading, session) {
  const rules = pcRules || {};
  const dist = rules.distance_cm || { target_min: 50, target_max: 70 };
  const luxCfg = rules.brightness_lux || { target_min: 300 };
  const blinkCfg = rules.blink_rate || { target_min_bpm: 6 };
  const hp = rules.head_pose || { pitch_down_max_deg: 5, pitch_up_max_deg: 5, roll_max_deg: 15, yaw_max_deg: 20 };
  const pitchLimDown = hp.pitch_down_max_deg ?? hp.pitch_forward_max_deg ?? 5;
  const pitchLimUp = hp.pitch_up_max_deg ?? 5;
  const rollLim = hp.roll_max_deg ?? 15;
  const yawLim = hp.yaw_max_deg ?? 20;
  const threshold = sittingThresholdSec(pcRules, session);
  const idle = isSessionIdle(session);
  const exposure = idle ? null : Number(session?.exposure_sec);
  const sittingOk = exposure == null || !Number.isFinite(exposure) ? null : exposure < threshold;

  const pitch = reading?.head_pitch_deg;
  const roll = reading?.head_roll_deg;
  const yaw = reading?.head_yaw_deg;
  const distance = reading?.distance_cm;
  const bpm = reading?.blink_rate_bpm;
  const lux = reading?.brightness_lux;

  const postural = [
    { id: "pitch", label: "Pitch", ok: pitch == null ? null : !(pitch > pitchLimDown || pitch < -pitchLimUp) },
    { id: "roll", label: "Roll", ok: roll == null ? null : Math.abs(roll) <= rollLim },
    { id: "yaw", label: "Yaw", ok: yaw == null ? null : Math.abs(yaw) <= yawLim },
    {
      id: "distance",
      label: "Distance",
      ok: distance == null ? null : distance >= dist.target_min && distance <= dist.target_max,
    },
    { id: "sitting", label: "Sitting", ok: sittingOk },
  ];

  const visual = [
    { id: "blink", label: "Blink", ok: bpm == null ? null : bpm >= (blinkCfg.target_min_bpm ?? 6) },
    {
      id: "lux",
      label: "Light",
      ok: lux == null ? null : lux >= (luxCfg.target_min ?? 300) && (luxCfg.target_max == null || lux <= luxCfg.target_max),
    },
    { id: "sitting", label: "Screen time", ok: sittingOk },
  ];

  return { postural, visual };
}

function renderGauge(factors, scoreEl, fillEl, listEl, gaugeEl) {
  const counted = factors.filter((f) => f.ok !== null);
  const inTarget = counted.filter((f) => factorInTarget(f.ok));
  const pct = counted.length ? Math.round((inTarget.length / counted.length) * 100) : null;
  if (scoreEl) scoreEl.textContent = pct == null ? "--" : `${pct}%`;
  if (fillEl) fillEl.style.width = pct == null ? "0%" : `${pct}%`;
  if (gaugeEl) {
    gaugeEl.classList.remove("score-warn", "score-low");
    if (pct != null && pct < 50) gaugeEl.classList.add("score-low");
    else if (pct != null && pct < 80) gaugeEl.classList.add("score-warn");
  }
  const out = factors.filter((f) => f.ok === false);
  if (listEl) {
    if (!out.length) {
      listEl.innerHTML = '<li class="empty">All factors in target</li>';
    } else {
      listEl.innerHTML = out.map((f) => `<li>${escapeHtml(f.label)}</li>`).join("");
    }
  }
}

function renderAxisGauges(reading, session) {
  if (!els.posturalScore && !els.visualScore) return;
  const { postural, visual } = evaluateAxisFactors(reading, session);
  renderGauge(postural, els.posturalScore, els.posturalFill, els.posturalFactors, els.posturalGauge);
  renderGauge(visual, els.visualScore, els.visualFill, els.visualFactors, els.visualGauge);
}

function parseSourceEntry(entry) {
  if (entry == null) return { kind: "stale", hint: "no data yet" };
  if (typeof entry === "boolean") return { kind: entry ? "live" : "stale", hint: entry ? "live" : "stale" };
  if (typeof entry === "string") {
    const s = entry.toLowerCase();
    if (s === "unused") return { kind: "unused", hint: "unused" };
    if (s === "live") return { kind: "live", hint: "live" };
    return { kind: "stale", hint: s === "stale" ? "stale" : entry };
  }
  const status = String(entry.status || "").toLowerCase();
  let kind = "stale";
  if (status === "live" || status === "unused" || status === "stale") kind = status;
  else if (entry.unused) kind = "unused";
  else if (entry.live === true) kind = "live";

  const bits = [kind];
  const age = entry.age_sec ?? entry.face_age_sec;
  if (age != null && Number.isFinite(Number(age))) bits.push(`${Math.round(Number(age))}s`);
  if (entry.source) bits.push(String(entry.source));
  if (entry.tracking_active === true) bits.push("tracking");
  if (entry.face_present === true) bits.push("face");
  else if (entry.face_present === false) bits.push("no face");
  return { kind, hint: bits.join(" · ") };
}

function renderSourceLights(sources) {
  if (!els.sourceLights) return;
  const src = sources || {};
  const items = [
    { key: "sensors", el: els.srcSensorsDot, label: "Sensors" },
    { key: "cam", el: els.srcCamDot, label: "Cam" },
    { key: "ai", el: els.srcAiDot, label: "AI" },
  ];
  const hints = [];
  items.forEach(({ key, el, label }) => {
    const parsed = parseSourceEntry(src[key]);
    const wrap = el && el.closest(".source-light");
    if (wrap) {
      wrap.classList.remove("live", "down", "unused", "stale");
      if (parsed.kind === "live") wrap.classList.add("live");
      else if (parsed.kind === "unused") wrap.classList.add("unused");
      else wrap.classList.add("stale", "down");
    }
    hints.push(`${label}: ${parsed.hint}`);
  });
  els.sourceLights.title = hints.join(" · ");
}

function timelineBuckets(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload.buckets) ? payload.buckets : [];
}

function bucketRisk(bucket) {
  if (bucket == null || typeof bucket !== "object") return null;
  const risk = bucket.risk_level;
  if (risk == null || risk === "") return null;
  return String(risk).toLowerCase();
}

function renderTimeline(payload) {
  const svg = els.sessionTimeline;
  if (!svg) return;
  const buckets = timelineBuckets(payload);
  const width = 600;
  const height = 16;
  if (!buckets.length) {
    svg.innerHTML = `<rect x="0" y="0" width="${width}" height="${height}" fill="rgba(255,255,255,0.03)" />`;
    return;
  }
  const n = buckets.length;
  const w = width / n;
  const colors = {
    normal: "#22c55e",
    warning: "#f59e0b",
    notice: "#f59e0b",
    high: "#ef4444",
    alert: "#ef4444",
    escalated: "#ef4444",
  };
  svg.innerHTML = buckets
    .map((b, i) => {
      const risk = bucketRisk(b);
      const fill = risk ? colors[String(risk).toLowerCase()] || "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.03)";
      return `<rect x="${(i * w).toFixed(2)}" y="0" width="${w.toFixed(2)}" height="${height}" fill="${fill}" />`;
    })
    .join("");
}

async function fetchTimeline() {
  try {
    const sid = latestSession && latestSession.session_id;
    const url = sid
      ? `/api/session/timeline?minutes=10&session_id=${encodeURIComponent(sid)}`
      : "/api/session/timeline?minutes=10";
    const res = await fetch(url);
    if (!res.ok) {
      renderTimeline({ buckets: [] });
      return;
    }
    const data = await res.json();
    timelineSessionId = data.session_id || sid;
    renderTimeline(data);
  } catch {
    renderTimeline({ buckets: [] });
  }
}

function loadGuidedDismiss() {
  try {
    return JSON.parse(localStorage.getItem(GUIDED_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveGuidedDismiss(rec) {
  try {
    localStorage.setItem(GUIDED_STORAGE_KEY, JSON.stringify(rec));
  } catch {
    // Private browsing — overlay may repeat.
  }
}

function shouldShowGuidedOverlay(session) {
  const state = String(session && session.state || "").toLowerCase();
  if (["monitoring", "away", "break"].includes(state)) return false;
  const rec = loadGuidedDismiss();
  const sid = session && session.session_id;
  if (sid && rec.dismissedSessionId === sid) return false;
  if (!sid && rec.skipArmed) return false;
  return true;
}

function dismissGuidedOverlay(session) {
  const sid = session && session.session_id;
  saveGuidedDismiss({
    dismissedSessionId: sid || null,
    skipArmed: !sid,
  });
  hideGuidedOverlay();
}

function noteGuidedSession(session) {
  const rec = loadGuidedDismiss();
  const sid = session && session.session_id;
  if (sid && rec.skipArmed) {
    saveGuidedDismiss({ dismissedSessionId: sid, skipArmed: false });
  }
}

function isDetailsTabActive() {
  const view = document.getElementById("detailsView");
  return !!(view && view.classList.contains("active"));
}

function hideGuidedOverlay() {
  if (els.guidedStartOverlay) els.guidedStartOverlay.hidden = true;
}

function showGuidedOverlay() {
  if (!els.guidedStartOverlay) return;
  const wasHidden = els.guidedStartOverlay.hidden;
  if (wasHidden) {
    wizardStep = 1;
    wizardDidCalibrate = false;
    const next2 = document.getElementById("guidedStep2Next");
    const next3 = document.getElementById("guidedStep3Next");
    if (next2) next2.disabled = true;
    if (next3) next3.disabled = true;
    const urlEl = document.getElementById("guidedStreamUrl");
    const camUrl = document.getElementById("streamUrlInput");
    if (urlEl && camUrl && camUrl.value) urlEl.value = camUrl.value;
  }
  els.guidedStartOverlay.hidden = false;
  if (wasHidden) setGuidedStep(1);
}

function syncGuidedOverlay(session) {
  noteGuidedSession(session);
  if (!isDetailsTabActive()) {
    hideGuidedOverlay();
    return;
  }
  if (shouldShowGuidedOverlay(session)) showGuidedOverlay();
  else hideGuidedOverlay();
}

function setGuidedStep(step) {
  wizardStep = step;
  for (let i = 1; i <= 4; i++) {
    const pane = document.getElementById("guidedPane" + i);
    if (pane) pane.hidden = i !== step;
  }
  document.querySelectorAll("#guidedStepsIndicator li").forEach((li) => {
    const n = Number(li.dataset.gstep);
    li.classList.toggle("active", n === step);
    li.classList.toggle("done", n < step);
  });
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
      ${s.corrected_label ? `<span>${escapeHtml(s.corrected_label)}</span>` : ""}
    </div>
    <p class="advice-meta">${item.model_name || "LLM"} · ${formatTime(item.created_at || new Date().toISOString())}</p>`;
}

function renderSessionBadge(session) {
  if (!els.sessionStateBadge || !session) return;
  const state = String(session.state || "idle").toLowerCase();
  els.sessionStateBadge.textContent = SESSION_DISPLAY_NAMES[state] || state.toUpperCase();
  els.sessionStateBadge.className = "session-badge state-" + state;
  if (els.dndChip) {
    els.dndChip.hidden = !session.dnd;
    els.dndChip.classList.toggle("active", !!session.dnd);
  }
}

/** Display only — all risk logic comes from backend processing.py */
function renderSensor(data, session) {
  if (!data) return;
  if (els.distance) els.distance.textContent = data.distance_cm != null ? Math.round(data.distance_cm) : "--";
  if (els.ultrasonicDistance) els.ultrasonicDistance.textContent = data.ultrasonic_distance_cm != null ? Math.round(data.ultrasonic_distance_cm) : "--";
  if (els.earValue) els.earValue.textContent = data.ear != null ? Number(data.ear).toFixed(3) : "--";
  if (els.earThreshold) els.earThreshold.textContent = data.ear_threshold != null ? Number(data.ear_threshold).toFixed(3) : "0.294";
  if (els.blinkRate) els.blinkRate.textContent = data.blink_rate_bpm != null ? Number(data.blink_rate_bpm).toFixed(1) : "--";
  if (els.brightness) {
    // A saturated LDR is censored, not measured — show it as a bound so a
    // pegged sensor never reads as an ordinary value.
    const luxPrefix =
      data.light_status === "above_range" ? "≥" : data.light_status === "below_range" ? "≤" : "";
    els.brightness.textContent =
      data.brightness_lux != null ? luxPrefix + Math.round(data.brightness_lux) : "--";
  }
  if (els.sittingMinutes) els.sittingMinutes.textContent = data.sitting_minutes ?? "--";
  if (els.headPose) els.headPose.textContent = formatHeadPose(data.head_pitch_deg, data.head_roll_deg, data.head_yaw_deg);

  // Auto-connect video feed if AI tracking is active when user opens web page
  if (typeof cameraStreamImg !== "undefined" && !isBrowserWebcamActive && !isManuallyStopped) {
    const hasAIData = data.face_present === true || data.head_pitch_deg != null || data.ear != null;
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
    roll_max_deg: 15.0,
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

  const riskLevel = (session && session.severity && session.state && session.state !== "idle")
    ? session.severity
    : (data.risk_level || "normal");
  if (els.riskLevel) {
    els.riskLevel.textContent = riskLevel.toUpperCase();
    els.riskLevel.className = "value risk-" + riskLevel;
  }
  if (els.lastUpdate) els.lastUpdate.textContent = "Updated " + formatTime(data.timestamp || new Date().toISOString());
  if (els.headerRiskPill) {
    els.headerRiskPill.textContent = riskLevel.toUpperCase();
    els.headerRiskPill.className = "risk-pill risk-" + riskLevel;
  }

  renderSitting(data, session);
  renderAxisGauges(data, session);

  const hasIssue =
    (session && session.severity && session.severity !== "normal") ||
    (Array.isArray(session?.flag_set) && session.flag_set.length > 0) ||
    (Array.isArray(data.flag_set) && data.flag_set.length > 0) ||
    !!(data.llm_eligible) ||
    ["warning", "high", "notice", "alert", "escalated"].includes(riskLevel);
  lastAdviceMode = hasIssue ? "advice" : "explain";
  if (els.analyzeBtn) {
    els.analyzeBtn.textContent = hasIssue ? "Get Advice" : "Explain current state";
    if (!analyzeInFlight) els.analyzeBtn.disabled = false;
  }

  // Ungated: reminders do not depend on Pomodoro focus mode.
  // notifyRiskHigh must not speak raw warning_messages (handled in focus.js).
  if (typeof applySessionGovernor === "function" && session) {
    applySessionGovernor(session);
  }

  chartPoints.push(data);
  if (chartPoints.length > 30) chartPoints.shift();
  const timeLabels = chartPoints.map((r) => formatTime(r.timestamp));

  // 1. Update Ocular & Blink Chart (EAR & blink rate bpm)
  ocularChart.data.labels = timeLabels;
  ocularChart.data.datasets[0].data = chartPoints.map((r) => r.ear);
  ocularChart.data.datasets[1].data = chartPoints.map((r) => (r.blink_rate_bpm != null ? r.blink_rate_bpm : null));
  ocularChart.update("none");

  // 2. Update Distance Chart (Camera AI & Ultrasonic Sensor)
  distanceChart.data.labels = timeLabels;
  distanceChart.data.datasets[0].data = chartPoints.map((r) => r.distance_cm);
  distanceChart.data.datasets[1].data = chartPoints.map((r) => (r.ultrasonic_distance_cm != null ? r.ultrasonic_distance_cm : null));
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

/* ---------------- Light sensor calibration ---------------- */

const lightCalibEls = {
  circuit: document.getElementById("lightCircuitInput"),
  rFixed: document.getElementById("lightRFixedInput"),
  lowAdc: document.getElementById("lightLowAdcInput"),
  lowLux: document.getElementById("lightLowLuxInput"),
  highAdc: document.getElementById("lightHighAdcInput"),
  highLux: document.getElementById("lightHighLuxInput"),
  sat: document.getElementById("lightSatInput"),
  gamma: document.getElementById("lightGammaValue"),
  linear: document.getElementById("lightLinearValue"),
  ceiling: document.getElementById("lightCeilingValue"),
  hint: document.getElementById("lightGammaHint"),
  save: document.getElementById("lightCalibSaveBtn"),
  status: document.getElementById("lightCalibStatus"),
};

function setIfIdle(el, value) {
  // Never overwrite a field the user is mid-edit in — /api/rules is polled.
  if (!el || el === document.activeElement || value == null) return;
  el.value = value;
}

function applyLightCalibration(cal) {
  if (!cal) return;
  setIfIdle(lightCalibEls.circuit, cal.circuit);
  setIfIdle(lightCalibEls.rFixed, cal.r_fixed_ohm);
  setIfIdle(lightCalibEls.lowAdc, cal.low_adc);
  setIfIdle(lightCalibEls.lowLux, cal.low_lux);
  setIfIdle(lightCalibEls.highAdc, cal.high_adc);
  setIfIdle(lightCalibEls.highLux, cal.high_lux);
  setIfIdle(lightCalibEls.sat, cal.adc_saturation);

  if (lightCalibEls.gamma) {
    lightCalibEls.gamma.textContent = cal.fitted_gamma != null ? cal.fitted_gamma.toFixed(2) : "--";
    lightCalibEls.gamma.className = "calib-stat-v" + (cal.fitted_gamma != null && !cal.gamma_plausible ? " bad" : "");
  }
  if (lightCalibEls.linear) {
    lightCalibEls.linear.textContent =
      cal.lux_at_linear_max != null ? Math.round(cal.lux_at_linear_max) + " lx" : "--";
  }
  if (lightCalibEls.ceiling) {
    lightCalibEls.ceiling.textContent =
      cal.lux_at_saturation != null ? Math.round(cal.lux_at_saturation) + " lx" : "--";
  }
  if (lightCalibEls.hint) {
    if (cal.fitted_gamma == null) {
      lightCalibEls.hint.textContent = "No usable fit — check the two reference points.";
    } else if (!cal.gamma_plausible) {
      lightCalibEls.hint.textContent =
        `γ = ${cal.fitted_gamma} is outside the 0.5–0.9 range of a CdS cell. The wiring or the fixed resistor is probably wrong.`;
    } else {
      const target = (pcRules.brightness_lux || {}).target_min;
      const linear = cal.lux_at_linear_max;
      lightCalibEls.hint.textContent =
        target != null && linear != null && linear < target
          ? `γ looks right, but the usable ceiling (${Math.round(linear)} lx) is below the ${target} lx target — the divider saturates before normal desk lighting.`
          : "γ is within the range expected of a CdS cell.";
    }
  }
}

if (lightCalibEls.save) {
  lightCalibEls.save.addEventListener("click", async () => {
    const num = (el) => (el && el.value !== "" ? Number(el.value) : null);
    const body = {
      circuit: lightCalibEls.circuit ? lightCalibEls.circuit.value : undefined,
      r_fixed_ohm: num(lightCalibEls.rFixed),
      low_adc: num(lightCalibEls.lowAdc),
      low_lux: num(lightCalibEls.lowLux),
      high_adc: num(lightCalibEls.highAdc),
      high_lux: num(lightCalibEls.highLux),
      adc_saturation: num(lightCalibEls.sat),
      source: "measured via dashboard",
    };
    Object.keys(body).forEach((k) => (body[k] == null) && delete body[k]);

    lightCalibEls.save.disabled = true;
    if (lightCalibEls.status) lightCalibEls.status.textContent = "Saving...";
    try {
      const res = await fetch("/api/settings/light-calibration", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (lightCalibEls.status) lightCalibEls.status.textContent = data.detail || "Couldn't save";
      } else {
        applyLightCalibration(data);
        if (lightCalibEls.status) lightCalibEls.status.textContent = "Saved";
        showToast("Light calibration saved.", true);
        fetchSensor();
      }
    } catch (err) {
      if (lightCalibEls.status) lightCalibEls.status.textContent = "Connection error";
    }
    lightCalibEls.save.disabled = false;
  });
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

  pcRules = rules;
  applyLightCalibration(rules.light_calibration);
  if (els.sittingDemoInput) {
    els.sittingDemoInput.checked = isSittingDemoMode(rules, latestSession);
  }
  renderSitting(latestReading, latestSession);
  renderAxisGauges(latestReading, latestSession);

  const d = rules.distance_cm || { target_min: 50, target_max: 70 };
  const b = rules.brightness_lux || { target_min: 300 };
  const s = rules.sitting_minutes || { max_continuous: 20 };
  const hp = rules.head_pose || { pitch_down_max_deg: 5, pitch_forward_max_deg: 5, roll_max_deg: 15, yaw_max_deg: 20 };
  const pitchLim = hp.pitch_down_max_deg ?? hp.pitch_forward_max_deg ?? 5;
  const rollLim = hp.roll_max_deg ?? 15;
  const yawLim = hp.yaw_max_deg ?? 20;
  const poseHint = document.querySelector(".pose-limits-hint");
  if (poseHint) {
    poseHint.textContent = `Limits: P ±${pitchLim}° · R ±${rollLim}° · Y ±${yawLim}°`;
  }
  const sitLabel = isSittingDemoMode(rules, latestSession)
    ? `sitting demo ${sittingDemoMinutes(rules, latestSession)} min`
    : `sitting max ${s.max_continuous || 20} min`;
  if (!els.rulesInfo) return;
  els.rulesInfo.innerHTML =
    `PostureCare rules: distance ${d.target_min}-${d.target_max} cm · ` +
    `light ${b.target_min}+ lux · ${sitLabel} · ` +
    `head pose: pitch ±${pitchLim}° · roll ±${rollLim}° · yaw ±${yawLim}°`;
}

function applySnapshot(data) {
  if (!data || typeof data !== "object") return;
  const session = data.session;
  if (session) {
    latestSession = session;
    renderSessionBadge(session);
    if (typeof applySessionGovernor === "function") applySessionGovernor(session);
  }
  const sources = data.sources || (session && session.sources);
  if (sources) renderSourceLights(sources);
  const wrapped = data.reading !== undefined || data.session || data.sources;
  const reading = data.reading !== undefined ? data.reading : wrapped ? null : data;
  if (reading) {
    latestReading = reading;
    renderSensor(reading, session || latestSession);
  } else if (session) {
    renderSitting(latestReading, session);
    renderAxisGauges(latestReading, session);
  }
}

async function fetchSensor() {
  const res = await fetch("/api/sensor");
  if (!res.ok) {
    if (els.lastUpdate) els.lastUpdate.textContent = "Backend not running";
    return;
  }
  const data = await res.json();
  if (!data) return;
  applySnapshot(data);
  syncGuidedOverlay(data.session || latestSession);
}

async function fetchAdvice() {
  const res = await fetch("/api/advice?limit=1");
  if (!res.ok) return;
  const { items } = await res.json();
  if (items[0]) renderAdvice(items[0]);
}

els.analyzeBtn.addEventListener("click", async () => {
  if (analyzeInFlight) return;
  analyzeInFlight = true;
  els.analyzeBtn.disabled = true;
  const mode = lastAdviceMode || "explain";
  els.analyzeStatus.textContent = mode === "explain" ? "Explaining..." : "Analyzing...";
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      els.analyzeStatus.textContent = data.detail || "Analyze failed";
    } else if (data.status === "skipped") {
      els.analyzeStatus.textContent = data.message;
      if (data.reading) renderSensor(data.reading, data.session || latestSession);
    } else {
      renderAdvice(data.advice);
      els.analyzeStatus.textContent = mode === "explain" ? "Explanation saved" : "Advice saved";
      if (data.reading) renderSensor(data.reading, data.session || latestSession);
      fetchAdvice();
      if (typeof speak === "function") {
        speak(truncateForSpeech(data.advice.spoken_line || data.advice.summary));
      }
    }
  } catch {
    els.analyzeStatus.textContent = "Connection error";
  }
  analyzeInFlight = false;
  if (els.analyzeBtn) els.analyzeBtn.disabled = false;
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
    const url = "/api/insights";
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      els.insightStatus.textContent = data.detail || "Insight failed";
    } else {
      els.insightStatus.textContent = "Insight generated";
      fetchInsight();
      const spoken = (data.advice && data.advice.summary) || (typeof data.summary === "string" ? data.summary : "");
      if (spoken && typeof speak === "function") speak(truncateForSpeech(spoken));
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
  if (tabName === "details") syncGuidedOverlay(latestSession);
  else hideGuidedOverlay();
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
  await Promise.allSettled([fetchSensor(), fetchAdvice(), fetchInsight(), fetchTimeline()]);
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
          <p style="color:#f87171; font-weight: 600; font-size: 14px; margin-bottom: 6px;">Could not connect to the ESP32-CAM stream</p>
          <small style="color:#cbd5e1; font-size: 12px; line-height: 1.5; display: block; max-width: 480px;">
            Please check: <br/>
            1. The ESP32-S3 is powered on. <br/>
            2. This computer and the ESP32-S3 are on the <strong>same Wi-Fi network</strong>. <br/>
            3. The stream URL is correct (e.g. <code>http://192.168.1.15:81/stream</code>).
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

function updateAIStatusUI(state, sourceLabel, detail) {
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
      aiStatusText.textContent = detail
        ? "🔴 AI Tracking Engine: " + detail
        : "🔴 AI Tracking Engine: CONNECTION FAILED (Check ESP32 IP / Power)";
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
  latestReading = null;
  if (els.distance) els.distance.textContent = "--";
  if (els.earValue) els.earValue.textContent = "--";
  if (els.blinkRate) els.blinkRate.textContent = "--";
  if (els.headPose) els.headPose.textContent = "--";
  if (els.riskLevel) {
    els.riskLevel.textContent = "NORMAL";
    els.riskLevel.className = "value risk-normal";
  }
  if (els.headerRiskPill) {
    els.headerRiskPill.textContent = "NORMAL";
    els.headerRiskPill.className = "risk-pill risk-normal";
  }
  if (els.distanceDelta) els.distanceDelta.textContent = "";
  updateAIStatusUI("idle", "None");
  renderSessionBadge({ state: "idle" });
  renderSitting(null, { state: "idle" });
  renderAxisGauges(null, { state: "idle" });
  renderTimeline({ buckets: [] });
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
    isBrowserWebcamActive = false;
    setWebcamToggleLabel();
    resetTrackingState();
    const url = streamUrlInput ? streamUrlInput.value.trim() : "";
    if (!url) {
      alert("Please enter the ESP32 camera URL (e.g. http://192.168.1.50:81/stream)");
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
        if (data.session) renderSessionBadge(data.session);
        if (data.status === "ok") {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
        } else {
          updateAIStatusUI("failed", "ESP32-CAM (" + url.replace("http://", "") + ")", data.error);
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

function setWebcamToggleLabel() {
  if (!toggleWebcamBtn) return;
  toggleWebcamBtn.textContent = isBrowserWebcamActive ? "Switch to ESP32 Stream" : "Use Browser Webcam";
}

if (toggleWebcamBtn) {
  toggleWebcamBtn.addEventListener("click", async () => {
    isManuallyStopped = false;
    resetTrackingState();
    try { await fetch("/api/tracking/stop", { method: "POST" }); } catch (e) {}
    if (isBrowserWebcamActive) {
      isBrowserWebcamActive = false;
      setWebcamToggleLabel();
      const url = streamUrlInput ? streamUrlInput.value.trim() : "";
      if (!url) {
        updateAIStatusUI("failed", "ESP32-CAM", "Enter the ESP32 stream URL first.");
        if (cameraStatus) {
          cameraStatus.textContent = "NO STREAM URL";
          cameraStatus.style.color = "#f87171";
        }
        return;
      }
      updateAIStatusUI("connecting", "ESP32-CAM (" + url.replace("http://", "") + ")");
      if (cameraStatus) {
        cameraStatus.textContent = "ESP32 STREAM";
        cameraStatus.style.color = "#60a5fa";
      }
      try {
        const res = await fetch("/api/tracking/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: url }),
        });
        const data = await res.json();
        if (data.session) renderSessionBadge(data.session);
        if (data.status === "ok" && cameraStreamImg) {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
          cameraStreamImg.style.display = "block";
          if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
          updateAIStatusUI("running", "ESP32-CAM (" + url.replace("http://", "") + ")");
        } else {
          updateAIStatusUI("failed", "ESP32-CAM (" + url.replace("http://", "") + ")", data.error);
        }
      } catch (e) {
        updateAIStatusUI("failed", "ESP32-CAM");
      }
    } else {
      isBrowserWebcamActive = true;
      setWebcamToggleLabel();
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
        if (data.session) renderSessionBadge(data.session);
        if (data.status === "ok" && cameraStreamImg) {
          cameraStreamImg.src = "/api/video_feed?" + Date.now();
          cameraStreamImg.style.display = "block";
          if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
          updateAIStatusUI("running", "Local Webcam (Device 0)");
        } else {
          isBrowserWebcamActive = false;
          setWebcamToggleLabel();
          updateAIStatusUI("failed", "Local Webcam (Device 0)", data.error);
        }
      } catch (err) {
        isBrowserWebcamActive = false;
        setWebcamToggleLabel();
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
    isBrowserWebcamActive = false;
    setWebcamToggleLabel();
    updateAIStatusUI("idle", "None");
    try {
      const res = await fetch("/api/tracking/stop", { method: "POST" });
      const data = await res.json();
      if (data.session) {
        latestSession = data.session;
        renderSessionBadge(data.session);
      }
      if (data.insight && typeof fetchInsight === "function") fetchInsight();
      const endedMsg =
        data.session && data.session.corrected_label && data.session.reminder_count
          ? data.session.corrected_label
          : "Session ended.";
      showToast(endedMsg, true, [{ label: "Session Report", onClick: () => openSessionReport() }], 8000);
    } catch (e) {}
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

function getActiveCameraElement() {
  const videoReady = browserWebcamVideo && browserWebcamVideo.style.display !== "none" && browserWebcamVideo.videoWidth > 0;
  if (videoReady) return browserWebcamVideo;
  return cameraStreamImg;
}

if (camSnapshotBtn) {
  camSnapshotBtn.addEventListener("click", () => {
    const activeEl = getActiveCameraElement();
    if (!activeEl) return;

    try {
      const canvas = document.createElement("canvas");
      canvas.width = activeEl.videoWidth || activeEl.naturalWidth || 640;
      canvas.height = activeEl.videoHeight || activeEl.naturalHeight || 480;
      if (!canvas.width || !canvas.height) {
        showToast("No camera frame to capture yet.", false);
        return;
      }
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
function showToast(message, isSuccess = true, actions = null, timeoutMs = null) {
  let toast = document.getElementById("pcToastNotification");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "pcToastNotification";
    toast.className = "pc-toast";
    document.body.appendChild(toast);
  }
  toast.innerHTML = "";
  const text = document.createElement("span");
  text.className = "pc-toast-msg";
  text.textContent = message;
  toast.appendChild(text);
  if (actions && actions.length) {
    const row = document.createElement("div");
    row.className = "pc-toast-actions";
    actions.forEach((action) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pc-toast-btn";
      btn.textContent = action.label;
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toast.className = "pc-toast";
        if (typeof action.onClick === "function") action.onClick();
      });
      row.appendChild(btn);
    });
    toast.appendChild(row);
  }
  const kind = isSuccess === "warn" ? "warn" : isSuccess ? "success" : "error";
  toast.className = "pc-toast show " + kind;
  clearTimeout(toast._timeout);
  const holdMs = timeoutMs != null ? timeoutMs : (actions && actions.length ? 14000 : 3500);
  if (holdMs > 0) {
    toast._timeout = setTimeout(() => {
      toast.className = "pc-toast";
    }, holdMs);
  }
}

let lastPendingAdviceId = null;
let lastPendingBreakId = null;

function toastActionLabel(id) {
  if (id === "snooze") return "Snooze 10 min";
  if (id === "dnd") return "DND";
  if (id === "ack") return "Acknowledge";
  if (id === "start_break") return "Start break";
  return id;
}

async function postSessionAction(path, body) {
  const opts = { method: "POST", headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (data.session) renderSessionBadge(data.session);
    return data;
  } catch (err) {
    showToast("Could not update session: " + err.message, false);
    return null;
  }
}

function handleGovernorAction(id) {
  if (id === "snooze") return postSessionAction("/api/session/snooze");
  if (id === "ack") return postSessionAction("/api/session/ack");
  if (id === "dnd") return postSessionAction("/api/session/dnd", { enabled: true });
  if (id === "start_break") {
    return postSessionAction("/api/session/break").then(() => {
      if (typeof focusTimer !== "undefined" && focusTimer.mode === "idle" && typeof startBreak === "function") {
        startBreak();
      }
    });
  }
  return Promise.resolve();
}

function applySessionGovernor(session) {
  if (!session) return;
  renderSessionBadge(session);
  const advice = session.pending_advice;
  // #region agent log
  agentLog("D", "app.js:applySessionGovernor", "governor snapshot on UI", {
    state: session.state,
    severity: session.severity,
    dnd: !!session.dnd,
    voiceEnabled: typeof settings !== "undefined" ? !!settings.voiceEnabled : null,
    flagSet: session.flag_set || [],
    qualified: session.qualified_flags || [],
    adviceId: advice && advice.id,
    adviceSpeak: advice ? !!advice.speak : null,
    adviceKind: advice && advice.kind,
    lastPendingAdviceId,
    skipSameId: !!(advice && advice.id && advice.id === lastPendingAdviceId),
  });
  // #endregion
  if (advice && advice.id && advice.id !== lastPendingAdviceId) {
    lastPendingAdviceId = advice.id;
    const actions = (advice.actions || []).map((id) => ({
      label: toastActionLabel(id),
      onClick: () => handleGovernorAction(id),
    }));
    const kind = advice.kind === "escalate" || advice.kind === "alert" ? "warn" : true;
    const timeoutMs = advice.kind === "escalate" ? 0 : null;
    showToast(advice.toast || advice.spoken_line || advice.summary || "Posture notice.", kind, actions, timeoutMs);
    if (advice.speak && typeof speak === "function") {
      speak(truncateForSpeech(advice.spoken_line || advice.summary || ""));
    }
    if ((advice.kind === "alert" || advice.kind === "escalate") && typeof notifyRiskHigh === "function") {
      notifyRiskHigh({ spoken_line: advice.spoken_line || advice.toast });
    }
    return;
  }
  const brk = session.pending_break;
  if (brk && brk.id && brk.id !== lastPendingBreakId) {
    lastPendingBreakId = brk.id;
    showToast(brk.toast || "Start a break?", true, [
      { label: "Start break", onClick: () => handleGovernorAction("start_break") },
      { label: "Not now", onClick: () => {} },
    ]);
  }
}

if (els.dndChip) {
  els.dndChip.addEventListener("click", () => postSessionAction("/api/session/dnd", { enabled: false }));
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
      showToast("Head pose origin calibrated to (0,0,0).", true);
      if (typeof playTone === "function") playTone(580, 160);
      return true;
    } else {
      showToast("Calibration failed: " + (data.detail || "Could not send the request"), false);
      return false;
    }
  } catch (err) {
    showToast("Connection error: " + err.message, false);
    return false;
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

/* ---------------- Sitting demo threshold ---------------- */

if (els.sittingDemoInput) {
  els.sittingDemoInput.addEventListener("change", async () => {
    const demo_mode = !!els.sittingDemoInput.checked;
    applySittingDemoMode(demo_mode);
    try {
      const res = await fetch("/api/settings/sitting-demo", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ demo_mode }),
      });
      if (!res.ok) {
        showToast("Could not save sitting demo setting", false);
        return;
      }
      const saved = await res.json().catch(() => null);
      if (saved) applySittingDemoMode(!!saved.demo_mode, saved);
    } catch {
      showToast("Could not save sitting demo setting", false);
    }
  });
}

/* ---------------- Session report ---------------- */

function openSessionReport() {
  const sid = (latestSession && latestSession.session_id) || "";
  const url = sid
    ? `/static/report.html?session_id=${encodeURIComponent(sid)}`
    : "/static/report.html";
  window.open(url, "_blank");
}

if (els.sessionReportBtn) {
  els.sessionReportBtn.addEventListener("click", () => openSessionReport());
}

/* ---------------- Guided start overlay (Details only) ---------------- */

function guidedSelectedSource() {
  const checked = document.querySelector('input[name="guidedSource"]:checked');
  return checked ? checked.value : "esp32";
}

async function guidedStartPreview() {
  const sourceKind = guidedSelectedSource();
  const status = document.getElementById("guidedStep2Status");
  const next = document.getElementById("guidedStep2Next");
  const preview = document.getElementById("guidedPreviewImg");
  isManuallyStopped = false;
  let source;
  let label;
  if (sourceKind === "webcam") {
    source = "0";
    label = "Local Webcam (Device 0)";
    isBrowserWebcamActive = true;
  } else {
    const url = (document.getElementById("guidedStreamUrl")?.value || (streamUrlInput && streamUrlInput.value) || "").trim();
    if (!url) {
      if (status) status.textContent = "Enter the ESP32 stream URL first.";
      return;
    }
    if (streamUrlInput) streamUrlInput.value = url;
    source = url;
    label = "ESP32-CAM (" + url.replace("http://", "") + ")";
    isBrowserWebcamActive = false;
  }
  updateAIStatusUI("connecting", label);
  if (status) status.textContent = "Starting tracking...";
  try {
    await fetch("/api/tracking/stop", { method: "POST" });
    const res = await fetch("/api/tracking/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const data = await res.json();
    if (data.session) {
      latestSession = data.session;
      renderSessionBadge(data.session);
    }
    if (data.status === "ok") {
      const feed = "/api/video_feed?" + Date.now();
      if (cameraStreamImg) {
        cameraStreamImg.src = feed;
        cameraStreamImg.style.display = "block";
      }
      if (preview) preview.src = feed;
      if (camFallbackOverlay) camFallbackOverlay.style.display = "none";
      if (cameraStatus) {
        cameraStatus.textContent = sourceKind === "webcam" ? "LOCAL WEBCAM (DEVICE 0)" : "ESP32 STREAM";
        cameraStatus.style.color = sourceKind === "webcam" ? "#34d399" : "#60a5fa";
      }
      updateAIStatusUI("running", label);
      if (status) status.textContent = "Preview live — sit straight, then continue.";
      if (next) next.disabled = false;
    } else {
      const errMsg = data.error || "Could not start tracking. Check the camera source.";
      updateAIStatusUI("failed", label, data.error);
      if (status) status.textContent = errMsg;
    }
  } catch (err) {
    updateAIStatusUI("failed", label);
    if (status) status.textContent = "Connection error: " + err.message;
  }
}

const guidedSkipBtn = document.getElementById("guidedSkipBtn");
if (guidedSkipBtn) {
  guidedSkipBtn.addEventListener("click", () => dismissGuidedOverlay(latestSession));
}

const guidedStep1Btn = document.getElementById("guidedStep1Btn");
if (guidedStep1Btn) {
  guidedStep1Btn.addEventListener("click", () => {
    const urlEl = document.getElementById("guidedStreamUrl");
    if (guidedSelectedSource() === "esp32" && urlEl && streamUrlInput) {
      streamUrlInput.value = urlEl.value;
    }
    setGuidedStep(2);
    guidedStartPreview();
  });
}

const guidedStep2Btn = document.getElementById("guidedStep2Btn");
if (guidedStep2Btn) {
  guidedStep2Btn.addEventListener("click", () => guidedStartPreview());
}

const guidedStep2Next = document.getElementById("guidedStep2Next");
if (guidedStep2Next) {
  guidedStep2Next.addEventListener("click", () => setGuidedStep(3));
}

const guidedCalibrateBtn = document.getElementById("guidedCalibrateBtn");
if (guidedCalibrateBtn) {
  guidedCalibrateBtn.addEventListener("click", async () => {
    const status = document.getElementById("guidedCalibStatus");
    const next = document.getElementById("guidedStep3Next");
    const ok = await triggerCalibrateHeadPose(guidedCalibrateBtn);
    if (ok) {
      wizardDidCalibrate = true;
      if (status) status.textContent = "Calibrated. Continue when ready.";
      if (next) next.disabled = false;
    } else if (status) {
      status.textContent = "Calibration did not complete. Sit straight and try again.";
    }
  });
}

const guidedStep3Next = document.getElementById("guidedStep3Next");
if (guidedStep3Next) {
  guidedStep3Next.addEventListener("click", () => {
    if (!wizardDidCalibrate) return;
    setGuidedStep(4);
  });
}

const guidedReadyBtn = document.getElementById("guidedReadyBtn");
if (guidedReadyBtn) {
  guidedReadyBtn.addEventListener("click", async () => {
    const voiceCb = document.getElementById("guidedVoiceEnabled");
    if (typeof settings !== "undefined" && voiceCb) {
      settings.voiceEnabled = !!voiceCb.checked;
      if (typeof saveSettings === "function") saveSettings();
      const voiceInput = document.getElementById("voiceEnabledInput");
      if (voiceInput) voiceInput.checked = settings.voiceEnabled;
    }
    try {
      if (typeof speak === "function") speak("Monitoring started.");
    } catch {
      // TTS is optional — entering the dashboard must still proceed.
    }
    try {
      const res = await fetch("/api/session/ready", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || "Could not enter dashboard", false);
        return;
      }
      applySnapshot(data);
      dismissGuidedOverlay(data.session || latestSession);
    } catch (err) {
      showToast("Could not enter dashboard: " + err.message, false);
    }
  });
}

const guidedSourceRadios = document.querySelectorAll('input[name="guidedSource"]');
guidedSourceRadios.forEach((radio) => {
  radio.addEventListener("change", () => {
    const urlEl = document.getElementById("guidedStreamUrl");
    if (urlEl) urlEl.disabled = guidedSelectedSource() === "webcam";
  });
});
