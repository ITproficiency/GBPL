/* Focus mode: Pomodoro timer + settings (ambient sound, volumes, background
 * theme) + task list. Self-contained — everything persists in localStorage,
 * no backend changes needed. Loaded before app.js so its globals
 * (focusTimer, notifyRiskHigh, applyBackendDefaultFocusMinutes) exist by the
 * time app.js's first poll cycle runs.
 */

const DEFAULT_SETTINGS = {
  focusMinutes: 25,
  breakMinutes: 5,
  ambientSound: "none", // none | white | pink | brown | rain | ocean | wind
  ambientVolume: 0.5,
  alarmVolume: 0.6,
  voiceEnabled: false,
  voiceVolume: 0.8,
  youtubeUrl: "",
  youtubeVolume: 0.5,
  theme: "slate", // slate | midnight | forest | sunset | ocean
};

function safeLocalStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private browsing / storage disabled — settings just won't persist.
  }
}

function loadSettings() {
  const raw = safeLocalStorageGet("pc_settings");
  if (!raw) return { ...DEFAULT_SETTINGS };
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings() {
  safeLocalStorageSet("pc_settings", JSON.stringify(settings));
}

function loadTasks() {
  try {
    return JSON.parse(safeLocalStorageGet("pc_tasks") || "[]");
  } catch {
    return [];
  }
}

function saveTasks() {
  safeLocalStorageSet("pc_tasks", JSON.stringify(tasks));
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

const hadSavedSettings = safeLocalStorageGet("pc_settings") !== null;
const settings = loadSettings();
let tasks = loadTasks();
let activeTaskId = safeLocalStorageGet("pc_active_task_id") || null;

const focusEls = {
  settingsBtn: document.getElementById("settingsBtn"),
  settingsOverlay: document.getElementById("settingsOverlay"),
  settingsCloseBtn: document.getElementById("settingsCloseBtn"),
  focusMinutesInput: document.getElementById("focusMinutesInput"),
  focusMinutesValue: document.getElementById("focusMinutesValue"),
  breakMinutesInput: document.getElementById("breakMinutesInput"),
  breakMinutesValue: document.getElementById("breakMinutesValue"),
  ambientVolumeInput: document.getElementById("ambientVolumeInput"),
  alarmVolumeInput: document.getElementById("alarmVolumeInput"),
  soundOptions: document.getElementById("soundOptions"),
  youtubeUrlInput: document.getElementById("youtubeUrlInput"),
  youtubePlayBtn: document.getElementById("youtubePlayBtn"),
  youtubeVolumeInput: document.getElementById("youtubeVolumeInput"),
  youtubeStatus: document.getElementById("youtubeStatus"),
  voiceEnabledInput: document.getElementById("voiceEnabledInput"),
  voiceVolumeInput: document.getElementById("voiceVolumeInput"),
  analyzeCooldownInput: document.getElementById("analyzeCooldownInput"),
  analyzeCooldownValue: document.getElementById("analyzeCooldownValue"),
  insightCooldownInput: document.getElementById("insightCooldownInput"),
  insightCooldownValue: document.getElementById("insightCooldownValue"),
  cooldownStatus: document.getElementById("cooldownStatus"),
  themeOptions: document.getElementById("themeOptions"),
  focusModeBadge: document.getElementById("focusModeBadge"),
  focusRingProgress: document.getElementById("focusRingProgress"),
  focusTime: document.getElementById("focusTime"),
  focusStartBtn: document.getElementById("focusStartBtn"),
  focusPauseBtn: document.getElementById("focusPauseBtn"),
  focusResetBtn: document.getElementById("focusResetBtn"),
  focusSessionCount: document.getElementById("focusSessionCount"),
  focusActiveTask: document.getElementById("focusActiveTask"),
  taskForm: document.getElementById("taskForm"),
  taskInput: document.getElementById("taskInput"),
  taskList: document.getElementById("taskList"),
};

/* ---------------- Background theme ---------------- */

function applyTheme() {
  document.documentElement.setAttribute("data-focus-theme", settings.theme);
}

function highlightThemeButton() {
  focusEls.themeOptions.querySelectorAll(".theme-swatch").forEach((btn) => {
    btn.classList.toggle("selected", btn.dataset.theme === settings.theme);
  });
}

focusEls.themeOptions.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-theme]");
  if (!btn) return;
  settings.theme = btn.dataset.theme;
  applyTheme();
  highlightThemeButton();
  saveSettings();
});

