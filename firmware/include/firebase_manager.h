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

#endif // FIREBASE_MANAGER_H_
