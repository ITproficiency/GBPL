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
 * Khởi tạo cấu hình chân GPIO và độ phân giải ADC cho cảm biến.
 */
void init_sensors();

/**
 * Đọc giá trị ADC trực tiếp từ cảm biến ánh sáng LDR (0 - 4095).
 */
int read_light_adc();

/**
 * Đọc và tính toán cường độ ánh sáng theo đơn vị Lux.
 */
float read_lux();

/**
 * Đọc khoảng cách từ cảm biến siêu âm Ultrasonic (cm).
 * Trả về -1.0f nếu không nhận được tín hiệu Echo.
 */
float read_distance();

#endif // SENSOR_MANAGER_H_
