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

static bool s_led_schema_ensured = false;
static unsigned long s_led_schema_retry_ms = 0;

static bool s_led_red = false;
static bool s_led_green = false;
static bool s_led_blink = false;
static unsigned long s_last_led_fetch_ms = 0;

static const unsigned long LED_FETCH_INTERVAL_MS = 400;
static const unsigned long LED_BLINK_HALF_PERIOD_MS = 250;  // 2 Hz

static bool json_field_bool(FirebaseJson &json, const char *key) {
    FirebaseJsonData data;
    json.get(data, key);
    if (!data.success) {
        return false;
    }
    return data.to<bool>();
}

static bool json_has_key(FirebaseJson &json, const char *key) {
    FirebaseJsonData data;
    json.get(data, key);
    return data.success;
}

static void parse_led_object(FirebaseJson &json, bool &red, bool &green, bool &blink) {
    red = json_field_bool(json, "red");
    green = json_field_bool(json, "green");
    blink = json_field_bool(json, "blink");
}

static void drive_led_outputs(bool red, bool green, bool blink) {
    // Alert (red or blink) wins over monitoring green.
    const bool alert_active = red || blink;
    bool red_level = false;
    if (blink) {
        red_level = ((millis() / LED_BLINK_HALF_PERIOD_MS) % 2UL) == 0UL;
    } else if (red) {
        red_level = true;
    }

    digitalWrite(LED_RED, red_level ? HIGH : LOW);
    digitalWrite(GREEN_LED, (green && !alert_active) ? HIGH : LOW);
}

static bool fetch_led_state_from_firebase() {
    if (!Firebase.RTDB.get(&fbdo, "/led_state")) {
        return false;
    }

    const String dtype = fbdo.dataType();
    if (dtype == "boolean" || dtype == "bool") {
        // Legacy: a single boolean means alert/red on.
        const bool on = fbdo.to<bool>();
        s_led_red = on;
        s_led_green = false;
        s_led_blink = false;
        return true;
    }

    if (dtype == "int" || dtype == "integer" || dtype == "float" || dtype == "double") {
        s_led_red = fbdo.to<int>() != 0;
        s_led_green = false;
        s_led_blink = false;
        return true;
    }

    if (dtype == "json") {
        FirebaseJson json;
        json.setJsonData(fbdo.jsonString());
        parse_led_object(json, s_led_red, s_led_green, s_led_blink);
        return true;
    }

    return false;
}

void ensure_led_schema() {
    if (s_led_schema_ensured) {
        return;
    }
    if (!Firebase.ready()) {
        return;
    }
    const unsigned long now = millis();
    if (s_led_schema_retry_ms != 0 && (now - s_led_schema_retry_ms) < 2000) {
        return;
    }
    s_led_schema_retry_ms = now;

    if (Firebase.RTDB.get(&fbdo, "/led_state")) {
        const String dtype = fbdo.dataType();
        if (dtype == "boolean" || dtype == "bool" || dtype == "int" || dtype == "integer") {
            Serial.println("[Firebase] /led_state is a legacy scalar; leaving in place");
            s_led_schema_ensured = true;
            return;
        }
        if (dtype == "json") {
            FirebaseJson json;
            json.setJsonData(fbdo.jsonString());
            if (!json_has_key(json, "red")) {
                Firebase.RTDB.setBool(&fbdo, "/led_state/red", false);
            }
            if (!json_has_key(json, "green")) {
                Firebase.RTDB.setBool(&fbdo, "/led_state/green", false);
            }
            if (!json_has_key(json, "blink")) {
                Firebase.RTDB.setBool(&fbdo, "/led_state/blink", false);
            }
            Serial.println("[Firebase] /led_state object schema ok");
            s_led_schema_ensured = true;
            return;
        }
        Serial.printf("[Firebase] /led_state exists (type=%s); leaving in place\n", dtype.c_str());
        s_led_schema_ensured = true;
        return;
    }

    FirebaseJson json;
    json.set("red", false);
    json.set("green", false);
    json.set("blink", false);

    if (Firebase.RTDB.setJSON(&fbdo, "/led_state", &json)) {
        Serial.println("[Firebase] Created /led_state schema {red, green, blink}");
        s_led_schema_ensured = true;
    } else {
        Serial.printf("[Firebase] Failed to create /led_state: %s\n", fbdo.errorReason().c_str());
    }
}

void apply_led_state_from_firebase() {
    const unsigned long now = millis();
    if (Firebase.ready() && (s_last_led_fetch_ms == 0 || (now - s_last_led_fetch_ms) >= LED_FETCH_INTERVAL_MS)) {
        s_last_led_fetch_ms = now;
        if (fetch_led_state_from_firebase()) {
            static bool last_red = false;
            static bool last_green = false;
            static bool last_blink = false;
            if (s_led_red != last_red || s_led_green != last_green || s_led_blink != last_blink) {
                Serial.printf("[Firebase] LED red=%s green=%s blink=%s\n",
                              s_led_red ? "true" : "false",
                              s_led_green ? "true" : "false",
                              s_led_blink ? "true" : "false");
                last_red = s_led_red;
                last_green = s_led_green;
                last_blink = s_led_blink;
            }
        } else {
            static unsigned long last_fail_log_ms = 0;
            if (last_fail_log_ms == 0 || (now - last_fail_log_ms) >= 5000) {
                last_fail_log_ms = now;
                Serial.printf("[Firebase] Failed to read /led_state: %s\n", fbdo.errorReason().c_str());
            }
        }
    }

    drive_led_outputs(s_led_red, s_led_green, s_led_blink);
}
