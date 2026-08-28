/**
 * ============================================================================
 * HEAD POSE DETECTOR IMPLEMENTATION FOR ESP32-S3 (TENSORFLOW LITE MICRO)
 * ============================================================================
 */

#include "head_pose_detector.h"
#include "head_pose_model_data.h"
#include "img_converters.h"

// TensorFlow Lite Micro Headers
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_error_reporter.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"

// Tên hiển thị của 4 lớp phân loại
const char* HEAD_POSE_CLASS_NAMES[4] = {
    "Normal (Nhin thang)",
    "Text_Neck (Cui dau)",
    "Turned_Away (Quay sang ben)",
    "Tilted_Head (Nghieng dau)"
};

namespace {
    tflite::ErrorReporter* error_reporter = nullptr;
    const tflite::Model* model = nullptr;
    tflite::MicroInterpreter* interpreter = nullptr;
    TfLiteTensor* input = nullptr;
    TfLiteTensor* output = nullptr;

    // Kích thước Tensor Arena (bộ nhớ làm việc cho TFLite Micro)
    constexpr int kTensorArenaSize = 75 * 1024; // 75 KB
    uint8_t* tensor_arena = nullptr;

    // Buffer tạm lưu ảnh 64x64 Grayscale
    uint8_t gray_64x64_buf[64 * 64];

    // Buffer tạm giải mã ảnh RGB565 (static để không chiếm bộ nhớ Stack của FreeRTOS Task)
    static uint8_t s_rgb565_buf[80 * 60 * 2];
}

bool init_head_pose_detector() {
    Serial.println("\n[TFLite Micro] Dang khoi tao mo hinh Head Pose Classifier...");

    static tflite::MicroErrorReporter micro_error_reporter;
    error_reporter = &micro_error_reporter;

    // 1. Kiem tra va nap mo hinh tu g_head_pose_model
    model = tflite::GetModel(g_head_pose_model);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
        Serial.printf("[ERROR] TFLite Schema version mismatch! Model version=%d, Expected=%d\n",
                      model->version(), TFLITE_SCHEMA_VERSION);
        return false;
    }

    // 2. Cap phat Tensor Arena (uu tien dung PSRAM neu co)
    if (psramFound()) {
        tensor_arena = (uint8_t*)heap_caps_malloc(kTensorArenaSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        Serial.println("[TFLite Micro] Da cap phat 75 KB Tensor Arena trong PSRAM.");
    } else {
        tensor_arena = (uint8_t*)malloc(kTensorArenaSize);
        Serial.println("[TFLite Micro] Da cap phat 75 KB Tensor Arena trong Internal RAM.");
    }

    if (tensor_arena == nullptr) {
        Serial.println("[ERROR] Khong cap phat duoc bo nho Tensor Arena!");
        return false;
    }

    // 3. Khai bao Operator Resolver
    static tflite::AllOpsResolver resolver;

    // 4. Khoi tao Interpreter
    static tflite::MicroInterpreter static_interpreter(
        model, resolver, tensor_arena, kTensorArenaSize, error_reporter
    );
    interpreter = &static_interpreter;

    // 5. Cap phat tensor
    TfLiteStatus allocate_status = interpreter->AllocateTensors();
    if (allocate_status != kTfLiteOk) {
        Serial.println("[ERROR] AllocateTensors() thất bại!");
        return false;
    }

    // 6. Lay con tro Input & Output Tensors
    input = interpreter->input(0);
    output = interpreter->output(0);

    Serial.printf("[TFLite Micro] Model Inited OK! Input shape: [%d, %d, %d, %d], type: %d\n",
                  input->dims->data[0], input->dims->data[1],
                  input->dims->data[2], input->dims->data[3], input->type);
    Serial.printf("[TFLite Micro] Input scale: %f, zero_point: %d\n",
                  input->params.scale, input->params.zero_point);
    Serial.printf("[TFLite Micro] Output scale: %f, zero_point: %d\n",
                  output->params.scale, output->params.zero_point);

    return true;
}

int run_head_pose_inference(const uint8_t* gray_64x64, float* confidence_out) {
    if (!interpreter || !input || !output) {
        Serial.println("[ERROR] Interpreter chua duoc khoi tao!");
        return -1;
    }

    float input_scale = input->params.scale;
    int input_zero_point = input->params.zero_point;

    // Lượng tử hóa pixel [0..255] -> INT8
    for (int i = 0; i < 64 * 64; i++) {
        float val_norm = (float)gray_64x64[i] / 255.0f;
        int32_t val_q = (int32_t)round(val_norm / input_scale) + input_zero_point;
        if (val_q < -128) val_q = -128;
        if (val_q > 127) val_q = 127;
        input->data.int8[i] = (int8_t)val_q;
    }

    // Chạy suy luận
    TfLiteStatus invoke_status = interpreter->Invoke();
    if (invoke_status != kTfLiteOk) {
        Serial.println("[ERROR] Invoke() thất bại!");
        return -1;
    }

    // Giải lượng tử hóa kết quả đầu ra INT8 -> float probability
    float output_scale = output->params.scale;
    int output_zero_point = output->params.zero_point;

    float probs[4];
    int max_idx = 0;
    float max_prob = -1.0f;

    for (int c = 0; c < 4; c++) {
        int8_t val_q = output->data.int8[c];
        float p = (val_q - output_zero_point) * output_scale;
        probs[c] = p;
        if (p > max_prob) {
            max_prob = p;
            max_idx = c;
        }
    }

    if (confidence_out) {
        *confidence_out = max_prob;
    }

    return max_idx;
}