/* ---------------- Ambient sound (real audio files, discovered dynamically) ----------------
 * Files live in backend/static/sounds/ — GET /api/sounds lists whatever is
 * actually there by filename, so adding a new option later is just dropping
 * an mp3 in that folder, no code change needed. (Procedurally-generated noise
 * was tried first but sounded harsh/painful — real recordings instead now.)
 */

let audioCtx = null; // still used by playTone() for alarm beeps, below

function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

let ambientAudio = null;
let availableSounds = []; // [{id, label, file}], populated from /api/sounds

function stopAmbient() {
  if (!ambientAudio) return;
  ambientAudio.pause();
  ambientAudio.src = "";
  ambientAudio = null;
}

function startAmbient(kind) {
  stopAmbient();
  if (kind === "none") return;

  const sound = availableSounds.find((s) => s.id === kind);
  if (!sound) return;
  stopYoutube(); // one music/ambience source at a time, avoid overlapping audio

  ambientAudio = new Audio(`/static/sounds/${sound.file}`);
  ambientAudio.loop = true;
  ambientAudio.volume = settings.ambientVolume;
  ambientAudio.play().catch(() => {
    // Autoplay blocked — shouldn't happen since this always runs from a
    // click handler, but never let a rejected promise surface as an error.
  });
}

function setAmbientVolume(v) {
  settings.ambientVolume = v;
  if (ambientAudio) ambientAudio.volume = v;
  saveSettings();
}

function renderSoundOptions() {
  const optionsHtml = [`<button type="button" data-sound="none" class="sound-btn">None</button>`].concat(
    availableSounds.map(
      (s) => `<button type="button" data-sound="${s.id}" class="sound-btn">${escapeHtml(s.label)}</button>`
    )
  );
  focusEls.soundOptions.innerHTML = optionsHtml.join("");
  highlightSoundButton();
}

async function loadAvailableSounds() {
  try {
    const res = await fetch("/api/sounds");
    if (res.ok) {
      const data = await res.json();
      availableSounds = data.sounds || [];
    }
  } catch {
    availableSounds = [];
  }
  renderSoundOptions();
}

function highlightSoundButton() {
  focusEls.soundOptions.querySelectorAll(".sound-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.sound === settings.ambientSound);
  });
}

focusEls.soundOptions.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-sound]");
  if (!btn) return;
  settings.ambientSound = btn.dataset.sound;
  highlightSoundButton();
  startAmbient(settings.ambientSound);
  saveSettings();
});

/* ---------------- Custom music (YouTube link) ----------------
 * Plays via the YouTube IFrame Player API in a 1x1 off-screen iframe (see
 * #youtubePlayerContainer / .yt-hidden in index.html) — actual audio, not
 * procedural, for users who want their own playlist instead of noise.
 * Volume is controlled through the YT Player API (setVolume), not our Web
 * Audio graph, since a cross-origin iframe's audio isn't reachable from it.
 */

let ytPlayer = null;
let ytApiLoadPromise = null;
let ytPlaying = false;

function extractYoutubeId(input) {
  const trimmed = (input || "").trim();
  const match = trimmed.match(
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([\w-]{11})/
  );
  if (match) return match[1];
  return /^[\w-]{11}$/.test(trimmed) ? trimmed : null;
}

function loadYoutubeApi() {
  if (window.YT && window.YT.Player) return Promise.resolve();
  if (ytApiLoadPromise) return ytApiLoadPromise;
  ytApiLoadPromise = new Promise((resolve) => {
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady = resolve;
  });
  return ytApiLoadPromise;
}

