#ifndef SENSOR_MANAGER_H_
#define SENSOR_MANAGER_H_

#include <Arduino.h>

#define LIGHT_PIN  1

#define TRIG_PIN   21
#define ECHO_PIN   14

#define LED_RED    47
#define GREEN_LED  45

// LDR Lux Constants
#define LUX_A 0.0005f
#define LUX_B 2.0f

/**
 * Initialize GPIO pin configuration and ADC resolution for sensors.
 */
void init_sensors();

/**
 * Read raw ADC value directly from LDR light sensor (0 - 4095).
 */
int read_light_adc();

/**
 * Read and calculate light intensity in Lux.
 */
float read_lux();

/**
 * Read distance from Ultrasonic sensor (cm).
 * Returns -1.0f if no echo signal is received.
 */
float read_distance();

#endif // SENSOR_MANAGER_H_
