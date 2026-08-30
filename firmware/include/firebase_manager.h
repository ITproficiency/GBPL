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
 * Ensure the `led_state` schema exists in the Realtime Database.
 * Creates an object with `red` and `green` keys if missing.
 */
void ensure_led_schema();

/**
 * Read `/led_state/red` from Firebase and apply it to the red alert LED.
 * This function only controls the red alert LED; the green LED remains off.
 */
void apply_led_state_from_firebase();

#endif // FIREBASE_MANAGER_H_
