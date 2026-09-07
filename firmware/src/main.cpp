#include "esp_camera.h"
#include <WiFi.h>
#include <time.h>
#include "sensor_manager.h"
#include "firebase_manager.h"

// ===================
// Select camera model
// ===================
#define CAMERA_MODEL_ESP32S3_EYE // Has PSRAM
#include "camera_pins.h"

// ===========================
// Enter your WiFi credentials
// ===========================
const char* ssid = "Toantham";
const char* password = "hoilamgi1";

void startCameraServer();
void setupLedFlash(int pin);

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000); // Wait for Serial CDC (USB) connection
  delay(1000);
  Serial.setDebugOutput(true);
  Serial.println();

  Serial.println("==================================================");
  Serial.println("   ESP32-S3 Camera + Sensors + Firebase");
  Serial.println("==================================================");

  // Initialize sensors (LDR & Ultrasonic)
  init_sensors();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QVGA;
  config.pixel_format = PIXFORMAT_JPEG; // for streaming
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 12;
  config.fb_count = 2;
  
  if (config.pixel_format == PIXFORMAT_JPEG) {
    if (psramFound()) {
      config.jpeg_quality = 10;
      config.fb_count = 2;
      config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
      config.frame_size = FRAMESIZE_SVGA;
      config.fb_location = CAMERA_FB_IN_DRAM;
    }
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  // camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x", err);
    return;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  if (config.pixel_format == PIXFORMAT_JPEG) {
    s->set_framesize(s, FRAMESIZE_QVGA);
  }

#if defined(CAMERA_MODEL_ESP32S3_EYE)
  s->set_vflip(s, 1);
#endif

#if defined(LED_GPIO_NUM)
  setupLedFlash(LED_GPIO_NUM);
#endif

  // Connect to WiFi with Google DNS (8.8.8.8) to prevent DNS lookup failures for www.googleapis.com
  // Connect to WiFi
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(true);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.println("[WiFi] Connected successfully!");

  // Set Google DNS (8.8.8.8) using DHCP assigned IP parameters to fix DNS lookup for googleapis.com
  IPAddress primaryDNS(8, 8, 8, 8);
  IPAddress secondaryDNS(8, 8, 4, 4);
  WiFi.config(WiFi.localIP(), WiFi.gatewayIP(), WiFi.subnetMask(), primaryDNS, secondaryDNS);

  // Synchronize system time via NTP — required for SSL certificate validation
  configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");
  Serial.println("[Time] Waiting for NTP time sync...");
  struct tm timeinfo;
  int retry = 0;
  while (!getLocalTime(&timeinfo) && retry < 10) {
    delay(1000);
    Serial.print(".");
    retry++;
  }
  if (retry >= 10) {
    Serial.println("");
    Serial.println("[Time] Failed to sync time; SSL operations may fail.");
  } else {
    Serial.println("");
    Serial.printf("[Time] Current time: %s", asctime(&timeinfo));
  }

  // Initialize Firebase Realtime Database
  init_firebase();
  ensure_led_schema();

  startCameraServer();

  Serial.print("Camera Ready! Use 'http://");
  Serial.print(WiFi.localIP());
  Serial.println("' to connect\n");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Disconnected. Reconnecting...");
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    delay(2000);
    return;
  }

  // Read sensor data (LDR and Ultrasonic)
  int lightADC = read_light_adc();
  float lux = read_lux();
  float distance = read_distance();

  Serial.println("----------------------------------------");
  Serial.printf("Light ADC : %d | Lux : %.2f lux\n", lightADC, lux);
  if (distance < 0) {
    Serial.println("Distance  : No echo");
  } else {
    Serial.printf("Distance  : %.2f cm\n", distance);
  }

  // Apply LED and buzzer state from Firebase to local hardware
  apply_led_state_from_firebase();

  // Upload sensor data to Firebase Realtime Database
  upload_sensor_data_to_firebase(lightADC, lux, distance);

  // Wait 2 seconds between sensor readings
  delay(2000);
}
