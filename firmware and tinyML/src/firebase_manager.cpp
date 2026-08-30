#include "firebase_manager.h"
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>

// Đối tượng điều khiển Firebase
static FirebaseData fbdo;
static FirebaseAuth auth;
static FirebaseConfig config;

bool init_firebase() {
    config.api_key = FIREBASE_API_KEY;
    config.database_url = FIREBASE_DATABASE_URL;

    Serial.println("[Firebase] Dang dang nhap an danh (Anonymous Sign-In)...");

    if (Firebase.signUp(&config, &auth, "", "")) {
        Serial.println("[Firebase] Dang nhap an danh THANH CONG!");
    } else {
        Serial.printf("[Firebase] Dang nhap an danh THAT BAI: %s\n", config.signer.signupError.message.c_str());
        return false;
    }

    config.token_status_callback = tokenStatusCallback;

    Firebase.begin(&config, &auth);
    Firebase.reconnectWiFi(true);

    Serial.println("[Firebase] Khoi tao Firebase Realtime Database hoan tat!");
    return true;
}

void upload_sensor_data_to_firebase(int light_adc, float lux, float distance) {
    if (!Firebase.ready()) {
        return;
    }

    // Đăng tải Light ADC
    if (Firebase.RTDB.setInt(&fbdo, "/sensor_data/light_adc", light_adc)) {
        Serial.println("[Firebase] Upload light ADC: OK");
    } else {
        Serial.printf("[Firebase] Upload light ADC ERROR: %s\n", fbdo.errorReason().c_str());
    }

    // Đăng tải Lux
    if (Firebase.RTDB.setFloat(&fbdo, "/sensor_data/lux", lux)) {
        Serial.println("[Firebase] Upload lux: OK");
    } else {
        Serial.printf("[Firebase] Upload lux ERROR: %s\n", fbdo.errorReason().c_str());
    }

    // Đăng tải Distance
    if (distance >= 0) {
        if (Firebase.RTDB.setFloat(&fbdo, "/sensor_data/distance", distance)) {
            Serial.println("[Firebase] Upload distance: OK");
        } else {
            Serial.printf("[Firebase] Upload distance ERROR: %s\n", fbdo.errorReason().c_str());
        }
    }
}

void upload_head_pose_to_firebase(int class_id, const char* class_name, float confidence) {
    if (!Firebase.ready()) {
        return;
    }

    Firebase.RTDB.setInt(&fbdo, "/ai_data/head_pose/class_id", class_id);
    Firebase.RTDB.setString(&fbdo, "/ai_data/head_pose/class_name", class_name);
    Firebase.RTDB.setFloat(&fbdo, "/ai_data/head_pose/confidence", confidence);
}
