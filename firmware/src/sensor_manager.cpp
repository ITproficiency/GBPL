#include "sensor_manager.h"
#include <math.h>

void init_sensors() {
    // Cấu hình độ phân giải ADC 12-bit (0 - 4095)
    analogReadResolution(12);

    // Cấu hình chân cho Cảm biến Siêu âm (Ultrasonic)
    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    digitalWrite(TRIG_PIN, LOW);

    // Cấu hình chân LED cảnh báo
    pinMode(LED_RED, OUTPUT);
    pinMode(GREEN_LED, OUTPUT);
    digitalWrite(LED_RED, LOW);
    digitalWrite(GREEN_LED, LOW);
}

int read_light_adc() {
    return analogRead(LIGHT_PIN);
}

float read_lux() {
    int adc = read_light_adc();
    if (adc <= 0) {
        return 0.0f;
    }
    return LUX_A * pow((float)adc, LUX_B);
}

float read_distance() {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);

    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);

    digitalWrite(TRIG_PIN, LOW);

    unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000); // Timeout 30ms

    if (duration == 0) {
        return -1.0f; // Timeout / No Echo
    }

    return duration * 0.0343f / 2.0f; // Đơn vị: cm
}