async function playYoutubeUrl(url) {
  const videoId = extractYoutubeId(url);
  if (!videoId) {
    focusEls.youtubeStatus.textContent = "Couldn't read a video ID from that link";
    return;
  }

  // One audio source at a time — turn off procedural ambience first.
  stopAmbient();
  settings.ambientSound = "none";
  highlightSoundButton();

  focusEls.youtubeStatus.textContent = "Loading...";
  await loadYoutubeApi();

  const volumePct = Math.round(settings.youtubeVolume * 100);
  if (!ytPlayer) {
    ytPlayer = new YT.Player("youtubePlayerContainer", {
      height: "1",
      width: "1",
      videoId,
      playerVars: { autoplay: 1, loop: 1, playlist: videoId, controls: 0 },
      events: {
        onReady: (e) => {
          e.target.setVolume(volumePct);
          e.target.playVideo();
        },
        onStateChange: (e) => {
          if (e.data === YT.PlayerState.PLAYING) {
            ytPlaying = true;
            focusEls.youtubePlayBtn.textContent = "Stop";
            focusEls.youtubeStatus.textContent = "Playing";
          }
        },
        onError: () => {
          focusEls.youtubeStatus.textContent = "Couldn't play that video (invalid or restricted)";
        },
      },
    });
  } else {
    ytPlayer.loadVideoById(videoId);
    ytPlayer.setVolume(volumePct);
  }

  settings.youtubeUrl = url;
  saveSettings();
}

function stopYoutube() {
  if (ytPlayer && typeof ytPlayer.stopVideo === "function") {
    ytPlayer.stopVideo();
  }
  ytPlaying = false;
  focusEls.youtubePlayBtn.textContent = "Play";
  focusEls.youtubeStatus.textContent = "";
}

focusEls.youtubePlayBtn.addEventListener("click", () => {
  if (ytPlaying) {
    stopYoutube();
  } else {
    playYoutubeUrl(focusEls.youtubeUrlInput.value);
  }
});

focusEls.youtubeVolumeInput.addEventListener("input", () => {
  settings.youtubeVolume = Number(focusEls.youtubeVolumeInput.value) / 100;
  if (ytPlayer && typeof ytPlayer.setVolume === "function") {
    ytPlayer.setVolume(Math.round(settings.youtubeVolume * 100));
  }
  saveSettings();
});

/* ---------------- Alarm tones ---------------- */

function playTone(freq, durationMs) {
  if (settings.alarmVolume <= 0) return;
  try {
    const ctx = getAudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq;
    osc.type = "sine";
    const peak = 0.2 * settings.alarmVolume;
    gain.gain.setValueAtTime(peak, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + durationMs / 1000);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + durationMs / 1000);
  } catch {
    // Audio is a nice-to-have; never let it break the timer.
  }
}

/* ---------------- Voice reminders (Web Speech API — no external TTS service) ---------------- */

function truncateForSpeech(text, maxChars = 220) {
  if (!text || text.length <= maxChars) return text || "";
  const cut = text.slice(0, maxChars);
  const lastPeriod = cut.lastIndexOf(". ");
  return (lastPeriod > 40 ? cut.slice(0, lastPeriod + 1) : cut) + "...";
}

function speak(text) {
  if (!settings.voiceEnabled || !("speechSynthesis" in window) || !text) return;
  if (window.speechSynthesis.speaking) return; // drop — do not cancel a line in progress
  try {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "en-US";
    const vol = Number(settings.voiceVolume);
    utter.volume = Number.isFinite(vol) ? Math.min(1, Math.max(0, vol)) : 1;
    window.speechSynthesis.speak(utter);
  } catch {
    // TTS is optional; never block monitoring or dashboard entry.
  }
}

focusEls.voiceEnabledInput.addEventListener("change", () => {
  settings.voiceEnabled = focusEls.voiceEnabledInput.checked;
  saveSettings();
  if (settings.voiceEnabled) {
    // The toggle click is the user gesture some browsers require before
    // speechSynthesis will play — also doubles as an audible confirmation.
    speak("Voice reminders enabled.");
  }
});

focusEls.voiceVolumeInput.addEventListener("input", () => {
  settings.voiceVolume = Number(focusEls.voiceVolumeInput.value) / 100;
  saveSettings();
});

/* ---------------- Notifications ---------------- */

let notificationsEnabled = false;
const RISK_ALERT_COOLDOWN_MS = 60000;
let lastRiskAlertAt = 0;

function requestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().then((perm) => {
      notificationsEnabled = perm === "granted";
    });
  } else {
    notificationsEnabled = Notification.permission === "granted";
  }
}

