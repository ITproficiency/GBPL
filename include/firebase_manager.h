#ifndef FIREBASE_MANAGER_H_
#define FIREBASE_MANAGER_H_

#include <Arduino.h>
#include <Firebase_ESP_Client.h>

// Firebase Credentials
#define FIREBASE_API_KEY      "AIzaSyDRCPkzUAeEFy6f8sHo1vXBYtzezRmAsDU"
#define FIREBASE_DATABASE_URL "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app"

/**
 * Khởi tạo cấu hình và đăng nhập ẩn danh vào Firebase Realtime Database.
 * @return true nếu khởi tạo thành công, false nếu thất bại.
 */
bool init_firebase();

/**
 * Đăng tải dữ liệu cảm biến (LDR ADC, Lux, Khoảng cách) lên Firebase Realtime Database.
 * 
 * @param light_adc Giá trị ADC từ cảm biến ánh sáng LDR.
 * @param lux Giá trị độ sáng tính theo Lux.
 * @param distance Khoảng cách đo được từ cảm biến siêu âm (cm).
 */
void upload_sensor_data_to_firebase(int light_adc, float lux, float distance);

/**
 * Đăng tải kết quả suy luận AI Head Pose Classifier lên Firebase Realtime Database.
 * 
 * @param class_id ID lớp phân loại tư thế đầu (0-3).
 * @param class_name Tên lớp phân loại.
 * @param confidence Độ tin cậy [0.0 - 1.0].
 */
void upload_head_pose_to_firebase(int class_id, const char* class_name, float confidence);

#endif // FIREBASE_MANAGER_H_
