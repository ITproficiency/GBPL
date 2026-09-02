# PostureCare - Smart Ergonomic Health & Posture Monitoring System

An end-to-end intelligent IoT & AI workspace monitoring system designed to protect user ergonomics, prevent eye strain, and encourage healthy sitting habits. The system integrates **ESP32-S3 hardware sensors & camera streaming**, **MediaPipe computer vision AI for face & blink tracking**, **Firebase Realtime Database**, and a **FastAPI Web Dashboard with LLM health insights**.

---

## 📐 System Architecture

```mermaid
flowchart TD
    subgraph Hardware ["ESP32-S3 IoT Node"]
        CAM["OV2640 / OV3660 Camera"]
        LDR["LDR Light Sensor (GPIO 1)"]
        US["HC-SR04 Ultrasonic (Trig: 21, Echo: 14)"]
        LEDS["Status LEDs (GPIO 45, 47)"]
        HTTP_STREAM["MJPEG Stream Server (:81/stream)"]
    end

    subgraph Firebase ["Firebase Realtime Database (Cloud)"]
        RTDB_SENSOR["/sensor_data<br/>(light_adc, lux, distance)"]
        RTDB_AI["/ai_data<br/>(pitch, roll, yaw, ear, blinks, warnings)"]
        RTDB_ADVICE["/advice & /insights"]
    end

    subgraph AI_Module ["Computer Vision & AI Tracking"]
        CV_TRACK["blink_counter_and_EAR_plot.py<br/>MediaPipe FaceMesh (468 landmarks)"]
        HEAD_POSE["Head Pose Estimator (solvePnP)"]
        BLINK_DET["Eye Aspect Ratio & Blink Counter"]
    end

    subgraph Dashboard ["PostureCare Web Platform (FastAPI)"]
        SERVER["FastAPI Backend (Port 8080)"]
        WEB_UI["Interactive Web Dashboard"]
        LLM["LLM Insights Engine (OpenRouter)"]
    end

    CAM -->|MJPEG Video Stream| CV_TRACK
    LDR -->|ADC / Lux| RTDB_SENSOR
    US -->|Distance in cm| RTDB_SENSOR
    CV_TRACK --> HEAD_POSE
    CV_TRACK --> BLINK_DET
    HEAD_POSE -->|Pitch, Roll, Yaw| RTDB_AI
    BLINK_DET -->|EAR, Blinks, Warnings| RTDB_AI
    RTDB_SENSOR --> SERVER
    RTDB_AI --> SERVER
    SERVER --> LLM
    LLM --> RTDB_ADVICE
    SERVER --> WEB_UI
```

---

## ✨ Key Features

1. **AI Vision & Head Pose Tracking (`tracking_AI/`)**:
   - **Blink Tracking & EAR**: Measures Eye Aspect Ratio (EAR) in real time to calculate blink frequency and detect eye fatigue.
   - **Head Pose Estimation (solvePnP)**: Calculates precise Pitch, Roll, and Yaw angles relative to the camera with dynamic 3D head axes visualization.
   - **Interpupillary Distance (IPD)**: Estimates distance from eyes to the screen in centimeters with instant 1-click calibration.
   - **Instant Ergonomic Alerts**: Triggers real-time alerts when tilting head down/up (>15°), tilting off-axis (>15°), or sitting too close (<40cm).

2. **IoT Sensor & Camera Firmware (`firmware/`)**:
   - **ESP32-S3 Camera Server**: High-speed MJPEG video streaming at `http://<ESP32_IP>:81/stream`.
   - **Ambient Light Monitoring**: LDR sensor converts analog reading into calibrated Lux values.
   - **Ultrasonic Distance Measurement**: Measures workspace obstacle clearance using HC-SR04.
   - **Direct Cloud Sync**: Publishes sensor readings to Firebase Realtime Database every 2 seconds.

