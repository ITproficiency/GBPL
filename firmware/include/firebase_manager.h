#ifndef FIREBASE_MANAGER_H_
#define FIREBASE_MANAGER_H_

#include <Arduino.h>
#include <Firebase_ESP_Client.h>

// Firebase Credentials
#define FIREBASE_API_KEY      "AIzaSyDRCPkzUAeEFy6f8sHo1vXBYtzezRmAsDU"
#define FIREBASE_DATABASE_URL "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app"

/**
 * Initialize Firebase configuration and sign in anonymously to Firebase Realtime Database.
 * @return true if initialized successfully, false otherwise.
 */
bool init_firebase();

/**
 * Upload sensor data (LDR ADC, Lux, Distance) to Firebase Realtime Database.
 * 
 * @param light_adc ADC value from LDR light sensor (0 - 4095).
 * @param lux Calculated light intensity in Lux.
 * @param distance Distance measured by ultrasonic sensor in cm.
 */
void upload_sensor_data_to_firebase(int light_adc, float lux, float distance);

/**
 * Ensure `/led_state` exists as `{red, green, blink}` (all false) if the node
 * is missing. Leaves a legacy boolean node in place. Safe to call more than
 * once; runs at most one successful ensure.
 */
void ensure_led_schema();

/**
 * Fetch `/led_state` from Firebase (object preferred, legacy boolean tolerated)
 * and drive the red/green GPIOs. Blink is generated in firmware (~2 Hz) from
 * a cached `blink` flag so it does not depend on the poller interval.
 * Call every loop cycle; network reads are throttled internally.
 */
void apply_led_state_from_firebase();

#endif // FIREBASE_MANAGER_H_