function notify(title, body) {
  if (notificationsEnabled && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

function notifyRiskHigh(data) {
  const now = Date.now();
  if (now - lastRiskAlertAt < RISK_ALERT_COOLDOWN_MS) return;
  lastRiskAlertAt = now;
  const message = data.spoken_line || data.toast || "Posture needs attention.";
  playTone(220, 250);
  notify("Posture alert", message);
}

/* ---------------- Tasks ---------------- */

function setActiveTask(id) {
  activeTaskId = id;
  safeLocalStorageSet("pc_active_task_id", id || "");
  renderTasks();
  updateActiveTaskLabel();
}

function updateActiveTaskLabel() {
  const task = tasks.find((t) => t.id === activeTaskId);
  focusEls.focusActiveTask.textContent = task ? `Working on: ${task.text}` : "No task selected";
}

function renderTasks() {
  if (tasks.length === 0) {
    focusEls.taskList.innerHTML = '<li class="empty">No tasks yet — add one to track pomodoros.</li>';
    return;
  }
  focusEls.taskList.innerHTML = tasks
    .map(
      (t) => `
    <li class="task-item ${t.id === activeTaskId ? "active" : ""} ${t.done ? "done" : ""}" data-id="${t.id}">
      <label class="task-check">
        <input type="checkbox" data-action="toggle" ${t.done ? "checked" : ""} />
        <span class="task-text">${escapeHtml(t.text)}</span>
      </label>
      <span class="task-pomo" title="Completed focus sessions">🍅 ${t.pomodoros}</span>
      <button type="button" class="task-select" data-action="select"
        title="${t.id === activeTaskId ? "Already the current task" : "Set as current task"}"
        ${t.id === activeTaskId ? "disabled" : ""}>▶</button>
      <button type="button" class="task-delete" data-action="delete" title="Delete task">✕</button>
    </li>`
    )
    .join("");
}

focusEls.taskForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = focusEls.taskInput.value.trim();
  if (!text) return;
  const task = { id: crypto.randomUUID(), text, done: false, pomodoros: 0 };
  tasks.push(task);
  if (!activeTaskId) setActiveTask(task.id);
  focusEls.taskInput.value = "";
  saveTasks();
  renderTasks();
});

focusEls.taskList.addEventListener("click", (e) => {
  const actionEl = e.target.closest("[data-action]");
  const li = e.target.closest("[data-id]");
  if (!actionEl || !li) return;
  const task = tasks.find((t) => t.id === li.dataset.id);
  if (!task) return;

  if (actionEl.dataset.action === "toggle") {
    task.done = !task.done;
  } else if (actionEl.dataset.action === "delete") {
    tasks = tasks.filter((t) => t.id !== task.id);
    if (activeTaskId === task.id) setActiveTask(null);
  } else if (actionEl.dataset.action === "select") {
    setActiveTask(task.id);
  }
  saveTasks();
  renderTasks();
});

function incrementActiveTaskPomodoro() {
  const task = tasks.find((t) => t.id === activeTaskId);
  if (!task) return;
  task.pomodoros += 1;
  saveTasks();
  renderTasks();
}

/* ---------------- Focus timer ---------------- */

const FOCUS_RING_CIRCUMFERENCE = 2 * Math.PI * 90;
focusEls.focusRingProgress.style.strokeDasharray = `${FOCUS_RING_CIRCUMFERENCE}`;

const focusTimer = {
  mode: "idle", // idle | focus | break
  remainingSec: 0,
  totalSec: 0,
  timerId: null,
  sessionsCompleted: 0,
};

/* ---------------- AI rate limits (server-enforced, editable here) ---------------- */

function applyCooldownSettings(analyzeCooldownSec, insightCooldownSec) {
  if (analyzeCooldownSec != null) {
    focusEls.analyzeCooldownInput.value = analyzeCooldownSec;
    focusEls.analyzeCooldownValue.textContent = analyzeCooldownSec;
  }
  if (insightCooldownSec != null) {
    focusEls.insightCooldownInput.value = insightCooldownSec;
    focusEls.insightCooldownValue.textContent = insightCooldownSec;
  }
}

