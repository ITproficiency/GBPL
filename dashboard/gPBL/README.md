# PostureCare Dashboard (gPBL)

Real-time ergonomic health & posture monitoring web dashboard with intelligent LLM insights powered by FastAPI, Firebase Realtime Database, and OpenRouter.

```
git clone → configure secrets → .\run.ps1
```

## 🚀 Quick Start

### 1. Secrets Configuration (One-time Setup)

| File | Instructions |
|------|--------------|
| `backend/serviceAccountKey.json` | Download private key from **Firebase Console → Project Settings → Service accounts → Generate new private key** and save to `backend/serviceAccountKey.json` (Template: `backend/serviceAccountKey.json.example`) |
| `backend/.env` | Copy `backend/.env.example` to `backend/.env` and set `OPENROUTER_API_KEY` |

### 2. Run Locally

#### Windows (PowerShell):
```powershell
.\run.ps1
```
*Alternatively, run `run.bat`.*

#### macOS / Linux:
```bash
chmod +x run.sh && ./run.sh
```

- **Dashboard UI**: http://localhost:8080/dashboard
- **Interactive API Docs**: http://localhost:8080/docs

> `run.ps1` / `run.sh` automatically creates `.venv`, installs dependencies, copies `.env` and `data/rules.json` from templates if missing, and launches the Uvicorn server.

## ⚙️ Rules & Configuration

- **Rules File**: `backend/data/rules.json` (template: `data/rules.json.example`). Edit thresholds (posture angles, distance, light lux, blink rates) and restart server or save changes.
- **Light ADC to Lux Conversion**: ESP32 transmits 12-bit ADC `light_adc`; the backend converts it to `lux` before evaluating threshold rules.

## 🤖 LLM Advice & Fallback

If `OPENROUTER_API_KEY` is not provided or left as default, the backend operates seamlessly with built-in heuristic mock advice responses when clicking **Get AI Advice**.

## 📋 Requirements

- Python 3.11+
- Internet access (for Firebase RTDB & OpenRouter API)
