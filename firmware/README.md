# ESP32-S3 Firmware - Camera, Environmental Sensors & Firebase RTDB

Firmware for the ESP32-S3 DevKitC / ESP32-S3-EYE microcontroller handling OV2640/OV3660 camera streaming, LDR light level measurement, ultrasonic distance sensing, and direct data dispatching to Firebase Realtime Database.

## 📌 Hardware Pinout

| Component | ESP32-S3 GPIO | Function |
|---|---|---|
| **LDR (Light Sensor)** | `GPIO 1` | Analog ADC Input (0 - 4095) |
| **Ultrasonic HC-SR04 Trigger** | `GPIO 21` | Digital Output (Trig Pulse) |
| **Ultrasonic HC-SR04 Echo** | `GPIO 14` | Digital Input (Echo Duration) |
| **Red Alert LED** | `GPIO 47` | Digital Output |
| **Green Status LED** | `GPIO 45` | Digital Output |
| **Flash LED** | `GPIO 48` / `LED_GPIO_NUM` | PWM Illuminator |
| **Camera D0 - D7, XCLK, PCLK** | Standard S3-EYE pins | See `camera_pins.h` |

## ⚙️ Compilation & Flashing

This project uses [PlatformIO](https://platformio.org/).

### Prerequisites
- Install VS Code with the **PlatformIO IDE** extension or PlatformIO Core CLI.

### Build and Upload
```bash
# In the firmware directory (or root directory using platformio.ini)
pio run -t upload

# Open Serial Monitor
pio device monitor -b 115200
```

## 🌐 Network & Firebase Endpoints
- Web stream server: `http://<ESP32_IP>:81/stream`
- Firebase Realtime Database path: `/sensor_data` (`light_adc`, `lux`, `distance`)