async function saveCooldownSettings() {
  const analyze_cooldown_sec = Number(focusEls.analyzeCooldownInput.value);
  const insight_cooldown_sec = Number(focusEls.insightCooldownInput.value);
  focusEls.cooldownStatus.textContent = "Saving...";
  try {
    const res = await fetch("/api/settings/cooldowns", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ analyze_cooldown_sec, insight_cooldown_sec }),
    });
    focusEls.cooldownStatus.textContent = res.ok ? "Saved" : "Couldn't save";
  } catch {
    focusEls.cooldownStatus.textContent = "Connection error";
  }
}

focusEls.analyzeCooldownInput.addEventListener("input", () => {
  focusEls.analyzeCooldownValue.textContent = focusEls.analyzeCooldownInput.value;
});
focusEls.analyzeCooldownInput.addEventListener("change", saveCooldownSettings);

focusEls.insightCooldownInput.addEventListener("input", () => {
  focusEls.insightCooldownValue.textContent = focusEls.insightCooldownInput.value;
});
focusEls.insightCooldownInput.addEventListener("change", saveCooldownSettings);

// Only auto-adopt the backend's insight window as the focus length the
// first time — once the user has ever saved settings, their choice wins.
function applyBackendDefaultFocusMinutes(minutes) {
  if (!hadSavedSettings) {
    settings.focusMinutes = minutes;
    if (focusEls.focusMinutesInput) {
      focusEls.focusMinutesInput.value = minutes;
      focusEls.focusMinutesValue.textContent = minutes;
    }
    if (focusTimer.mode === "idle") updateFocusDisplay();
  }
}

