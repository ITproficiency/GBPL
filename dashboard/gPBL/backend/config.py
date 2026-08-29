import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

FIREBASE_DATABASE_URL = os.environ.get(
    "FIREBASE_DATABASE_URL",
    "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app",
)
FIREBASE_CREDENTIALS = os.environ.get(
    "FIREBASE_CREDENTIALS",
    str(BASE_DIR / "serviceAccountKey.json"),
)
FIREBASE_SENSOR_PATH = os.environ.get("FIREBASE_SENSOR_PATH", "sensor_data")
FIREBASE_ADVICE_PATH = os.environ.get("FIREBASE_ADVICE_PATH", "advice")
FIREBASE_LED_PATH = os.environ.get("FIREBASE_LED_PATH", "led_state")
DEFAULT_DEVICE_ID = os.environ.get("DEFAULT_DEVICE_ID", "esp32_001")

RULES_FILE_PATH = os.environ.get("RULES_FILE_PATH", str(BASE_DIR / "data" / "rules.json"))

HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", str(BASE_DIR / "data" / "history.db"))
SENSOR_POLL_INTERVAL_SEC = int(os.environ.get("SENSOR_POLL_INTERVAL_SEC", "10"))
INSIGHT_WINDOW_MINUTES = int(os.environ.get("INSIGHT_WINDOW_MINUTES", "5"))
INSIGHT_MIN_READINGS = int(os.environ.get("INSIGHT_MIN_READINGS", "5"))
INSIGHT_COOLDOWN_SEC = int(os.environ.get("INSIGHT_COOLDOWN_SEC", "60"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

ANALYZE_COOLDOWN_SEC = int(os.environ.get("ANALYZE_COOLDOWN_SEC", "60"))
