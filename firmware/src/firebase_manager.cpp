#include "firebase_manager.h"
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>

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