3. **Intelligent Web Dashboard & LLM Insights (`dashboard/gPBL/`)**:
   - **Real-Time Visualization**: HUD metrics, 3D attitude visualizers, sensor dials, and alert feeds.
   - **Rule Evaluation Engine**: Customizable thresholds for posture, lighting, and viewing distance via `data/rules.json`.
   - **AI Ergonomic Advisor**: Generates context-aware health advice and sitting habit summaries powered by LLMs (OpenRouter API with heuristic fallback).

---

## 📁 Repository Structure

```
gpbl/
├── firmware/                   # ESP32-S3 PlatformIO embedded project
│   ├── include/
│   │   ├── camera_pins.h       # Pin configuration for ESP32-S3-EYE / AI-Thinker
│   │   ├── firebase_manager.h  # Firebase RTDB client declarations
│   │   └── sensor_manager.h    # Sensor pinout & calculation definitions
│   ├── src/
│   │   ├── app_httpd.cpp       # Camera web server implementation
│   │   ├── firebase_manager.cpp# Anonymous auth & data upload logic
│   │   ├── main.cpp            # Main setup() and loop() routines
│   │   └── sensor_manager.cpp  # ADC, Lux & ultrasonic distance drivers
│   ├── platformio.ini          # PlatformIO environment & build flags
│   └── README.md               # Firmware specific documentation
│
├── tracking_AI/                # Python Computer Vision & Tracking Engine
│   ├── blink_counter.py        # Lightweight blink & head pose tracker
│   ├── blink_counter_and_EAR_plot.py # Comprehensive tracker with real-time EAR graph
│   ├── FaceMeshModule.py       # MediaPipe FaceMesh wrapper
│   ├── utils.py                # Video threading and UI drawing utilities
│   ├── requirements.txt        # Python dependencies for CV tracking
│   └── README.md               # Tracking module documentation
│
├── dashboard/gPBL/             # Web Platform & Backend Service
│   ├── backend/
│   │   ├── routers/            # FastAPI REST and WebSocket endpoints
│   │   ├── detectors.py        # Posture & environmental risk rules engine
│   │   ├── firebase_client.py  # Firebase Admin SDK communication
│   │   ├── llm_service.py      # OpenRouter LLM ergonomic advice engine
│   │   ├── main.py             # FastAPI application entrypoint
│   │   ├── requirements.txt    # Backend dependencies
│   │   └── static/             # Frontend UI assets, CSS, JS & sounds
│   ├── run.ps1                 # 1-click Windows startup script
│   ├── run.sh                  # 1-click Linux/macOS startup script
│   ├── run.bat                 # Windows batch launcher
│   └── README.md               # Dashboard specific guide
│
├── run.sh                      # Root macOS/Linux launcher (dashboard + tracking)
├── run.ps1                     # Root Windows launcher (dashboard + tracking)
├── platformio.ini              # Root PlatformIO workspace configuration
└── README.md                   # Main project documentation (this file)
```

---

## 🔌 Hardware Setup & Pinout

### ESP32-S3 Pin Mapping

| Component | Pin Type | ESP32-S3 GPIO | Description |
|---|---|---|---|
| **LDR (Light Sensor)** | ADC Analog | `GPIO 1` | 12-bit ADC reading (0 - 4095) |
| **HC-SR04 Ultrasonic Trig** | Digital Output | `GPIO 21` | Ultrasonic trigger pulse (10µs) |
| **HC-SR04 Ultrasonic Echo** | Digital Input | `GPIO 14` | Ultrasonic pulse width measurement |
| **Red Warning LED** | Digital Output | `GPIO 47` | Ergonomic violation indicator |
| **Green Status LED** | Digital Output | `GPIO 45` | Safe status indicator |
| **On-board Flash LED** | PWM Output | `GPIO 48` / `LED_GPIO_NUM` | Camera illumination |
| **Camera Module** | DVP Parallel | S3-EYE standard | Refer to `firmware/include/camera_pins.h` |

---

## 🚀 Quick Start Guide

**Python 3.10–3.12** is required for the tracking stack (MediaPipe). **3.11 is recommended.** Python 3.13 is not supported for `tracking_AI`.