// Resize ảnh bất kỳ về 64x64 Grayscale bằng Nearest Neighbor
static void resize_to_64x64_gray(const uint8_t* src, int src_w, int src_h, bool is_rgb565, uint8_t* dst_64x64) {
    float x_ratio = (float)src_w / 64.0f;
    float y_ratio = (float)src_h / 64.0f;

    for (int y = 0; y < 64; y++) {
        int src_y = (int)(y * y_ratio);
        if (src_y >= src_h) src_y = src_h - 1;

        for (int x = 0; x < 64; x++) {
            int src_x = (int)(x * x_ratio);
            if (src_x >= src_w) src_x = src_w - 1;

            if (is_rgb565) {
                int src_idx = (src_y * src_w + src_x) * 2;
                uint8_t hb = src[src_idx];
                uint8_t lb = src[src_idx + 1];
                uint16_t pixel = (hb << 8) | lb;

                uint8_t r = (pixel >> 11) & 0x1F;
                uint8_t g = (pixel >> 5) & 0x3F;
                uint8_t b = pixel & 0x1F;

                r = (r * 527 + 23) >> 6;
                g = (g * 259 + 33) >> 6;
                b = (b * 527 + 23) >> 6;

                uint8_t gray = (uint8_t)(0.299f * r + 0.587f * g + 0.114f * b);
                dst_64x64[y * 64 + x] = gray;
            } else {
                // Đã là Grayscale
                int src_idx = src_y * src_w + src_x;
                dst_64x64[y * 64 + x] = src[src_idx];
            }
        }
    }
}

int run_head_pose_from_camera_fb(camera_fb_t* fb, float* confidence_out) {
    if (!fb || !fb->buf) {
        Serial.println("[ERROR] Frame Buffer camera rỗng!");
        return -1;
    }

    if (fb->format == PIXFORMAT_GRAYSCALE) {
        resize_to_64x64_gray(fb->buf, fb->width, fb->height, false, gray_64x64_buf);
    } else if (fb->format == PIXFORMAT_RGB565) {
        resize_to_64x64_gray(fb->buf, fb->width, fb->height, true, gray_64x64_buf);
    } else if (fb->format == PIXFORMAT_JPEG) {
        // Fast hardware-assisted JPEG decode scaling (1/4 scale -> 80x60 pixels in ~1.8ms)
        bool converted = jpg2rgb565(fb->buf, fb->len, s_rgb565_buf, JPG_SCALE_4X);
        if (!converted) {
            Serial.println("[ERROR] Giai ma Fast JPEG that bai!");
            return -1;
        }

        // Convert 80x60 RGB565 -> 64x64 Grayscale
        float x_ratio = 80.0f / 64.0f;
        float y_ratio = 60.0f / 64.0f;

        for (int y = 0; y < 64; y++) {
            int src_y = (int)(y * y_ratio);
            if (src_y >= 60) src_y = 59;

            for (int x = 0; x < 64; x++) {
                int src_x = (int)(x * x_ratio);
                if (src_x >= 80) src_x = 79;

                int idx = (src_y * 80 + src_x) * 2;
                uint8_t hb = s_rgb565_buf[idx];
                uint8_t lb = s_rgb565_buf[idx + 1];
                uint16_t pixel = (hb << 8) | lb;

                uint8_t r = ((pixel >> 11) & 0x1F) * 255 / 31;
                uint8_t g = ((pixel >> 5) & 0x3F) * 255 / 63;
                uint8_t b = (pixel & 0x1F) * 255 / 31;

                gray_64x64_buf[y * 64 + x] = (uint8_t)(0.299f * r + 0.587f * g + 0.114f * b);
            }
        }
    } else {
        Serial.printf("[ERROR] Dinh dang anh camera khong duoc ho tro: %d\n", fb->format);
        return -1;
    }

    return run_head_pose_inference(gray_64x64_buf, confidence_out);
}

const char* get_head_pose_label(int class_id) {
    if (class_id >= 0 && class_id < 4) {
        return HEAD_POSE_CLASS_NAMES[class_id];
    }
    return "Unknown";
}
