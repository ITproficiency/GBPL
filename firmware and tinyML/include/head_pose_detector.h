/**
 * ============================================================================
 * HEAD POSE DETECTOR FOR ESP32-S3 (TENSORFLOW LITE MICRO)
 * ============================================================================
 * Module suy luận mô hình TinyML phân loại tư thế đầu (Head Pose Classification):
 *   - Class 0: Normal (Nhìn thẳng)
 *   - Class 1: Text_Neck (Cúi đầu)
 *   - Class 2: Turned_Away (Quay đầu)
 *   - Class 3: Tilted_Head (Nghiêng đầu)
 * ============================================================================
 */

#ifndef HEAD_POSE_DETECTOR_H_
#define HEAD_POSE_DETECTOR_H_

#include <Arduino.h>
#include "esp_camera.h"

// Tên các lớp phân loại
extern const char* HEAD_POSE_CLASS_NAMES[4];

/**
 * Khởi tạo mô hình TensorFlow Lite Micro trên ESP32-S3.
 * Cấp phát Tensor Arena trong bộ nhớ RAM / PSRAM.
 * 
 * Trả về: true nếu khởi tạo thành công, false nếu có lỗi.
 */
bool init_head_pose_detector();

/**
 * Thực hiện suy luận (Inference) từ mảng ảnh Grayscale 64x64 (64x64 = 4096 bytes).
 * 
 * @param gray_64x64 Con trỏ chứa 4096 byte ảnh xám (giá trị 0-255).
 * @param confidence_out (Output) Con trỏ nhận độ tin cậy của dự đoán [0.0, 1.0].
 * @return ID của lớp có xác suất cao nhất (0: Normal, 1: Text_Neck, 2: Turned_Away, 3: Tilted_Head).
 */
int run_head_pose_inference(const uint8_t* gray_64x64, float* confidence_out);

/**
 * Thực hiện suy luận trực tiếp từ Frame Buffer của camera OV2640 / OV3660.
 * Tự động giải mã JPEG / RGB565, resize về 64x64 Grayscale và chạy mô hình TFLite.
 * 
 * @param fb Con trỏ camera_fb_t thu được từ esp_camera_fb_get().
 * @param confidence_out (Output) Con trỏ nhận độ tin cậy của dự đoán [0.0, 1.0].
 * @return ID của lớp có xác suất cao nhất (-1 nếu có lỗi).
 */
int run_head_pose_from_camera_fb(camera_fb_t* fb, float* confidence_out);

/**
 * Trả về chuỗi tên tiếng Việt / mô tả cho lớp phân loại tương ứng.
 */
const char* get_head_pose_label(int class_id);

#endif // HEAD_POSE_DETECTOR_H_