From the repo root, `./run.sh` (macOS / Linux) or `.\run.ps1` (Windows) creates two venvs, bootstraps missing config, starts the dashboard, then launches tracking. That is the supported path after `git clone`.

**macOS Camera:** grant Camera access to the **parent app** (Terminal, iTerm, VS Code, or Cursor) under System Settings → Privacy & Security → Camera. If access is denied, tracking stays up with a “CONNECTING…” placeholder; it does not exit.

### Step 1: Flash Firmware to ESP32-S3

1. Open the project in VS Code with the **PlatformIO** extension.
2. Edit WiFi credentials in [firmware/src/main.cpp](file:///d:/Documents/PlatformIO/Projects/gpbl/firmware/src/main.cpp):
   ```cpp
   const char* ssid = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```
3. Connect the ESP32-S3 via USB and upload:
   ```bash
   pio run -t upload
   ```
4. Open the Serial Monitor (`115200` baud) to find the ESP32 IP address (e.g. `http://192.168.1.50`).

---

### Step 2: Run dashboard + AI tracking (repo root)

Prefer the root launcher (venv + config + FastAPI + tracking). Do not `pip install` globally.

**Windows (PowerShell):**
```powershell
.\run.ps1
```

**macOS / Linux:**
```bash
chmod +x run.sh && ./run.sh
```

Optional camera argument (default `0`): `./run.sh 0` or `.\run.ps1 0`. For an ESP32 MJPEG URL, pass it as the first argument.

- Press **`c`** to calibrate baseline head pose (0°) and eye distance.
- Press **`p`** to quit tracking (the dashboard process is then stopped).

---

### Step 3: Dashboard-only (optional)

If you only need the web UI, use the nested scripts in `dashboard/gPBL/` (Python 3.13 is allowed here; no MediaPipe). You still need `backend/serviceAccountKey.json` (copy from the `.example` or a Firebase Admin key). Root scripts copy `.env` and `data/rules.json` from examples when missing; they will not invent a fake service-account key.

- **Dashboard**: [http://localhost:8080/dashboard](http://localhost:8080/dashboard)
- **Interactive API Docs**: [http://localhost:8080/docs](http://localhost:8080/docs)

---

## 📊 Firebase Realtime Database Structure

| Node Path | Source | Description |
|---|---|---|
| `/sensor_data/light_adc` | ESP32 | Raw 12-bit ADC light reading (0 - 4095) |
| `/sensor_data/lux` | ESP32 | Calibrated ambient illumination (Lux) |
| `/sensor_data/distance` | ESP32 | Physical obstacle distance via Ultrasonic (cm) |
| `/ai_data/pitch` | AI Vision | Head tilt angle up/down (degrees) |
| `/ai_data/roll` | AI Vision | Head tilt angle sideways (degrees) |
| `/ai_data/yaw` | AI Vision | Head turn angle left/right (degrees) |
| `/ai_data/distance_cm`| AI Vision | User face-to-camera distance (cm) |
| `/ai_data/ear` | AI Vision | Real-time Eye Aspect Ratio |
| `/ai_data/blinks` | AI Vision | Total blink count |
| `/ai_data/warnings` | AI Vision | Active ergonomic warning messages |
| `/ai_data/posture_status` | AI Vision | Current posture evaluation (`GOOD`, `WARNING`, `DANGER`) |
| `/advice` | Backend LLM | AI-generated ergonomic suggestions and posture insights |

---

## 🛠 Tech Stack

- **Firmware**: C++ (Arduino Framework, ESP-IDF Camera Driver, PlatformIO, Firebase-ESP-Client)
- **Computer Vision**: Python 3.10–3.12 (3.11 recommended), OpenCV, MediaPipe Face Mesh, NumPy, Matplotlib
- **Backend**: FastAPI, Uvicorn, Pydantic, Firebase Admin SDK, OpenRouter AI API
- **Frontend**: Vanilla HTML5, Modern Responsive CSS3 (Glassmorphism & Dark UI), Canvas 2D/3D Rendering, Web Audio API

---

## 📄 License

This project is licensed under the Apache License 2.0.
