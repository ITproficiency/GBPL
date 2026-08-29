# gPBL — PostureCare Demo (local)

```
git clone → điền key → .\run.ps1
```

## Quick start

```powershell
git clone https://github.com/Apeiron2/gPBL.git
cd gPBL
```

**Secrets (1 lần, không commit Git):**

| File | Cách lấy |
|------|----------|
| `backend/serviceAccountKey.json` | Firebase Console → Service accounts → Generate key |
| `backend/.env` | `copy backend\.env.example backend\.env` → điền `OPENROUTER_API_KEY` |

Template: `backend/serviceAccountKey.json.example`

**Chạy:**

```powershell
.\run.ps1
```

macOS/Linux: `chmod +x run.sh && ./run.sh`

- Dashboard: http://localhost:8080/dashboard

`run.ps1` tự tạo `.venv`, cài dependencies, tạo `.env` + `data/rules.json` nếu thiếu.

## Rules

**Một file duy nhất:** `backend/data/rules.json` (template: `data/rules.json.example`). Sửa file → restart server.

ESP32 gửi **`light_adc`** (ADC 12-bit); backend tự quy đổi sang **lux** trước khi đánh giá. Chi tiết công thức: [`docs/light-adc-to-lux.md`](docs/light-adc-to-lux.md).

## Không có OpenRouter key?

App vẫn chạy — LLM dùng mock cho nút **Get AI Advice**.

## Yêu cầu

Python 3.11+, internet (Firebase).
