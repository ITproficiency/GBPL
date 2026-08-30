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

    // Upload Light ADC
    if (Firebase.RTDB.setInt(&fbdo, "/sensor_data/light_adc", light_adc)) {
        Serial.println("[Firebase] Upload light ADC: OK");
    } else {
        Serial.printf("[Firebase] Upload light ADC ERROR: %s\n", fbdo.errorReason().c_str());
    }

    // Upload Lux
    if (Firebase.RTDB.setFloat(&fbdo, "/sensor_data/lux", lux)) {
        Serial.println("[Firebase] Upload lux: OK");
    } else {
        Serial.printf("[Firebase] Upload lux ERROR: %s\n", fbdo.errorReason().c_str());
    }

    // Upload Distance
    if (distance >= 0) {
        if (Firebase.RTDB.setFloat(&fbdo, "/sensor_data/distance", distance)) {
            Serial.println("[Firebase] Upload distance: OK");
        } else {
            Serial.printf("[Firebase] Upload distance ERROR: %s\n", fbdo.errorReason().c_str());
        }
    }
}

void ensure_led_schema() {
    if (!Firebase.ready()) {
        return;
    }

    // Check if led_state exists
    if (Firebase.RTDB.getJSON(&fbdo, "/led_state")) {
        Serial.println("[Firebase] /led_state exists");
        return;
    }

    // Create default schema with both LEDs off (but firmware will only use red)
    FirebaseJson json;
    json.set("red", false);
    json.set("green", false);

    if (Firebase.RTDB.setJSON(&fbdo, "/led_state", &json)) {
        Serial.println("[Firebase] Created /led_state schema");
    } else {
        Serial.printf("[Firebase] Failed to create /led_state: %s\n", fbdo.errorReason().c_str());
    }
}

void apply_led_state_from_firebase() {
    if (!Firebase.ready()) {
        return;
    }

    // Only read the red state and apply it. Keep green LED off.
    if (Firebase.RTDB.getBool(&fbdo, "/led_state/red")) {
        bool redState = fbdo.to<bool>();
        digitalWrite(LED_RED, redState ? HIGH : LOW);
        digitalWrite(GREEN_LED, LOW);
        Serial.printf("[Firebase] Applied led_state/red = %s\n", redState ? "ON" : "OFF");
    } else {
        Serial.printf("[Firebase] Failed to read /led_state/red: %s\n", fbdo.errorReason().c_str());
    }
}