function formatMMSS(totalSeconds) {
  const m = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const s = Math.floor(totalSeconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function updateFocusDisplay() {
  focusEls.focusTime.textContent =
    focusTimer.mode === "idle" ? formatMMSS(settings.focusMinutes * 60) : formatMMSS(focusTimer.remainingSec);
  const fraction = focusTimer.totalSec ? focusTimer.remainingSec / focusTimer.totalSec : 1;
  focusEls.focusRingProgress.style.strokeDashoffset = FOCUS_RING_CIRCUMFERENCE * (1 - fraction);
  focusEls.focusModeBadge.textContent =
    focusTimer.mode === "focus" ? "FOCUS" : focusTimer.mode === "break" ? "BREAK" : "READY";
  focusEls.focusModeBadge.className = "focus-mode-badge" + (focusTimer.mode === "break" ? " mode-break" : "");
  focusEls.focusSessionCount.textContent = `Sessions completed: ${focusTimer.sessionsCompleted}`;
}

function setFocusButtons(state) {
  if (state === "idle") {
    focusEls.focusStartBtn.disabled = false;
    focusEls.focusPauseBtn.disabled = true;
    focusEls.focusPauseBtn.textContent = "Pause";
    focusEls.focusResetBtn.disabled = true;
  } else if (state === "running") {
    focusEls.focusStartBtn.disabled = true;
    focusEls.focusPauseBtn.disabled = false;
    focusEls.focusPauseBtn.textContent = "Pause";
    focusEls.focusResetBtn.disabled = false;
  } else if (state === "paused") {
    focusEls.focusPauseBtn.textContent = "Resume";
  }
}

function tickFocus() {
  focusTimer.remainingSec -= 1;
  if (focusTimer.remainingSec <= 0) {
    if (focusTimer.mode === "focus") onFocusComplete();
    else onBreakComplete();
    return;
  }
  updateFocusDisplay();
}

async function onFocusComplete() {
  clearInterval(focusTimer.timerId);
  focusTimer.sessionsCompleted += 1;
  incrementActiveTaskPomodoro();
  playTone(880, 300);
  setTimeout(() => playTone(1046, 300), 350);
  notify("Focus session complete", "Time for a break.");
  startBreak();
}

function onBreakComplete() {
  clearInterval(focusTimer.timerId);
  playTone(660, 400);
  notify("Break over", "Ready for another focus session?");
  syncSessionBreak(false);
  focusTimer.mode = "idle";
  focusTimer.remainingSec = 0;
  focusTimer.totalSec = 0;
  updateFocusDisplay();
  setFocusButtons("idle");
}

async function syncSessionBreak(enter) {
  try {
    await fetch(enter ? "/api/session/break" : "/api/session/break/end", { method: "POST" });
  } catch {
    // Session sync is best-effort; the timer still runs locally.
  }
}

function startFocus() {
  requestNotificationPermission();
  if (settings.ambientSound !== "none") startAmbient(settings.ambientSound);
  syncSessionBreak(false);
  focusTimer.mode = "focus";
  focusTimer.totalSec = settings.focusMinutes * 60;
  focusTimer.remainingSec = focusTimer.totalSec;
  updateFocusDisplay();
  setFocusButtons("running");
  focusTimer.timerId = setInterval(tickFocus, 1000);
}

function startBreak() {
  syncSessionBreak(true);
  focusTimer.mode = "break";
  focusTimer.totalSec = settings.breakMinutes * 60;
  focusTimer.remainingSec = focusTimer.totalSec;
  updateFocusDisplay();
  setFocusButtons("running");
  focusTimer.timerId = setInterval(tickFocus, 1000);
}

function resetFocus() {
  if (focusTimer.mode === "break") syncSessionBreak(false);
  clearInterval(focusTimer.timerId);
  focusTimer.mode = "idle";
  focusTimer.remainingSec = 0;
  focusTimer.totalSec = 0;
  updateFocusDisplay();
  setFocusButtons("idle");
}

focusEls.focusStartBtn.addEventListener("click", startFocus);
focusEls.focusResetBtn.addEventListener("click", resetFocus);
focusEls.focusPauseBtn.addEventListener("click", () => {
  if (focusEls.focusPauseBtn.textContent === "Pause") {
    clearInterval(focusTimer.timerId);
    setFocusButtons("paused");
  } else {
    focusTimer.timerId = setInterval(tickFocus, 1000);
    setFocusButtons("running");
  }
});

/* ---------------- Settings panel wiring ---------------- */

focusEls.settingsBtn.addEventListener("click", () => {
  focusEls.settingsOverlay.classList.add("open");
});
focusEls.settingsCloseBtn.addEventListener("click", () => {
  focusEls.settingsOverlay.classList.remove("open");
});
focusEls.settingsOverlay.addEventListener("click", (e) => {
  if (e.target === focusEls.settingsOverlay) focusEls.settingsOverlay.classList.remove("open");
});

focusEls.focusMinutesInput.addEventListener("input", () => {
  settings.focusMinutes = Number(focusEls.focusMinutesInput.value);
  focusEls.focusMinutesValue.textContent = settings.focusMinutes;
  saveSettings();
  if (focusTimer.mode === "idle") updateFocusDisplay();
});

focusEls.breakMinutesInput.addEventListener("input", () => {
  settings.breakMinutes = Number(focusEls.breakMinutesInput.value);
  focusEls.breakMinutesValue.textContent = settings.breakMinutes;
  saveSettings();
});

focusEls.ambientVolumeInput.addEventListener("input", () => {
  setAmbientVolume(Number(focusEls.ambientVolumeInput.value) / 100);
});

focusEls.alarmVolumeInput.addEventListener("input", () => {
  settings.alarmVolume = Number(focusEls.alarmVolumeInput.value) / 100;
  saveSettings();
});

/* ---------------- Web Audio Ambient Noise Synthesizer ---------------- */
let synthNoiseNode = null;
let synthGainNode = null;
let synthFilterNode = null;

function stopSynthAmbient() {
  if (synthGainNode) {
    try {
      const ctx = getAudioContext();
      synthGainNode.gain.linearRampToValueAtTime(0.001, ctx.currentTime + 0.3);
      setTimeout(() => {
        if (synthNoiseNode) {
          synthNoiseNode.stop();
          synthNoiseNode.disconnect();
          synthNoiseNode = null;
        }
      }, 350);
    } catch {
      // Ignore cleanup error
    }
  }
}

function playSynthesizedAmbient(kind, volume = 0.5) {
  stopSynthAmbient();
  stopAmbient();
  if (kind === "none") return;

  try {
    const ctx = getAudioContext();
    const bufferSize = 2 * ctx.sampleRate;
    const noiseBuffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const output = noiseBuffer.getChannelData(0);

    let lastOut = 0.0;
    for (let i = 0; i < bufferSize; i++) {
      const white = Math.random() * 2 - 1;
      if (kind === "ocean" || kind === "rain") {
        lastOut = (lastOut + 0.02 * white) / 1.02;
        output[i] = lastOut * 3.5;
      } else {
        output[i] = white * 0.15;
      }
    }

    synthNoiseNode = ctx.createBufferSource();
    synthNoiseNode.buffer = noiseBuffer;
    synthNoiseNode.loop = true;

    synthFilterNode = ctx.createBiquadFilter();
    if (kind === "rain") {
      synthFilterNode.type = "lowpass";
      synthFilterNode.frequency.value = 1200;
    } else if (kind === "ocean") {
      synthFilterNode.type = "bandpass";
      synthFilterNode.frequency.value = 400;
      synthFilterNode.Q.value = 1.2;
    } else if (kind === "breeze") {
      synthFilterNode.type = "lowpass";
      synthFilterNode.frequency.value = 600;
    } else {
      synthFilterNode.type = "lowpass";
      synthFilterNode.frequency.value = 3200;
    }

    synthGainNode = ctx.createGain();
    synthGainNode.gain.setValueAtTime(volume * 0.3, ctx.currentTime);

    synthNoiseNode.connect(synthFilterNode);
    synthFilterNode.connect(synthGainNode);
    synthGainNode.connect(ctx.destination);

    synthNoiseNode.start();
  } catch (err) {
    console.warn("Web Audio ambient synthesis error:", err);
  }
}

/* ---------------- Focus Presets Controller ---------------- */
const focusPresetBar = document.getElementById("focusPresetBar");
if (focusPresetBar) {
  focusPresetBar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mins]");
    if (!btn) return;
    const mins = Number(btn.dataset.mins);
    if (!mins) return;

    focusPresetBar.querySelectorAll(".preset-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    if (mins === 5 || mins === 15) {
      settings.breakMinutes = mins;
    } else {
      settings.focusMinutes = mins;
      if (focusEls.focusMinutesInput) focusEls.focusMinutesInput.value = mins;
      if (focusEls.focusMinutesValue) focusEls.focusMinutesValue.textContent = mins;
    }
    saveSettings();

    if (focusTimer.mode === "idle") {
      focusTimer.totalSeconds = mins * 60;
      focusTimer.remainingSeconds = focusTimer.totalSeconds;
      updateFocusDisplay();
    }
  });
}

