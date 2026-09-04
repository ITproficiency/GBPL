#include "firebase_manager.h"
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>
#include "sensor_manager.h"

// Firebase control objects
static FirebaseData fbdo;
static FirebaseAuth auth;
static FirebaseConfig config;

bool init_firebase() {
    config.api_key = FIREBASE_API_KEY;
    config.database_url = FIREBASE_DATABASE_URL;

    Serial.println("[Firebase] Signing in anonymously...");

    if (Firebase.signUp(&config, &auth, "", "")) {
        Serial.println("[Firebase] Anonymous sign-in SUCCESSFUL!");
    } else {
        Serial.printf("[Firebase] Anonymous sign-in FAILED: %s\n", config.signer.signupError.message.c_str());
        return false;
    }

    config.token_status_callback = tokenStatusCallback;

    Firebase.begin(&config, &auth);
    Firebase.reconnectWiFi(true);

    Serial.println("[Firebase] Firebase Realtime Database initialization completed!");
    return true;
}

void upload_sensor_data_to_firebase(int light_adc, float lux, float distance) {
    if (!Firebase.ready()) {
        return;
    }

    FirebaseJson json;
    json.set("light_adc", light_adc);
    json.set("lux", lux);
    if (distance >= 0) {
        json.set("distance", distance);
    }

    if (Firebase.RTDB.setJSON(&fbdo, "/sensor_data", &json)) {
        Serial.println("[Firebase] Upload sensor_data: OK");
    } else {
        Serial.printf("[Firebase] Upload sensor_data ERROR: %s\n", fbdo.errorReason().c_str());
    }
}

void ensure_led_schema() {
    if (!Firebase.ready()) {
        return;
    }

    // Check if led_state exists
    if (Firebase.RTDB.getJSON(&fbdo, "/led_state")) {
        Serial.println("[Firebase] /led_state schema exists");
        return;
    }

    // Create default schema used by the app:
    // led_state/red_led, led_state/green_led, led_state/buzzer
    FirebaseJson json;
    json.set("green_led", true);
    json.set("red_led", false);
    json.set("buzzer", false);

    if (Firebase.RTDB.setJSON(&fbdo, "/led_state", &json)) {
        Serial.println("[Firebase] Created /led_state with green_led/red_led/buzzer");
    } else {
        Serial.printf("[Firebase] Failed to create /led_state: %s\n", fbdo.errorReason().c_str());
    }
}

void apply_led_state_from_firebase() {
    if (!Firebase.ready()) {
        Serial.println("[Firebase] Not ready, LED state not applied.");
        return;
    }

    bool greenOn = false;
    bool redOn = false;
    bool buzzerOn = false;

    if (Firebase.RTDB.getBool(&fbdo, "/led_state/green_led")) {
        greenOn = fbdo.to<bool>();
    } else {
        Serial.printf("[Firebase] Failed to read /led_state/green_led: %s\n", fbdo.errorReason().c_str());
    }

    if (Firebase.RTDB.getBool(&fbdo, "/led_state/red_led")) {
        redOn = fbdo.to<bool>();
    } else {
        Serial.printf("[Firebase] Failed to read /led_state/red_led: %s\n", fbdo.errorReason().c_str());
    }

    if (Firebase.RTDB.getBool(&fbdo, "/led_state/buzzer")) {
        buzzerOn = fbdo.to<bool>();
    } else {
        Serial.printf("[Firebase] Failed to read /led_state/buzzer: %s\n", fbdo.errorReason().c_str());
    }

    // Read Firebase bools and drive GPIO pins directly.
    digitalWrite(LED_RED, redOn ? HIGH : LOW);
    digitalWrite(GREEN_LED, greenOn ? HIGH : LOW);
#if defined(BUZZER_PIN)
    digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
#endif

    Serial.printf("[Firebase LED] green_led=%s | red_led=%s | buzzer=%s\n",
                  greenOn ? "ON" : "OFF",
                  redOn ? "ON" : "OFF",
                  buzzerOn ? "ON" : "OFF");
}
