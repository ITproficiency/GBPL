# Tracking AI - Eye Blink Counter & Head Pose Estimator

Real-time computer vision module for tracking user blinks, Eye Aspect Ratio (EAR), head pose orientation (Pitch, Roll, Yaw), and eye-to-screen distance using MediaPipe FaceMesh and OpenCV. Supports standard webcams and ESP32-CAM MJPEG streams.

## 🚀 Features

- **`blink_counter.py`**: Lightweight, CPU-optimized blink detector and head pose estimator with live Firebase sync.
- **`blink_counter_and_EAR_plot.py`**: Comprehensive tracking system featuring real-time EAR graph visualization, 3D head axes rotation (solvePnP), IPD distance estimation, ergonomic posture warning triggers, and bi-directional calibration sync with Firebase Realtime Database.
- **`FaceMeshModule.py`**: MediaPipe 468-landmark Face Mesh extraction wrapper.
- **`utils.py`**: Drawing utilities (text overlays, rounded rectangles) and threaded video stream reader for smooth frame rates.

## 🛠 Prerequisites & Installation

Python **3.10–3.12** (3.11 recommended). MediaPipe does not support Python 3.13.

Prefer the **repo-root** launcher, which creates `tracking_AI/.venv` and does not use a global `pip install`:

```bash
./run.sh          # macOS / Linux (from repo root)
.\run.ps1         # Windows
```

To install into that venv only:

```bash
tracking_AI/.venv/bin/python -m pip install -r tracking_AI/requirements.txt   # macOS / Linux
tracking_AI\.venv\Scripts\python.exe -m pip install -r tracking_AI\requirements.txt  # Windows
```

Dependencies (see `requirements.txt` for pins):
- `opencv-python`
- `mediapipe`
- `numpy`
- `matplotlib`

### macOS Camera permission

Grant **Camera** to the parent app (Terminal / iTerm / VS Code / Cursor) under System Settings → Privacy & Security → Camera. If access is denied, tracking stays up with a connecting placeholder and does not exit.

## 💻 Usage

### 1. Simple Blink Counter
```bash
tracking_AI/.venv/bin/python blink_counter.py
```

### 2. Blink Counter with Real-time EAR Plot & Head Pose
```bash
tracking_AI/.venv/bin/python blink_counter_and_EAR_plot.py
```

> **Note**: To configure the video input source (local webcam `0` or ESP32-CAM stream URL like `http://192.168.1.50:81/stream`), edit the `input_video_path` variable at the bottom of the script. Root `./run.sh` / `.\run.ps1` pass the camera argument for you.

## 📡 Firebase Data Schema

The tracking module publishes state updates to Firebase RTDB node `/ai_data`:
```json
{
  "pitch": 0.0,
  "roll": 0.0,
  "yaw": 0.0,
  "distance_cm": 55.0,
  "ear": 0.32,
  "blinks": 24,
  "warnings": ["Sitting too close to screen (38.0 cm < 40 cm)"],
  "posture_status": "GOOD",
  "nose_x": 0.5,
  "nose_y": 0.55,
  "timestamp": 1740900000.0
}
```