/* ---------------- Ambient Sound Chips & Volume Slider ---------------- */
const soundChipGrid = document.getElementById("soundChipGrid");
const synthVolumeSlider = document.getElementById("synthVolumeSlider");

if (soundChipGrid) {
  soundChipGrid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-sound]");
    if (!btn) return;
    const kind = btn.dataset.sound;

    soundChipGrid.querySelectorAll(".sound-chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    const vol = synthVolumeSlider ? Number(synthVolumeSlider.value) : 0.5;
    playSynthesizedAmbient(kind, vol);
  });
}

if (synthVolumeSlider) {
  synthVolumeSlider.addEventListener("input", () => {
    const vol = Number(synthVolumeSlider.value);
    if (synthGainNode) {
      try {
        synthGainNode.gain.setValueAtTime(vol * 0.3, getAudioContext().currentTime);
      } catch {}
    }
  });
}

/* ---------------- Init ---------------- */

function initFocusUI() {
  focusEls.focusMinutesInput.value = settings.focusMinutes;
  focusEls.focusMinutesValue.textContent = settings.focusMinutes;
  focusEls.breakMinutesInput.value = settings.breakMinutes;
  focusEls.breakMinutesValue.textContent = settings.breakMinutes;
  focusEls.ambientVolumeInput.value = Math.round(settings.ambientVolume * 100);
  focusEls.alarmVolumeInput.value = Math.round(settings.alarmVolume * 100);
  focusEls.voiceEnabledInput.checked = settings.voiceEnabled;
  focusEls.voiceVolumeInput.value = Math.round(settings.voiceVolume * 100);
  focusEls.youtubeUrlInput.value = settings.youtubeUrl || "";
  focusEls.youtubeVolumeInput.value = Math.round(settings.youtubeVolume * 100);
  loadAvailableSounds();
  highlightThemeButton();
  applyTheme();
  updateFocusDisplay();
  setFocusButtons("idle");
  renderTasks();
  updateActiveTaskLabel();
}

initFocusUI();
