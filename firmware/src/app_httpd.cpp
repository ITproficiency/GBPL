// Copyright 2015-2016 Espressif Systems (Shanghai) PTE LTD
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
#include "esp_http_server.h"
#include "esp_timer.h"
#include "esp_camera.h"
#include "img_converters.h"
#include "fb_gfx.h"
#include "esp32-hal-ledc.h"
#include "sdkconfig.h"
#include "camera_index.h"
#include "firebase_manager.h"
#include "sensor_manager.h"

#if defined(ARDUINO_ARCH_ESP32) && defined(CONFIG_ARDUHAL_ESP_LOG)
#include "esp32-hal-log.h"
#endif

// Face Detection will not work on boards without (or with disabled) PSRAM
#ifdef BOARD_HAS_PSRAM
#define CONFIG_ESP_FACE_DETECT_ENABLED 0
// Face Recognition takes upward from 15 seconds per frame on chips other than ESP32S3
// Makes no sense to have it enabled for them
#if CONFIG_IDF_TARGET_ESP32S3
#define CONFIG_ESP_FACE_RECOGNITION_ENABLED 0
#else
#define CONFIG_ESP_FACE_RECOGNITION_ENABLED 0
#endif
#else
#define CONFIG_ESP_FACE_DETECT_ENABLED 0
#define CONFIG_ESP_FACE_RECOGNITION_ENABLED 0
#endif

#if CONFIG_ESP_FACE_DETECT_ENABLED

#include <vector>
#include "human_face_detect_msr01.hpp"
#include "human_face_detect_mnp01.hpp"

#define TWO_STAGE 1 /*<! 1: detect by two-stage which is more accurate but slower(with keypoints). */
                    /*<! 0: detect by one-stage which is less accurate but faster(without keypoints). */

#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
#include "face_recognition_tool.hpp"
#include "face_recognition_112_v1_s16.hpp"
#include "face_recognition_112_v1_s8.hpp"

#define QUANT_TYPE 0 //if set to 1 => very large firmware, very slow, reboots when streaming...

#define FACE_ID_SAVE_NUMBER 7
#endif

#define FACE_COLOR_WHITE 0x00FFFFFF
#define FACE_COLOR_BLACK 0x00000000
#define FACE_COLOR_RED 0x000000FF
#define FACE_COLOR_GREEN 0x0000FF00
#define FACE_COLOR_BLUE 0x00FF0000
#define FACE_COLOR_YELLOW (FACE_COLOR_RED | FACE_COLOR_GREEN)
#define FACE_COLOR_CYAN (FACE_COLOR_BLUE | FACE_COLOR_GREEN)
#define FACE_COLOR_PURPLE (FACE_COLOR_BLUE | FACE_COLOR_RED)
#endif

// Enable LED FLASH setting
#define CONFIG_LED_ILLUMINATOR_ENABLED 1

// LED FLASH setup
#if CONFIG_LED_ILLUMINATOR_ENABLED

#define LED_LEDC_CHANNEL 2 //Using different ledc channel/timer than camera
#define CONFIG_LED_MAX_INTENSITY 255

int led_duty = 0;
bool isStreaming = false;

#endif

typedef struct
{
    httpd_req_t *req;
    size_t len;
} jpg_chunking_t;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char *_STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *_STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *_STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\nX-Timestamp: %d.%06d\r\n\r\n";

httpd_handle_t stream_httpd = NULL;
httpd_handle_t camera_httpd = NULL;

#if CONFIG_ESP_FACE_DETECT_ENABLED

static int8_t detection_enabled = 0;

// #if TWO_STAGE
// static HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
// static HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
// #else
// static HumanFaceDetectMSR01 s1(0.3F, 0.5F, 10, 0.2F);
// #endif

#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
static int8_t recognition_enabled = 0;
static int8_t is_enrolling = 0;

#if QUANT_TYPE
    // S16 model
    FaceRecognition112V1S16 recognizer;
#else
    // S8 model
    FaceRecognition112V1S8 recognizer;
#endif
#endif

#endif

typedef struct
{
    size_t size;  //number of values used for filtering
    size_t index; //current value index
    size_t count; //value count
    int sum;
    int *values; //array to be filled with values
} ra_filter_t;

static ra_filter_t ra_filter;

static ra_filter_t *ra_filter_init(ra_filter_t *filter, size_t sample_size)
{
    memset(filter, 0, sizeof(ra_filter_t));

    filter->values = (int *)malloc(sample_size * sizeof(int));
    if (!filter->values)
    {
        return NULL;
    }
    memset(filter->values, 0, sample_size * sizeof(int));

    filter->size = sample_size;
    return filter;
}

#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
static int ra_filter_run(ra_filter_t *filter, int value)
{
    if (!filter->values)
    {
        return value;
    }
    filter->sum -= filter->values[filter->index];
    filter->values[filter->index] = value;
    filter->sum += filter->values[filter->index];
    filter->index++;
    filter->index = filter->index % filter->size;
    if (filter->count < filter->size)
    {
        filter->count++;
    }
    return filter->sum / filter->count;
}
#endif

#if CONFIG_ESP_FACE_DETECT_ENABLED
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
static void rgb_print(fb_data_t *fb, uint32_t color, const char *str)
{
    fb_gfx_print(fb, (fb->width - (strlen(str) * 14)) / 2, 10, color, str);
}

static int rgb_printf(fb_data_t *fb, uint32_t color, const char *format, ...)
{
    char loc_buf[64];
    char *temp = loc_buf;
    int len;
    va_list arg;
    va_list copy;
    va_start(arg, format);
    va_copy(copy, arg);
    len = vsnprintf(loc_buf, sizeof(loc_buf), format, arg);
    va_end(copy);
    if (len >= sizeof(loc_buf))
    {
        temp = (char *)malloc(len + 1);
        if (temp == NULL)
        {
            return 0;
        }
    }
    vsnprintf(temp, len + 1, format, arg);
    va_end(arg);
    rgb_print(fb, color, temp);
    if (len > 64)
    {
        free(temp);
    }
    return len;
}
#endif
static void draw_face_boxes(fb_data_t *fb, std::list<dl::detect::result_t> *results, int face_id)
{
    int x, y, w, h;
    uint32_t color = FACE_COLOR_YELLOW;
    if (face_id < 0)
    {
        color = FACE_COLOR_RED;
    }
    else if (face_id > 0)
    {
        color = FACE_COLOR_GREEN;
    }
    if(fb->bytes_per_pixel == 2){
        //color = ((color >> 8) & 0xF800) | ((color >> 3) & 0x07E0) | (color & 0x001F);
        color = ((color >> 16) & 0x001F) | ((color >> 3) & 0x07E0) | ((color << 8) & 0xF800);
    }
    int i = 0;
    for (std::list<dl::detect::result_t>::iterator prediction = results->begin(); prediction != results->end(); prediction++, i++)
    {
        // rectangle box
        x = (int)prediction->box[0];
        y = (int)prediction->box[1];
        w = (int)prediction->box[2] - x + 1;
        h = (int)prediction->box[3] - y + 1;
        if((x + w) > fb->width){
            w = fb->width - x;
        }
        if((y + h) > fb->height){
            h = fb->height - y;
        }
        fb_gfx_drawFastHLine(fb, x, y, w, color);
        fb_gfx_drawFastHLine(fb, x, y + h - 1, w, color);
        fb_gfx_drawFastVLine(fb, x, y, h, color);
        fb_gfx_drawFastVLine(fb, x + w - 1, y, h, color);
#if TWO_STAGE
        // landmarks (left eye, mouth left, nose, right eye, mouth right)
        int x0, y0, j;
        for (j = 0; j < 10; j+=2) {
            x0 = (int)prediction->keypoint[j];
            y0 = (int)prediction->keypoint[j+1];
            fb_gfx_fillRect(fb, x0, y0, 3, 3, color);
        }
#endif
    }
}

#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
static int run_face_recognition(fb_data_t *fb, std::list<dl::detect::result_t> *results)
{
    std::vector<int> landmarks = results->front().keypoint;
    int id = -1;

    Tensor<uint8_t> tensor;
    tensor.set_element((uint8_t *)fb->data).set_shape({fb->height, fb->width, 3}).set_auto_free(false);

    int enrolled_count = recognizer.get_enrolled_id_num();

    if (enrolled_count < FACE_ID_SAVE_NUMBER && is_enrolling){
        id = recognizer.enroll_id(tensor, landmarks, "", true);
        log_i("Enrolled ID: %d", id);
        rgb_printf(fb, FACE_COLOR_CYAN, "ID[%u]", id);
    }

    face_info_t recognize = recognizer.recognize(tensor, landmarks);
    if(recognize.id >= 0){
        rgb_printf(fb, FACE_COLOR_GREEN, "ID[%u]: %.2f", recognize.id, recognize.similarity);
    } else {
        rgb_print(fb, FACE_COLOR_RED, "Intruder Alert!");
    }
    return recognize.id;
}
#endif
#endif

#if CONFIG_LED_ILLUMINATOR_ENABLED
void enable_led(bool en)
{ // Turn LED On or Off
    if (led_duty == 0) return; // Skip if LED flash is disabled
    int duty = en ? led_duty : 0;
    if (en && isStreaming && (led_duty > CONFIG_LED_MAX_INTENSITY))
    {
        duty = CONFIG_LED_MAX_INTENSITY;
    }
    ledcWrite(LED_LEDC_CHANNEL, duty);
    log_i("Set LED intensity to %d", duty);
}
#endif

static esp_err_t bmp_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
    uint64_t fr_start = esp_timer_get_time();
#endif
    fb = esp_camera_fb_get();
    if (!fb)
    {
        log_e("Camera capture failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "image/x-windows-bmp");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.bmp");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    char ts[32];
    snprintf(ts, 32, "%ld.%06ld", fb->timestamp.tv_sec, fb->timestamp.tv_usec);
    httpd_resp_set_hdr(req, "X-Timestamp", (const char *)ts);


    uint8_t * buf = NULL;
    size_t buf_len = 0;
    bool converted = frame2bmp(fb, &buf, &buf_len);
    esp_camera_fb_return(fb);
    if(!converted){
        log_e("BMP Conversion failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    res = httpd_resp_send(req, (const char *)buf, buf_len);
    free(buf);
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
    uint64_t fr_end = esp_timer_get_time();
#endif
    log_i("BMP: %llums, %uB", (uint64_t)((fr_end - fr_start) / 1000), buf_len);
    return res;
}

static size_t jpg_encode_stream(void *arg, size_t index, const void *data, size_t len)
{
    jpg_chunking_t *j = (jpg_chunking_t *)arg;
    if (!index)
    {
        j->len = 0;
    }
    if (httpd_resp_send_chunk(j->req, (const char *)data, len) != ESP_OK)
    {
        return 0;
    }
    j->len += len;
    return len;
}

static esp_err_t capture_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
    int64_t fr_start = esp_timer_get_time();
#endif

#if CONFIG_LED_ILLUMINATOR_ENABLED
    enable_led(true);
    vTaskDelay(150 / portTICK_PERIOD_MS); // The LED needs to be turned on ~150ms before the call to esp_camera_fb_get()
    fb = esp_camera_fb_get();             // or it won't be visible in the frame. A better way to do this is needed.
    enable_led(false);
#else
    fb = esp_camera_fb_get();
#endif

    if (!fb)
    {
        log_e("Camera capture failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    char ts[32];
    snprintf(ts, 32, "%ld.%06ld", fb->timestamp.tv_sec, fb->timestamp.tv_usec);
    httpd_resp_set_hdr(req, "X-Timestamp", (const char *)ts);

#if CONFIG_ESP_FACE_DETECT_ENABLED
    size_t out_len, out_width, out_height;
    uint8_t *out_buf;
    bool s;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
    bool detected = false;
#endif
    int face_id = 0;
    if (!detection_enabled || fb->width > 400)
    {
#endif
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        size_t fb_len = 0;
#endif
        if (fb->format == PIXFORMAT_JPEG)
        {
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
            fb_len = fb->len;
#endif
            res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
        }
        else
        {
            jpg_chunking_t jchunk = {req, 0};
            res = frame2jpg_cb(fb, 80, jpg_encode_stream, &jchunk) ? ESP_OK : ESP_FAIL;
            httpd_resp_send_chunk(req, NULL, 0);
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
            fb_len = jchunk.len;
#endif
        }
        esp_camera_fb_return(fb);
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        int64_t fr_end = esp_timer_get_time();
#endif
        log_i("JPG: %uB %ums", (uint32_t)(fb_len), (uint32_t)((fr_end - fr_start) / 1000));
        return res;
#if CONFIG_ESP_FACE_DETECT_ENABLED
    }

    jpg_chunking_t jchunk = {req, 0};

    if (fb->format == PIXFORMAT_RGB565
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
     && !recognition_enabled
#endif
     ){
#if TWO_STAGE
        HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
        HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
        std::list<dl::detect::result_t> &candidates = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
        std::list<dl::detect::result_t> &results = s2.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3}, candidates);
#else
        HumanFaceDetectMSR01 s1(0.3F, 0.5F, 10, 0.2F);
        std::list<dl::detect::result_t> &results = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
#endif
        if (results.size() > 0) {
            fb_data_t rfb;
            rfb.width = fb->width;
            rfb.height = fb->height;
            rfb.data = fb->buf;
            rfb.bytes_per_pixel = 2;
            rfb.format = FB_RGB565;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
            detected = true;
#endif
            draw_face_boxes(&rfb, &results, face_id);
        }
        s = fmt2jpg_cb(fb->buf, fb->len, fb->width, fb->height, PIXFORMAT_RGB565, 90, jpg_encode_stream, &jchunk);
        esp_camera_fb_return(fb);
    } else
    {
        out_len = fb->width * fb->height * 3;
        out_width = fb->width;
        out_height = fb->height;
        out_buf = (uint8_t*)malloc(out_len);
        if (!out_buf) {
            log_e("out_buf malloc failed");
            httpd_resp_send_500(req);
            return ESP_FAIL;
        }
        s = fmt2rgb888(fb->buf, fb->len, fb->format, out_buf);
        esp_camera_fb_return(fb);
        if (!s) {
            free(out_buf);
            log_e("To rgb888 failed");
            httpd_resp_send_500(req);
            return ESP_FAIL;
        }

        fb_data_t rfb;
        rfb.width = out_width;
        rfb.height = out_height;
        rfb.data = out_buf;
        rfb.bytes_per_pixel = 3;
        rfb.format = FB_BGR888;

#if TWO_STAGE
        HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
        HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
        std::list<dl::detect::result_t> &candidates = s1.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3});
        std::list<dl::detect::result_t> &results = s2.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3}, candidates);
#else
        HumanFaceDetectMSR01 s1(0.3F, 0.5F, 10, 0.2F);
        std::list<dl::detect::result_t> &results = s1.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3});
#endif

        if (results.size() > 0) {
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
            detected = true;
#endif
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
            if (recognition_enabled) {
                face_id = run_face_recognition(&rfb, &results);
            }
#endif
            draw_face_boxes(&rfb, &results, face_id);
        }

        s = fmt2jpg_cb(out_buf, out_len, out_width, out_height, PIXFORMAT_RGB888, 90, jpg_encode_stream, &jchunk);
        free(out_buf);
    }

    if (!s) {
        log_e("JPEG compression failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
    int64_t fr_end = esp_timer_get_time();
#endif
    log_i("FACE: %uB %ums %s%d", (uint32_t)(jchunk.len), (uint32_t)((fr_end - fr_start) / 1000), detected ? "DETECTED " : "", face_id);
    return res;
#endif
}

static esp_err_t stream_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    struct timeval _timestamp;
    esp_err_t res = ESP_OK;
    size_t _jpg_buf_len = 0;
    uint8_t *_jpg_buf = NULL;
    char *part_buf[128];
#if CONFIG_ESP_FACE_DETECT_ENABLED
    #if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        bool detected = false;
        int64_t fr_ready = 0;
        int64_t fr_recognize = 0;
        int64_t fr_encode = 0;
        int64_t fr_face = 0;
        int64_t fr_start = 0;
    #endif
    int face_id = 0;
    size_t out_len = 0, out_width = 0, out_height = 0;
    uint8_t *out_buf = NULL;
    bool s = false;
#if TWO_STAGE
    HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
    HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
#else
    HumanFaceDetectMSR01 s1(0.3F, 0.5F, 10, 0.2F);
#endif
#endif

    static int64_t last_frame = 0;
    if (!last_frame)
    {
        last_frame = esp_timer_get_time();
    }

    res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
    if (res != ESP_OK)
    {
        return res;
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "X-Framerate", "60");

#if CONFIG_LED_ILLUMINATOR_ENABLED
    isStreaming = true;
    enable_led(true);
#endif

    while (true)
    {
#if CONFIG_ESP_FACE_DETECT_ENABLED
    #if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        detected = false;
    #endif
        face_id = 0;
#endif

        fb = esp_camera_fb_get();
        if (!fb)
        {
            log_e("Camera capture failed");
            res = ESP_FAIL;
        }
        else
        {
            _timestamp.tv_sec = fb->timestamp.tv_sec;
            _timestamp.tv_usec = fb->timestamp.tv_usec;
#if CONFIG_ESP_FACE_DETECT_ENABLED
    #if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
            fr_start = esp_timer_get_time();
            fr_ready = fr_start;
            fr_encode = fr_start;
            fr_recognize = fr_start;
            fr_face = fr_start;
    #endif
            if (!detection_enabled || fb->width > 400)
            {
#endif
                if (fb->format != PIXFORMAT_JPEG)
                {
                    bool jpeg_converted = frame2jpg(fb, 80, &_jpg_buf, &_jpg_buf_len);
                    esp_camera_fb_return(fb);
                    fb = NULL;
                    if (!jpeg_converted)
                    {
                        log_e("JPEG compression failed");
                        res = ESP_FAIL;
                    }
                }
                else
                {
                    _jpg_buf_len = fb->len;
                    _jpg_buf = fb->buf;
                }
#if CONFIG_ESP_FACE_DETECT_ENABLED
            }
            else
            {
                if (fb->format == PIXFORMAT_RGB565
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
                    && !recognition_enabled
#endif
                ){
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                    fr_ready = esp_timer_get_time();
#endif
#if TWO_STAGE
                    std::list<dl::detect::result_t> &candidates = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
                    std::list<dl::detect::result_t> &results = s2.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3}, candidates);
#else
                    std::list<dl::detect::result_t> &results = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
#endif
#if CONFIG_ESP_FACE_DETECT_ENABLED && ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                    fr_face = esp_timer_get_time();
                    fr_recognize = fr_face;
#endif
                    if (results.size() > 0) {
                        fb_data_t rfb;
                        rfb.width = fb->width;
                        rfb.height = fb->height;
                        rfb.data = fb->buf;
                        rfb.bytes_per_pixel = 2;
                        rfb.format = FB_RGB565;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                        detected = true;
#endif
                        draw_face_boxes(&rfb, &results, face_id);
                    }
                    s = fmt2jpg(fb->buf, fb->len, fb->width, fb->height, PIXFORMAT_RGB565, 80, &_jpg_buf, &_jpg_buf_len);
                    esp_camera_fb_return(fb);
                    fb = NULL;
                    if (!s) {
                        log_e("fmt2jpg failed");
                        res = ESP_FAIL;
                    }
#if CONFIG_ESP_FACE_DETECT_ENABLED && ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                    fr_encode = esp_timer_get_time();
#endif
                } else
                {
                    out_len = fb->width * fb->height * 3;
                    out_width = fb->width;
                    out_height = fb->height;
                    out_buf = (uint8_t*)malloc(out_len);
                    if (!out_buf) {
                        log_e("out_buf malloc failed");
                        res = ESP_FAIL;
                    } else {
                        s = fmt2rgb888(fb->buf, fb->len, fb->format, out_buf);
                        esp_camera_fb_return(fb);
                        fb = NULL;
                        if (!s) {
                            free(out_buf);
                            log_e("To rgb888 failed");
                            res = ESP_FAIL;
                        } else {
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                            fr_ready = esp_timer_get_time();
#endif

                            fb_data_t rfb;
                            rfb.width = out_width;
                            rfb.height = out_height;
                            rfb.data = out_buf;
                            rfb.bytes_per_pixel = 3;
                            rfb.format = FB_BGR888;

#if TWO_STAGE
                            std::list<dl::detect::result_t> &candidates = s1.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3});
                            std::list<dl::detect::result_t> &results = s2.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3}, candidates);
#else
                            std::list<dl::detect::result_t> &results = s1.infer((uint8_t *)out_buf, {(int)out_height, (int)out_width, 3});
#endif

#if CONFIG_ESP_FACE_DETECT_ENABLED && ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                            fr_face = esp_timer_get_time();
                            fr_recognize = fr_face;
#endif

                            if (results.size() > 0) {
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                                detected = true;
#endif
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
                                if (recognition_enabled) {
                                    face_id = run_face_recognition(&rfb, &results);
    #if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                                    fr_recognize = esp_timer_get_time();
    #endif
                                }
#endif
                                draw_face_boxes(&rfb, &results, face_id);
                            }
                            s = fmt2jpg(out_buf, out_len, out_width, out_height, PIXFORMAT_RGB888, 90, &_jpg_buf, &_jpg_buf_len);
                            free(out_buf);
                            if (!s) {
                                log_e("fmt2jpg failed");
                                res = ESP_FAIL;
                            }
#if CONFIG_ESP_FACE_DETECT_ENABLED && ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
                            fr_encode = esp_timer_get_time();
#endif
                        }
                    }
                }
            }
#endif
        }
        if (res == ESP_OK)
        {
            res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
        }
        if (res == ESP_OK)
        {
            size_t hlen = snprintf((char *)part_buf, 128, _STREAM_PART, _jpg_buf_len, _timestamp.tv_sec, _timestamp.tv_usec);
            res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
        }
        if (res == ESP_OK)
        {
            res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);
        }
        if (fb)
        {
            esp_camera_fb_return(fb);
            fb = NULL;
            _jpg_buf = NULL;
        }
        else if (_jpg_buf)
        {
            free(_jpg_buf);
            _jpg_buf = NULL;
        }
        if (res != ESP_OK)
        {
            log_e("Send frame failed");
            break;
        }
        int64_t fr_end = esp_timer_get_time();

#if CONFIG_ESP_FACE_DETECT_ENABLED && ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        int64_t ready_time = (fr_ready - fr_start) / 1000;
        int64_t face_time = (fr_face - fr_ready) / 1000;
        int64_t recognize_time = (fr_recognize - fr_face) / 1000;
        int64_t encode_time = (fr_encode - fr_recognize) / 1000;
        int64_t process_time = (fr_encode - fr_start) / 1000;
#endif

        int64_t frame_time = fr_end - last_frame;
        frame_time /= 1000;
#if ARDUHAL_LOG_LEVEL >= ARDUHAL_LOG_LEVEL_INFO
        uint32_t avg_frame_time = ra_filter_run(&ra_filter, frame_time);
#endif
        log_i("MJPG: %uB %ums (%.1ffps), AVG: %ums (%.1ffps)"
#if CONFIG_ESP_FACE_DETECT_ENABLED
                      ", %u+%u+%u+%u=%u %s%d"
#endif
                 ,
                 (uint32_t)(_jpg_buf_len),
                 (uint32_t)frame_time, 1000.0 / (uint32_t)frame_time,
                 avg_frame_time, 1000.0 / avg_frame_time
#if CONFIG_ESP_FACE_DETECT_ENABLED
                 ,
                 (uint32_t)ready_time, (uint32_t)face_time, (uint32_t)recognize_time, (uint32_t)encode_time, (uint32_t)process_time,
                 (detected) ? "DETECTED " : "", face_id
#endif
        );
    }

#if CONFIG_LED_ILLUMINATOR_ENABLED
    isStreaming = false;
    enable_led(false);
#endif

    return res;
}

static esp_err_t parse_get(httpd_req_t *req, char **obuf)
{
    char *buf = NULL;
    size_t buf_len = 0;

    buf_len = httpd_req_get_url_query_len(req) + 1;
    if (buf_len > 1) {
        buf = (char *)malloc(buf_len);
        if (!buf) {
            httpd_resp_send_500(req);
            return ESP_FAIL;
        }
        if (httpd_req_get_url_query_str(req, buf, buf_len) == ESP_OK) {
            *obuf = buf;
            return ESP_OK;
        }
        free(buf);
    }
    httpd_resp_send_404(req);
    return ESP_FAIL;
}

static esp_err_t cmd_handler(httpd_req_t *req)
{
    char *buf = NULL;
    char variable[32];
    char value[32];

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_query_key_value(buf, "var", variable, sizeof(variable)) != ESP_OK ||
        httpd_query_key_value(buf, "val", value, sizeof(value)) != ESP_OK) {
        free(buf);
        httpd_resp_send_404(req);
        return ESP_FAIL;
    }
    free(buf);

    int val = atoi(value);
    log_i("%s = %d", variable, val);
    sensor_t *s = esp_camera_sensor_get();
    int res = 0;

    if (!strcmp(variable, "framesize")) {
        if (s->pixformat == PIXFORMAT_JPEG) {
            res = s->set_framesize(s, (framesize_t)val);
        }
    }
    else if (!strcmp(variable, "quality"))
        res = s->set_quality(s, val);
    else if (!strcmp(variable, "contrast"))
        res = s->set_contrast(s, val);
    else if (!strcmp(variable, "brightness"))
        res = s->set_brightness(s, val);
    else if (!strcmp(variable, "saturation"))
        res = s->set_saturation(s, val);
    else if (!strcmp(variable, "gainceiling"))
        res = s->set_gainceiling(s, (gainceiling_t)val);
    else if (!strcmp(variable, "colorbar"))
        res = s->set_colorbar(s, val);
    else if (!strcmp(variable, "awb"))
        res = s->set_whitebal(s, val);
    else if (!strcmp(variable, "agc"))
        res = s->set_gain_ctrl(s, val);
    else if (!strcmp(variable, "aec"))
        res = s->set_exposure_ctrl(s, val);
    else if (!strcmp(variable, "hmirror"))
        res = s->set_hmirror(s, val);
    else if (!strcmp(variable, "vflip"))
        res = s->set_vflip(s, val);
    else if (!strcmp(variable, "awb_gain"))
        res = s->set_awb_gain(s, val);
    else if (!strcmp(variable, "agc_gain"))
        res = s->set_agc_gain(s, val);
    else if (!strcmp(variable, "aec_value"))
        res = s->set_aec_value(s, val);
    else if (!strcmp(variable, "aec2"))
        res = s->set_aec2(s, val);
    else if (!strcmp(variable, "dcw"))
        res = s->set_dcw(s, val);
    else if (!strcmp(variable, "bpc"))
        res = s->set_bpc(s, val);
    else if (!strcmp(variable, "wpc"))
        res = s->set_wpc(s, val);
    else if (!strcmp(variable, "raw_gma"))
        res = s->set_raw_gma(s, val);
    else if (!strcmp(variable, "lenc"))
        res = s->set_lenc(s, val);
    else if (!strcmp(variable, "special_effect"))
        res = s->set_special_effect(s, val);
    else if (!strcmp(variable, "wb_mode"))
        res = s->set_wb_mode(s, val);
    else if (!strcmp(variable, "ae_level"))
        res = s->set_ae_level(s, val);
#if CONFIG_LED_ILLUMINATOR_ENABLED
    else if (!strcmp(variable, "led_intensity")) {
        led_duty = val;
        if (isStreaming)
            enable_led(true);
    }
#endif

#if CONFIG_ESP_FACE_DETECT_ENABLED
    else if (!strcmp(variable, "face_detect")) {
        detection_enabled = val;
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
        if (!detection_enabled) {
            recognition_enabled = 0;
        }
#endif
    }
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
    else if (!strcmp(variable, "face_enroll")){
        is_enrolling = !is_enrolling;
        log_i("Enrolling: %s", is_enrolling?"true":"false");
    }
    else if (!strcmp(variable, "face_recognize")) {
        recognition_enabled = val;
        if (recognition_enabled) {
            detection_enabled = val;
        }
    }
#endif
#endif
    else {
        log_i("Unknown command: %s", variable);
        res = -1;
    }

    if (res < 0) {
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, NULL, 0);
}

static int print_reg(char * p, sensor_t * s, uint16_t reg, uint32_t mask){
    return sprintf(p, "\"0x%x\":%u,", reg, s->get_reg(s, reg, mask));
}

static esp_err_t status_handler(httpd_req_t *req)
{
    static char json_response[1024];

    sensor_t *s = esp_camera_sensor_get();
    char *p = json_response;
    *p++ = '{';

    if(s->id.PID == OV5640_PID || s->id.PID == OV3660_PID){
        for(int reg = 0x3400; reg < 0x3406; reg+=2){
            p+=print_reg(p, s, reg, 0xFFF);//12 bit
        }
        p+=print_reg(p, s, 0x3406, 0xFF);

        p+=print_reg(p, s, 0x3500, 0xFFFF0);//16 bit
        p+=print_reg(p, s, 0x3503, 0xFF);
        p+=print_reg(p, s, 0x350a, 0x3FF);//10 bit
        p+=print_reg(p, s, 0x350c, 0xFFFF);//16 bit

        for(int reg = 0x5480; reg <= 0x5490; reg++){
            p+=print_reg(p, s, reg, 0xFF);
        }

        for(int reg = 0x5380; reg <= 0x538b; reg++){
            p+=print_reg(p, s, reg, 0xFF);
        }

        for(int reg = 0x5580; reg < 0x558a; reg++){
            p+=print_reg(p, s, reg, 0xFF);
        }
        p+=print_reg(p, s, 0x558a, 0x1FF);//9 bit
    } else if(s->id.PID == OV2640_PID){
        p+=print_reg(p, s, 0xd3, 0xFF);
        p+=print_reg(p, s, 0x111, 0xFF);
        p+=print_reg(p, s, 0x132, 0xFF);
    }

    p += sprintf(p, "\"xclk\":%u,", s->xclk_freq_hz / 1000000);
    p += sprintf(p, "\"pixformat\":%u,", s->pixformat);
    p += sprintf(p, "\"framesize\":%u,", s->status.framesize);
    p += sprintf(p, "\"quality\":%u,", s->status.quality);
    p += sprintf(p, "\"brightness\":%d,", s->status.brightness);
    p += sprintf(p, "\"contrast\":%d,", s->status.contrast);
    p += sprintf(p, "\"saturation\":%d,", s->status.saturation);
    p += sprintf(p, "\"sharpness\":%d,", s->status.sharpness);
    p += sprintf(p, "\"special_effect\":%u,", s->status.special_effect);
    p += sprintf(p, "\"wb_mode\":%u,", s->status.wb_mode);
    p += sprintf(p, "\"awb\":%u,", s->status.awb);
    p += sprintf(p, "\"awb_gain\":%u,", s->status.awb_gain);
    p += sprintf(p, "\"aec\":%u,", s->status.aec);
    p += sprintf(p, "\"aec2\":%u,", s->status.aec2);
    p += sprintf(p, "\"ae_level\":%d,", s->status.ae_level);
    p += sprintf(p, "\"aec_value\":%u,", s->status.aec_value);
    p += sprintf(p, "\"agc\":%u,", s->status.agc);
    p += sprintf(p, "\"agc_gain\":%u,", s->status.agc_gain);
    p += sprintf(p, "\"gainceiling\":%u,", s->status.gainceiling);
    p += sprintf(p, "\"bpc\":%u,", s->status.bpc);
    p += sprintf(p, "\"wpc\":%u,", s->status.wpc);
    p += sprintf(p, "\"raw_gma\":%u,", s->status.raw_gma);
    p += sprintf(p, "\"lenc\":%u,", s->status.lenc);
    p += sprintf(p, "\"hmirror\":%u,", s->status.hmirror);
    p += sprintf(p, "\"dcw\":%u,", s->status.dcw);
    p += sprintf(p, "\"colorbar\":%u", s->status.colorbar);
#if CONFIG_LED_ILLUMINATOR_ENABLED
    p += sprintf(p, ",\"led_intensity\":%u", led_duty);
#else
    p += sprintf(p, ",\"led_intensity\":%d", -1);
#endif
#if CONFIG_ESP_FACE_DETECT_ENABLED
    p += sprintf(p, ",\"face_detect\":%u", detection_enabled);
#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
    p += sprintf(p, ",\"face_enroll\":%u,", is_enrolling);
    p += sprintf(p, "\"face_recognize\":%u", recognition_enabled);
#endif
#endif
    *p++ = '}';
    *p++ = 0;
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, json_response, strlen(json_response));
}

static esp_err_t xclk_handler(httpd_req_t *req)
{
    char *buf = NULL;
    char _xclk[32];

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_query_key_value(buf, "xclk", _xclk, sizeof(_xclk)) != ESP_OK) {
        free(buf);
        httpd_resp_send_404(req);
        return ESP_FAIL;
    }
    free(buf);

    int xclk = atoi(_xclk);
    log_i("Set XCLK: %d MHz", xclk);

    sensor_t *s = esp_camera_sensor_get();
    int res = s->set_xclk(s, LEDC_TIMER_0, xclk);
    if (res) {
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, NULL, 0);
}

static esp_err_t reg_handler(httpd_req_t *req)
{
    char *buf = NULL;
    char _reg[32];
    char _mask[32];
    char _val[32];

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_query_key_value(buf, "reg", _reg, sizeof(_reg)) != ESP_OK ||
        httpd_query_key_value(buf, "mask", _mask, sizeof(_mask)) != ESP_OK ||
        httpd_query_key_value(buf, "val", _val, sizeof(_val)) != ESP_OK) {
        free(buf);
        httpd_resp_send_404(req);
        return ESP_FAIL;
    }
    free(buf);

    int reg = atoi(_reg);
    int mask = atoi(_mask);
    int val = atoi(_val);
    log_i("Set Register: reg: 0x%02x, mask: 0x%02x, value: 0x%02x", reg, mask, val);

    sensor_t *s = esp_camera_sensor_get();
    int res = s->set_reg(s, reg, mask, val);
    if (res) {
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, NULL, 0);
}

static esp_err_t greg_handler(httpd_req_t *req)
{
    char *buf = NULL;
    char _reg[32];
    char _mask[32];

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_query_key_value(buf, "reg", _reg, sizeof(_reg)) != ESP_OK ||
        httpd_query_key_value(buf, "mask", _mask, sizeof(_mask)) != ESP_OK) {
        free(buf);
        httpd_resp_send_404(req);
        return ESP_FAIL;
    }
    free(buf);

    int reg = atoi(_reg);
    int mask = atoi(_mask);
    sensor_t *s = esp_camera_sensor_get();
    int res = s->get_reg(s, reg, mask);
    if (res < 0) {
        return httpd_resp_send_500(req);
    }
    log_i("Get Register: reg: 0x%02x, mask: 0x%02x, value: 0x%02x", reg, mask, res);

    char buffer[20];
    const char * val = itoa(res, buffer, 10);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, val, strlen(val));
}

static int parse_get_var(char *buf, const char * key, int def)
{
    char _int[16];
    if(httpd_query_key_value(buf, key, _int, sizeof(_int)) != ESP_OK){
        return def;
    }
    return atoi(_int);
}

static esp_err_t pll_handler(httpd_req_t *req)
{
    char *buf = NULL;

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }

    int bypass = parse_get_var(buf, "bypass", 0);
    int mul = parse_get_var(buf, "mul", 0);
    int sys = parse_get_var(buf, "sys", 0);
    int root = parse_get_var(buf, "root", 0);
    int pre = parse_get_var(buf, "pre", 0);
    int seld5 = parse_get_var(buf, "seld5", 0);
    int pclken = parse_get_var(buf, "pclken", 0);
    int pclk = parse_get_var(buf, "pclk", 0);
    free(buf);

    log_i("Set Pll: bypass: %d, mul: %d, sys: %d, root: %d, pre: %d, seld5: %d, pclken: %d, pclk: %d", bypass, mul, sys, root, pre, seld5, pclken, pclk);
    sensor_t *s = esp_camera_sensor_get();
    int res = s->set_pll(s, bypass, mul, sys, root, pre, seld5, pclken, pclk);
    if (res) {
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, NULL, 0);
}

static esp_err_t win_handler(httpd_req_t *req)
{
    char *buf = NULL;

    if (parse_get(req, &buf) != ESP_OK) {
        return ESP_FAIL;
    }

    int startX = parse_get_var(buf, "sx", 0);
    int startY = parse_get_var(buf, "sy", 0);
    int endX = parse_get_var(buf, "ex", 0);
    int endY = parse_get_var(buf, "ey", 0);
    int offsetX = parse_get_var(buf, "offx", 0);
    int offsetY = parse_get_var(buf, "offy", 0);
    int totalX = parse_get_var(buf, "tx", 0);
    int totalY = parse_get_var(buf, "ty", 0);
    int outputX = parse_get_var(buf, "ox", 0);
    int outputY = parse_get_var(buf, "oy", 0);
    bool scale = parse_get_var(buf, "scale", 0) == 1;
    bool binning = parse_get_var(buf, "binning", 0) == 1;
    free(buf);

    log_i("Set Window: Start: %d %d, End: %d %d, Offset: %d %d, Total: %d %d, Output: %d %d, Scale: %u, Binning: %u", startX, startY, endX, endY, offsetX, offsetY, totalX, totalY, outputX, outputY, scale, binning);
    sensor_t *s = esp_camera_sensor_get();
    int res = s->set_res_raw(s, startX, startY, endX, endY, offsetX, offsetY, totalX, totalY, outputX, outputY, scale, binning);
    if (res) {
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, NULL, 0);
}

static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Ergonomics & Head Pose Monitor</title>
    <style>
        :root {
            --bg-primary: #0a0f1d;
            --bg-card: #131b2e;
            --bg-card-hover: #1a253c;
            --bg-glass: rgba(19, 27, 46, 0.85);
            --border-color: #1e293b;
            --border-glow: #38bdf8;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-cyan: #38bdf8;
            --accent-blue: #0284c7;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-rose: #f43f5e;
            --accent-purple: #818cf8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; }
        body { background: var(--bg-primary); color: var(--text-main); min-height: 100vh; padding: 16px; display: flex; flex-direction: column; align-items: center; }
        
        /* Header */
        header { width: 100%; max-width: 1280px; display: flex; justify-content: space-between; align-items: center; padding: 14px 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); backdrop-filter: blur(8px); flex-wrap: wrap; gap: 12px; }
        .logo-area { display: flex; align-items: center; gap: 12px; }
        .logo-icon { width: 38px; height: 38px; background: linear-gradient(135deg, #0284c7, #818cf8); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 20px; box-shadow: 0 0 15px rgba(56, 189, 248, 0.4); }
        .header-title h1 { font-size: 1.35rem; font-weight: 800; background: linear-gradient(135deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header-title p { font-size: 0.82rem; color: var(--text-muted); }
        .header-badges { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .badge { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; background: rgba(30, 41, 59, 0.8); border: 1px solid #334155; }
        .badge-dot { width: 8px; height: 8px; border-radius: 50%; }
        .badge-green .badge-dot { background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
        .badge-amber .badge-dot { background: var(--accent-amber); box-shadow: 0 0 8px var(--accent-amber); }
        .badge-red .badge-dot { background: var(--accent-rose); box-shadow: 0 0 8px var(--accent-rose); }
        .btn-audio { background: #1e293b; border: 1px solid #334155; color: var(--text-main); padding: 6px 14px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 6px; transition: all 0.2s; }
        .btn-audio.active { background: #0284c7; border-color: #38bdf8; box-shadow: 0 0 12px rgba(56, 189, 248, 0.3); }

        /* Main Grid */
        .dashboard-grid { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20px; max-width: 1280px; width: 100%; }
        @media (max-width: 960px) { .dashboard-grid { grid-template-columns: 1fr; } }

        /* Left Column */
        .left-col { display: flex; flex-direction: column; gap: 20px; }
        .right-col { display: flex; flex-direction: column; gap: 20px; }

        /* Cards */
        .card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 18px; box-shadow: 0 4px 20px rgba(0,0,0,0.25); transition: border-color 0.2s; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; border-bottom: 1px solid #1e293b; padding-bottom: 10px; }
        .card-title { font-size: 1.05rem; font-weight: 700; color: var(--accent-cyan); display: flex; align-items: center; gap: 8px; }

        /* Stream Container */
        .video-box { position: relative; width: 100%; aspect-ratio: 4/3; background: #050811; border-radius: 12px; overflow: hidden; display: flex; align-items: center; justify-content: center; border: 1px solid #1e293b; }
        .video-box img { width: 100%; height: 100%; object-fit: contain; }
        #axis-canvas { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 5; }
        .video-hud { position: absolute; top: 12px; left: 12px; right: 12px; display: flex; justify-content: space-between; pointer-events: none; z-index: 10; }
        .hud-pill { background: rgba(15, 23, 42, 0.88); backdrop-filter: blur(6px); border: 1px solid rgba(255,255,255,0.15); padding: 5px 12px; border-radius: 8px; font-size: 0.78rem; font-weight: 600; }
        
        .stream-controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-top: 14px; }
        .btn { padding: 10px 14px; border-radius: 10px; border: none; font-weight: 600; cursor: pointer; transition: all 0.2s; font-size: 0.88rem; text-align: center; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
        .btn-primary { background: linear-gradient(135deg, #0284c7, #0369a1); color: white; box-shadow: 0 2px 10px rgba(2, 132, 199, 0.3); }
        .btn-primary:hover { background: #0284c7; transform: translateY(-1px); }
        .btn-calibrate { background: linear-gradient(135deg, #f59e0b, #d97706); color: #0f172a; font-weight: 800; box-shadow: 0 2px 12px rgba(245, 158, 11, 0.4); border: 1px solid #fde68a; }
        .btn-calibrate:hover { background: #fbbf24; transform: translateY(-1px); box-shadow: 0 4px 18px rgba(245, 158, 11, 0.6); }
        .btn-calibrate.btn-calibrated { background: linear-gradient(135deg, #10b981, #059669); color: white; border-color: #6ee7b7; box-shadow: 0 2px 12px rgba(16, 185, 129, 0.4); }
        .btn-secondary { background: #1e293b; color: var(--text-main); border: 1px solid #334155; }
        .btn-secondary:hover { background: #334155; }

        /* Toast notification */
        #toast { position: fixed; bottom: 25px; right: 25px; background: rgba(15, 23, 42, 0.95); border: 1px solid var(--accent-cyan); color: #fff; padding: 12px 20px; border-radius: 12px; font-size: 0.9rem; font-weight: 600; box-shadow: 0 8px 30px rgba(0,0,0,0.5); z-index: 1000; transform: translateY(100px); opacity: 0; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); pointer-events: none; }
        #toast.show { transform: translateY(0); opacity: 1; }

        /* Warning & Alert Banner */
        .status-banner { border-radius: 14px; padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; gap: 14px; font-weight: 700; transition: all 0.3s; border: 2px solid transparent; }
        .status-banner.status-good { background: rgba(16, 185, 129, 0.12); border-color: var(--accent-green); color: #34d399; }
        .status-banner.status-caution { background: rgba(245, 158, 11, 0.15); border-color: var(--accent-amber); color: #fbbf24; }
        .status-banner.status-danger { background: rgba(244, 63, 94, 0.2); border-color: var(--accent-rose); color: #fb7185; animation: pulseGlow 1.5s infinite; }
        @keyframes pulseGlow { 0%, 100% { box-shadow: 0 0 15px rgba(244, 63, 94, 0.3); } 50% { box-shadow: 0 0 28px rgba(244, 63, 94, 0.65); } }
        .status-text-main { font-size: 1.15rem; }
        .status-desc { font-size: 0.82rem; font-weight: 500; opacity: 0.9; margin-top: 3px; }

        /* Active Alerts List */
        .alert-list { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
        .alert-item { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 10px; background: rgba(30, 41, 59, 0.7); border-left: 4px solid var(--accent-amber); font-size: 0.88rem; }
        .alert-item.danger { border-left-color: var(--accent-rose); background: rgba(244, 63, 94, 0.1); }
        .alert-item.safe { border-left-color: var(--accent-green); background: rgba(16, 185, 129, 0.08); }
        .alert-icon { font-size: 1.2rem; line-height: 1; margin-top: 2px; }
        .alert-content strong { display: block; margin-bottom: 2px; font-weight: 700; color: #fff; }
        .alert-content span { font-size: 0.82rem; color: var(--text-muted); }

        /* Head Pose Angle Grid */
        .angles-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
        @media (max-width: 600px) { .angles-grid { grid-template-columns: 1fr; } }
        .angle-card { background: rgba(15, 23, 42, 0.6); border: 1px solid #1e293b; border-radius: 12px; padding: 14px; display: flex; flex-direction: column; align-items: center; text-align: center; position: relative; overflow: hidden; }
        .angle-label { font-size: 0.82rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        .angle-value { font-size: 1.9rem; font-weight: 800; margin: 6px 0; font-variant-numeric: tabular-nums; }
        .angle-status { font-size: 0.75rem; padding: 3px 8px; border-radius: 6px; font-weight: 700; }
        
        /* Attitude Visualizers */
        .pitch-meter-box { width: 100%; height: 38px; background: #0f172a; border-radius: 6px; margin-top: 8px; position: relative; overflow: hidden; border: 1px solid #334155; }
        .pitch-zero-line { position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; background: rgba(255,255,255,0.4); z-index: 2; }
        .pitch-safe-zone { position: absolute; top: 0; bottom: 0; left: 33.3%; right: 33.3%; background: rgba(16, 185, 129, 0.15); }
        .pitch-indicator-bar { position: absolute; top: 6px; bottom: 6px; width: 6px; border-radius: 3px; background: var(--accent-cyan); left: 50%; transform: translateX(-50%); transition: left 0.15s ease-out, background 0.2s; }
        
        .roll-horizon-box { width: 100%; height: 38px; background: #0f172a; border-radius: 6px; margin-top: 8px; position: relative; display: flex; align-items: center; justify-content: center; border: 1px solid #334155; overflow: hidden; }
        .roll-line { width: 70%; height: 3px; background: var(--accent-cyan); border-radius: 2px; transform: rotate(0deg); transition: transform 0.15s ease-out, background 0.2s; }
        .roll-center-dot { position: absolute; width: 6px; height: 6px; border-radius: 50%; background: #fff; }

        /* 3D Posture Model Visualizer */
        .attitude-3d-card { background: rgba(15, 23, 42, 0.7); border: 1px solid #1e293b; border-radius: 12px; padding: 14px; display: flex; align-items: center; justify-content: space-around; margin-top: 14px; }
        .head-model-container { perspective: 400px; width: 110px; height: 110px; display: flex; align-items: center; justify-content: center; }
        .head-3d-box { width: 70px; height: 80px; background: linear-gradient(135deg, #1e293b, #334155); border: 2px solid var(--accent-cyan); border-radius: 20px 20px 24px 24px; position: relative; transition: transform 0.15s ease-out; transform-style: preserve-3d; display: flex; flex-direction: column; align-items: center; justify-content: center; box-shadow: 0 0 20px rgba(56, 189, 248, 0.2); }
        .head-eyes { display: flex; gap: 14px; margin-top: 5px; }
        .head-eye { width: 8px; height: 8px; background: var(--accent-cyan); border-radius: 50%; box-shadow: 0 0 6px var(--accent-cyan); }
        .head-nose { width: 4px; height: 12px; background: #f43f5e; border-radius: 2px; margin-top: 4px; box-shadow: 0 0 6px #f43f5e; }
        .head-mouth { width: 18px; height: 3px; background: #64748b; border-radius: 2px; margin-top: 6px; }

        /* Sensors & Biometrics Grid */
        .sensors-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .sensor-card { background: rgba(15, 23, 42, 0.6); border: 1px solid #1e293b; border-radius: 12px; padding: 12px; }
        .sensor-header { display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px; }
        .sensor-val { font-size: 1.4rem; font-weight: 800; color: #fff; font-variant-numeric: tabular-nums; }
        .sensor-unit { font-size: 0.82rem; font-weight: 500; color: var(--text-muted); margin-left: 3px; }
        .progress-bar-bg { width: 100%; height: 6px; background: #1e293b; border-radius: 3px; overflow: hidden; margin-top: 8px; }
        .progress-bar-fill { height: 100%; width: 0%; background: var(--accent-cyan); transition: width 0.3s, background 0.3s; }

        /* Advice Card */
        .advice-box { background: rgba(15, 23, 42, 0.6); border: 1px solid #1e293b; border-radius: 12px; padding: 14px; font-size: 0.88rem; line-height: 1.5; color: #cbd5e1; }
        .advice-box ul { padding-left: 18px; margin-top: 8px; }
        .advice-box li { margin-bottom: 5px; }

        /* Helpers */
        .color-green { color: var(--accent-green) !important; }
        .color-amber { color: var(--accent-amber) !important; }
        .color-red { color: var(--accent-rose) !important; }
        .bg-green { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); }
        .bg-amber { background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); }
        .bg-red { background: rgba(244, 63, 94, 0.2); color: var(--accent-rose); }
    </style>
</head>
<body>
    <header>
        <div class="logo-area">
            <div class="logo-icon">👁️</div>
            <div class="header-title">
                <h1>GPBL Ergonomics & Posture AI</h1>
                <p>Giám Sát Tư Thế & Trục Tọa Độ Gắn Chóp Mũi Thời Gian Thực</p>
            </div>
        </div>
        <div class="header-badges">
            <div class="badge badge-green" id="badge-cam">
                <span class="badge-dot"></span>
                <span>ESP32 Cam (172.20.10.3)</span>
            </div>
            <div class="badge badge-green" id="badge-firebase">
                <span class="badge-dot"></span>
                <span id="firebase-status-text">Firebase RTDB: Đang kết nối...</span>
            </div>
            <button id="btn-audio" class="btn-audio active" onclick="toggleAudioAlarm()">
                <span id="audio-icon">🔔</span>
                <span id="audio-label">Chuông Báo: BẬT</span>
            </button>
        </div>
    </header>

    <div class="dashboard-grid">
        <!-- LEFT COLUMN: Live Video & Warnings -->
        <div class="left-col">
            <!-- Video Stream Card -->
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📹 Video Stream & Trục Tọa Độ 3D Gắn Chóp Mũi (Nose Tip)</div>
                    <span class="badge badge-green" id="stream-status-badge"><span class="badge-dot"></span> <span id="stream-state-text">STREAM LIVE</span></span>
                </div>
                <div class="video-box" id="video-container">
                    <img id="stream-view" src="" alt="Đang kết nối luồng camera...">
                    <!-- 3D Axes Canvas Overlay -->
                    <canvas id="axis-canvas"></canvas>
                    <div class="video-hud">
                        <div class="hud-pill" id="hud-angles">P: 0.0° | R: 0.0° | Y: 0.0°</div>
                        <div class="hud-pill" id="hud-calib-pill" style="display:none; color: #fbbf24; border-color: #f59e0b;">🎯 Đã Calibrate Gốc</div>
                        <div class="hud-pill" id="hud-distance">Khoảng cách: -- cm</div>
                    </div>
                </div>
                <div class="stream-controls">
                    <button id="btn-toggle-stream" class="btn btn-primary" onclick="toggleStream()">⏹ Tắt Stream</button>
                    <button id="btn-calibrate" class="btn btn-calibrate" onclick="calibrateOrigin()" title="Đặt tư thế ngồi hiện tại làm mốc 0 độ">🎯 Calibrate Gốc (0°)</button>
                    <button id="btn-toggle-axes" class="btn btn-secondary" onclick="toggleAxesOverlay()">🧭 Ẩn/Hiện Trục 3D</button>
                    <button id="btn-reset-calib" class="btn btn-secondary" onclick="resetCalibration()">🔄 Reset Gốc</button>
                    <button class="btn btn-secondary" onclick="toggleFullscreen()">⛶ Toàn Màn Hình</button>
                    <a href="/capture" target="_blank" class="btn btn-secondary">📸 Chụp Ảnh</a>
                </div>
            </div>

            <!-- Posture Status & Warnings Card -->
            <div class="card">
                <div class="card-header">
                    <div class="card-title">🚨 Trung Tâm Cảnh Báo Tư Thế & Sức Khỏe</div>
                    <span id="last-sync-time" style="font-size: 0.78rem; color: var(--text-muted);">Cập nhật: Vừa xong</span>
                </div>

                <!-- Main Status Banner -->
                <div id="status-banner" class="status-banner status-good">
                    <div>
                        <div class="status-text-main" id="status-title">🟢 TƯ THẾ TỐT - ĐẠT CHUẨN</div>
                        <div class="status-desc" id="status-desc">Bạn đang duy trì tư thế ngồi làm việc chuẩn công thái học.</div>
                    </div>
                    <div id="status-big-icon" style="font-size: 2rem;">✅</div>
                </div>

                <!-- Active Warning List -->
                <div class="alert-list" id="alert-list">
                    <div class="alert-item safe">
                        <div class="alert-icon">✨</div>
                        <div class="alert-content">
                            <strong>Trạng thái bình thường</strong>
                            <span>Góc nghiêng đầu và khoảng cách ngồi đều nằm trong giới hạn an toàn.</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RIGHT COLUMN: Head Angles, Sensors & Advice -->
        <div class="right-col">
            <!-- Head Pose Angles Card -->
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📐 Góc Tư Thế Đầu (Pitch - Roll - Yaw)</div>
                    <span style="font-size: 0.8rem; color: var(--text-muted);">Giới hạn an toàn: ±15°</span>
                </div>
                
                <div class="angles-grid">
                    <!-- Pitch Card -->
                    <div class="angle-card" id="card-pitch">
                        <span class="angle-label">Góc Pitch (Cúi/Ngửa)</span>
                        <div class="angle-value" id="val-pitch">0.0°</div>
                        <span class="angle-status bg-green" id="tag-pitch">Chuẩn</span>
                        <div class="pitch-meter-box">
                            <div class="pitch-zero-line"></div>
                            <div class="pitch-safe-zone"></div>
                            <div class="pitch-indicator-bar" id="meter-pitch"></div>
                        </div>
                    </div>

                    <!-- Roll Card -->
                    <div class="angle-card" id="card-roll">
                        <span class="angle-label">Góc Roll (Nghiêng)</span>
                        <div class="angle-value" id="val-roll">0.0°</div>
                        <span class="angle-status bg-green" id="tag-roll">Cân bằng</span>
                        <div class="roll-horizon-box">
                            <div class="roll-line" id="meter-roll"></div>
                            <div class="roll-center-dot"></div>
                        </div>
                    </div>

                    <!-- Yaw Card -->
                    <div class="angle-card" id="card-yaw">
                        <span class="angle-label">Góc Yaw (Quay)</span>
                        <div class="angle-value" id="val-yaw">0.0°</div>
                        <span class="angle-status bg-green" id="tag-yaw">Thẳng</span>
                        <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 10px;">Hướng nhìn chính diện</div>
                    </div>
                </div>

                <!-- 3D Posture Model Visualizer -->
                <div class="attitude-3d-card">
                    <div class="head-model-container">
                        <div class="head-3d-box" id="head-3d">
                            <div class="head-eyes">
                                <div class="head-eye"></div>
                                <div class="head-eye"></div>
                            </div>
                            <div class="head-nose"></div>
                            <div class="head-mouth"></div>
                        </div>
                    </div>
                    <div style="flex: 1; padding-left: 16px;">
                        <strong style="font-size: 0.92rem; color: var(--text-main); display: block; margin-bottom: 4px;">Mô Phỏng Tư Thế Đầu 3D (Gốc tại Chóp Mũi)</strong>
                        <p style="font-size: 0.8rem; color: var(--text-muted); line-height: 1.4;">
                            Trục 3D trên camera và mô hình gắn chặt vào chóp mũi người dùng (Landmark 1) theo góc <strong>Pitch</strong> (X-Đỏ: Ngang), <strong>Yaw</strong> (Y-Xanh lục: Dọc) và <strong>Hướng Nhìn/Roll</strong> (Z-Xanh lam: Mũi chỉ ra ngoài).
                        </p>
                    </div>
                </div>
            </div>

            <!-- Sensors & Environment Card -->
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📊 Cảm Biến Môi Trường & Thị Lực</div>
                    <span style="font-size: 0.8rem; color: var(--text-muted);">ESP32-S3 + LDR + Siêu âm</span>
                </div>

                <div class="sensors-grid">
                    <!-- Distance Sensor -->
                    <div class="sensor-card">
                        <div class="sensor-header">
                            <span>Khoảng cách mắt</span>
                            <span id="dist-status" class="color-green">Chuẩn (50-70cm)</span>
                        </div>
                        <div>
                            <span class="sensor-val" id="val-dist">55.0</span>
                            <span class="sensor-unit">cm</span>
                        </div>
                        <div class="progress-bar-bg">
                            <div class="progress-bar-fill" id="bar-dist" style="width: 55%;"></div>
                        </div>
                    </div>

                    <!-- Ambient Light -->
                    <div class="sensor-card">
                        <div class="sensor-header">
                            <span>Độ sáng phòng</span>
                            <span id="lux-status" class="color-green">Đạt chuẩn</span>
                        </div>
                        <div>
                            <span class="sensor-val" id="val-lux">450</span>
                            <span class="sensor-unit">Lux</span>
                        </div>
                        <div class="progress-bar-bg">
                            <div class="progress-bar-fill" id="bar-lux" style="width: 60%; background: #f59e0b;"></div>
                        </div>
                    </div>

                    <!-- Blink Counter -->
                    <div class="sensor-card">
                        <div class="sensor-header">
                            <span>Số lần chớp mắt</span>
                            <span id="blink-freq" class="color-green">Bình thường</span>
                        </div>
                        <div>
                            <span class="sensor-val" id="val-blinks">0</span>
                            <span class="sensor-unit">lần</span>
                        </div>
                    </div>

                    <!-- EAR Ratio -->
                    <div class="sensor-card">
                        <div class="sensor-header">
                            <span>Chỉ số mở mắt (EAR)</span>
                            <span id="ear-status" class="color-green">Mắt mở</span>
                        </div>
                        <div>
                            <span class="sensor-val" id="val-ear">0.32</span>
                            <span class="sensor-unit">EAR</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- AI LLM Advice Card -->
            <div class="card">
                <div class="card-header">
                    <div class="card-title">💡 Lời Khuyên Công Thái Học (AI Advice)</div>
                </div>
                <div class="advice-box" id="advice-content">
                    <strong style="color: var(--accent-cyan); display: block; margin-bottom: 6px;">Lời khuyên thông minh:</strong>
                    <div id="advice-text">
                        Đang tải khuyến nghị công thái học từ hệ thống AI...
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Toast Notification -->
    <div id="toast"></div>

    <!-- Script Logic -->
    <script>
        // DOM Elements
        const streamView = document.getElementById('stream-view');
        const axisCanvas = document.getElementById('axis-canvas');
        const btnToggleStream = document.getElementById('btn-toggle-stream');
        const btnCalibrate = document.getElementById('btn-calibrate');
        const streamStateText = document.getElementById('stream-state-text');
        const streamBadge = document.getElementById('stream-status-badge');
        const hudAngles = document.getElementById('hud-angles');
        const hudDistance = document.getElementById('hud-distance');
        const hudCalibPill = document.getElementById('hud-calib-pill');
        const toast = document.getElementById('toast');

        // Angle Displays
        const valPitch = document.getElementById('val-pitch');
        const valRoll = document.getElementById('val-roll');
        const valYaw = document.getElementById('val-yaw');
        const tagPitch = document.getElementById('tag-pitch');
        const tagRoll = document.getElementById('tag-roll');
        const tagYaw = document.getElementById('tag-yaw');
        const meterPitch = document.getElementById('meter-pitch');
        const meterRoll = document.getElementById('meter-roll');
        const head3D = document.getElementById('head-3d');

        // Status & Alerts
        const statusBanner = document.getElementById('status-banner');
        const statusTitle = document.getElementById('status-title');
        const statusDesc = document.getElementById('status-desc');
        const statusBigIcon = document.getElementById('status-big-icon');
        const alertList = document.getElementById('alert-list');
        const lastSyncTime = document.getElementById('last-sync-time');

        // Sensors
        const valDist = document.getElementById('val-dist');
        const barDist = document.getElementById('bar-dist');
        const distStatus = document.getElementById('dist-status');
        const valLux = document.getElementById('val-lux');
        const barLux = document.getElementById('bar-lux');
        const luxStatus = document.getElementById('lux-status');
        const valBlinks = document.getElementById('val-blinks');
        const valEar = document.getElementById('val-ear');
        const earStatus = document.getElementById('ear-status');
        const adviceText = document.getElementById('advice-text');
        const firebaseStatusText = document.getElementById('firebase-status-text');

        // State Variables
        let isStreaming = false;
        let show3DAxes = true;
        let audioAlarmEnabled = true;
        let audioCtx = null;
        let lastBeepTime = 0;
        
        // Raw & Calibrated Head Pose Angles
        let rawPitch = 0.0, rawRoll = 0.0, rawYaw = 0.0;
        let calibPitch = 0.0, calibRoll = 0.0, calibYaw = 0.0;
        let isCalibrated = false;
        let currentPitch = 0.0, currentRoll = 0.0, currentYaw = 0.0;
        
        // Interpolated angles for 60fps smooth canvas render
        let renderPitch = 0.0, renderRoll = 0.0, renderYaw = 0.0;

        // Nose Coordinates (Normalized 0.0 - 1.0)
        let noseNormX = 0.5;
        let noseNormY = 0.55;
        let targetNoseX = 0.5;
        let targetNoseY = 0.55;

        let currentDist = 55.0;
        let currentLux = 400;

        const FIREBASE_BASE = 'https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app';

        // Toast Helper
        let toastTimeout = null;
        function alertToast(msg) {
            toast.innerText = msg;
            toast.classList.add('show');
            if (toastTimeout) clearTimeout(toastTimeout);
            toastTimeout = setTimeout(() => toast.classList.remove('show'), 3500);
        }

        // Initialize Video Stream
        function initStream() {
            const streamUrl = window.location.protocol + '//' + window.location.hostname + ':81/stream';
            streamView.src = streamUrl;
            isStreaming = true;
            btnToggleStream.innerText = '⏹ Tắt Stream';
            btnToggleStream.classList.add('btn-primary');
            streamStateText.innerText = 'STREAM LIVE';
            streamBadge.className = 'badge badge-green';
        }

        function toggleStream() {
            if (isStreaming) {
                streamView.src = '';
                btnToggleStream.innerText = '▶ Bật Stream';
                btnToggleStream.className = 'btn btn-secondary';
                streamStateText.innerText = 'STREAM OFF';
                streamBadge.className = 'badge badge-red';
                isStreaming = false;
            } else {
                initStream();
            }
        }

        function reloadStream() {
            streamView.src = '';
            setTimeout(initStream, 300);
        }

        function toggleFullscreen() {
            const container = document.getElementById('video-container');
            if (!document.fullscreenElement) {
                container.requestFullscreen().catch(err => console.log(err));
            } else {
                document.exitFullscreen();
            }
        }

        function toggleAxesOverlay() {
            show3DAxes = !show3DAxes;
            const btn = document.getElementById('btn-toggle-axes');
            if (show3DAxes) {
                btn.innerText = '🧭 Ẩn Trục 3D';
                btn.classList.add('btn-primary');
            } else {
                btn.innerText = '🧭 Hiện Trục 3D';
                btn.classList.remove('btn-primary');
            }
        }

        // Calibrate Zero-Angle Origin
        function calibrateOrigin() {
            calibPitch = rawPitch;
            calibRoll = rawRoll;
            calibYaw = rawYaw;
            isCalibrated = true;

            btnCalibrate.innerText = '🎯 Đã Calibrate (0°)';
            btnCalibrate.classList.add('btn-calibrated');
            hudCalibPill.style.display = 'inline-block';

            // Send calibration request to Firebase RTDB so Python AI worker synchronizes
            fetch(`${FIREBASE_BASE}/ai_data.json`, {
                method: 'PATCH',
                body: JSON.stringify({ calibrate_req: Date.now() }),
                headers: { 'Content-Type': 'application/json' }
            }).catch(e => {});

            playCalibChime();
            alertToast('🎯 Đã hiệu chuẩn gốc tọa độ chuẩn (0°, 0°, 0°) tại vị trí ngồi hiện tại!');
        }

        function resetCalibration() {
            calibPitch = 0.0;
            calibRoll = 0.0;
            calibYaw = 0.0;
            isCalibrated = false;

            btnCalibrate.innerText = '🎯 Calibrate Gốc (0°)';
            btnCalibrate.classList.remove('btn-calibrated');
            hudCalibPill.style.display = 'none';

            alertToast('🔄 Đã đặt lại gốc tọa độ về giá trị thô ban đầu.');
        }

        // Web Audio Synthesizer Beep
        function toggleAudioAlarm() {
            audioAlarmEnabled = !audioAlarmEnabled;
            const btn = document.getElementById('btn-audio');
            const icon = document.getElementById('audio-icon');
            const label = document.getElementById('audio-label');
            if (audioAlarmEnabled) {
                btn.classList.add('active');
                icon.innerText = '🔔';
                label.innerText = 'Chuông Báo: BẬT';
            } else {
                btn.classList.remove('active');
                icon.innerText = '🔕';
                label.innerText = 'Chuông Báo: TẮT';
            }
        }

        function playWarningBeep() {
            if (!audioAlarmEnabled) return;
            const now = Date.now();
            if (now - lastBeepTime < 3500) return;
            lastBeepTime = now;

            try {
                if (!audioCtx) {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                if (audioCtx.state === 'suspended') {
                    audioCtx.resume();
                }
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(880, audioCtx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(587.33, audioCtx.currentTime + 0.18);
                gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.25);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.26);
            } catch (e) {}
        }

        function playCalibChime() {
            try {
                if (!audioCtx) {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                if (audioCtx.state === 'suspended') audioCtx.resume();
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(523.25, audioCtx.currentTime); // C5
                osc.frequency.exponentialRampToValueAtTime(1046.50, audioCtx.currentTime + 0.15); // C6
                gain.gain.setValueAtTime(0.35, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.2);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.22);
            } catch (e) {}
        }

        // Calculate exact image bounding box within video container (handling aspect ratios & letterboxing)
        function getImageRenderBox() {
            const rect = axisCanvas.getBoundingClientRect();
            if (axisCanvas.width !== rect.width || axisCanvas.height !== rect.height) {
                axisCanvas.width = rect.width;
                axisCanvas.height = rect.height;
            }

            const cW = axisCanvas.width;
            const cH = axisCanvas.height;
            if (cW <= 0 || cH <= 0) return { x: 0, y: 0, w: cW, h: cH };

            // Natural image aspect ratio (default 4:3)
            const imgW = streamView.naturalWidth || 640;
            const imgH = streamView.naturalHeight || 480;
            const imgRatio = imgW / imgH;
            const canvasRatio = cW / cH;

            let renderW, renderH, offsetX, offsetY;
            if (canvasRatio > imgRatio) {
                renderH = cH;
                renderW = cH * imgRatio;
                offsetX = (cW - renderW) / 2;
                offsetY = 0;
            } else {
                renderW = cW;
                renderH = cW / imgRatio;
                offsetX = 0;
                offsetY = (cH - renderH) / 2;
            }
            return { x: offsetX, y: offsetY, w: renderW, h: renderH };
        }

        // Draw 3D Axes Centered at Nose on Canvas
        function draw3DAxesCanvas() {
            if (!axisCanvas) return;
            const ctx = axisCanvas.getContext('2d');
            const imgBox = getImageRenderBox();

            ctx.clearRect(0, 0, axisCanvas.width, axisCanvas.height);

            if (!show3DAxes || axisCanvas.width <= 0 || axisCanvas.height <= 0) return;

            // Interpolate angles & nose position for 60fps smoothness
            renderPitch += (currentPitch - renderPitch) * 0.22;
            renderRoll  += (currentRoll  - renderRoll)  * 0.22;
            renderYaw   += (currentYaw   - renderYaw)   * 0.22;
            noseNormX   += (targetNoseX  - noseNormX)   * 0.18;
            noseNormY   += (targetNoseY  - noseNormY)   * 0.18;

            // Center of coordinate system attached directly to the user's nose tip
            const originX = imgBox.x + imgBox.w * noseNormX;
            const originY = imgBox.y + imgBox.h * noseNormY;
            const axisLength = Math.min(imgBox.w, imgBox.h) * 0.24; // Responsive length

            // Convert angles to radians
            const p = (renderPitch * Math.PI) / 180.0;
            const r = (renderRoll * Math.PI) / 180.0;
            const y = (renderYaw * Math.PI) / 180.0;

            const cp = Math.cos(p), sp = Math.sin(p);
            const cr = Math.cos(r), sr = Math.sin(r);
            const cy = Math.cos(y), sy = Math.sin(y);

            // 3D rotation matrix matching solvePnP / MediaPipe:
            // R = Rz(Roll) * Rx(Pitch) * Ry(Yaw)
            function transformPoint3D(vx, vy, vz) {
                // 1. Ry (Yaw)
                const x1 = vx * cy + vz * sy;
                const y1 = vy;
                const z1 = -vx * sy + vz * cy;

                // 2. Rx (Pitch)
                const x2 = x1;
                const y2 = y1 * cp - z1 * sp;
                const z2 = y1 * sp + z1 * cp;

                // 3. Rz (Roll)
                const x3 = x2 * cr - y2 * sr;
                const y3 = x2 * sr + y2 * cr;
                const z3 = z2;

                return {
                    x: originX + x3,
                    y: originY + y3,
                    z: z3
                };
            }

            // Define 3D axis endpoints from nose tip:
            // X-Axis (Red): Pointing right along face horizontal axis
            const pX = transformPoint3D(axisLength, 0, 0);
            // Y-Axis (Green): Pointing down along face vertical axis (bridge of nose -> chin)
            const pY = transformPoint3D(0, axisLength, 0);
            // Z-Axis (Blue): Pointing outward from nose tip towards camera (gaze direction)
            const pZ = transformPoint3D(0, 0, -axisLength);

            // Draw Axis Line with Arrow, Glow & Label
            function drawAxis(target, color, label, angleVal, isZ = false) {
                const dx = target.x - originX;
                const dy = target.y - originY;
                const dist = Math.hypot(dx, dy);

                // Draw main axis line
                ctx.beginPath();
                ctx.moveTo(originX, originY);
                ctx.lineTo(target.x, target.y);
                ctx.strokeStyle = color;
                ctx.lineWidth = isZ ? 4.0 : 3.2;
                ctx.lineCap = 'round';
                ctx.shadowColor = color;
                ctx.shadowBlur = 8;
                ctx.stroke();
                ctx.shadowBlur = 0;

                // If axis has noticeable projection length, draw directional arrowhead
                if (dist > 8) {
                    const angle = Math.atan2(dy, dx);
                    const headLen = isZ ? 14 : 11;
                    ctx.beginPath();
                    ctx.moveTo(target.x, target.y);
                    ctx.lineTo(target.x - headLen * Math.cos(angle - Math.PI / 6), target.y - headLen * Math.sin(angle - Math.PI / 6));
                    ctx.lineTo(target.x - headLen * Math.cos(angle + Math.PI / 6), target.y - headLen * Math.sin(angle + Math.PI / 6));
                    ctx.closePath();
                    ctx.fillStyle = color;
                    ctx.fill();

                    // Text Badge
                    ctx.font = 'bold 12px "Segoe UI", sans-serif';
                    ctx.fillStyle = color;
                    ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
                    ctx.shadowBlur = 6;
                    ctx.fillText(`${label}: ${(angleVal >= 0 ? '+' : '') + angleVal.toFixed(1)}°`, target.x + 8, target.y + 4);
                    ctx.shadowBlur = 0;
                } else if (isZ) {
                    // When looking straight forward, draw a target crosshair badge for Z-axis
                    ctx.beginPath();
                    ctx.arc(originX, originY, 16, 0, 2 * Math.PI);
                    ctx.strokeStyle = color;
                    ctx.lineWidth = 1.8;
                    ctx.setLineDash([3, 3]);
                    ctx.stroke();
                    ctx.setLineDash([]);

                    ctx.font = 'bold 11px "Segoe UI", sans-serif';
                    ctx.fillStyle = color;
                    ctx.shadowColor = 'rgba(0,0,0,0.9)';
                    ctx.shadowBlur = 6;
                    ctx.fillText(`Z (Hướng Mũi): ${(angleVal >= 0 ? '+' : '') + angleVal.toFixed(1)}°`, originX + 22, originY - 14);
                    ctx.shadowBlur = 0;
                }
            }

            // Draw 3 Axes
            drawAxis(pX, '#f43f5e', 'X (Pitch)', renderPitch);
            drawAxis(pY, '#10b981', 'Y (Yaw)', renderYaw);
            drawAxis(pZ, '#38bdf8', 'Z (Mũi/Roll)', renderRoll, true);

            // Draw Origin (Nose Tip - Landmark #1)
            // Outer pulse glow ring
            ctx.beginPath();
            ctx.arc(originX, originY, 10, 0, 2 * Math.PI);
            ctx.strokeStyle = 'rgba(56, 189, 248, 0.9)';
            ctx.lineWidth = 2.5;
            ctx.shadowColor = '#38bdf8';
            ctx.shadowBlur = 10;
            ctx.stroke();
            ctx.shadowBlur = 0;

            // Inner solid center dot
            ctx.beginPath();
            ctx.arc(originX, originY, 5, 0, 2 * Math.PI);
            ctx.fillStyle = '#ffffff';
            ctx.fill();

            // Label at Nose Origin
            ctx.font = '600 11px "Segoe UI", sans-serif';
            ctx.fillStyle = '#f8fafc';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
            ctx.shadowBlur = 6;
            ctx.fillText('👃 Chóp Mũi (0,0,0)', originX - 45, originY + 24);
            ctx.shadowBlur = 0;
        }

        // Animation Render Loop
        function animate() {
            draw3DAxesCanvas();
            requestAnimationFrame(animate);
        }

        // Update Dashboard UI with Data
        function updateDashboard(aiData, sensorData) {
            const nowStr = new Date().toLocaleTimeString('vi-VN');
            lastSyncTime.innerText = 'Cập nhật: ' + nowStr;

            // Pitch, Roll, Yaw
            if (aiData && aiData.pitch !== undefined) rawPitch = parseFloat(aiData.pitch);
            if (aiData && aiData.roll !== undefined) rawRoll = parseFloat(aiData.roll);
            if (aiData && aiData.yaw !== undefined) rawYaw = parseFloat(aiData.yaw);

            // Nose 2D Coordinates if passed from AI tracking
            if (aiData && aiData.nose_x !== undefined) targetNoseX = Math.max(0.1, Math.min(0.9, parseFloat(aiData.nose_x)));
            if (aiData && aiData.nose_y !== undefined) targetNoseY = Math.max(0.1, Math.min(0.9, parseFloat(aiData.nose_y)));

            // Apply Calibration Offsets
            currentPitch = rawPitch - calibPitch;
            currentRoll = rawRoll - calibRoll;
            currentYaw = rawYaw - calibYaw;

            // Sensor Distance & Lux
            if (sensorData && sensorData.distance !== undefined && sensorData.distance >= 0) {
                currentDist = parseFloat(sensorData.distance);
            } else if (aiData && aiData.distance_cm !== undefined && aiData.distance_cm > 0) {
                currentDist = parseFloat(aiData.distance_cm);
            }
            if (sensorData && sensorData.lux !== undefined) {
                currentLux = Math.round(sensorData.lux);
            }

            // Blinks & EAR
            if (aiData && aiData.blinks !== undefined) {
                valBlinks.innerText = aiData.blinks;
            }
            if (aiData && aiData.ear !== undefined) {
                valEar.innerText = parseFloat(aiData.ear).toFixed(2);
                if (aiData.ear < 0.26) {
                    earStatus.innerText = 'Nhắm mắt / Mỏi';
                    earStatus.className = 'color-amber';
                } else {
                    earStatus.innerText = 'Mắt mở tốt';
                    earStatus.className = 'color-green';
                }
            }

            // Render Numbers
            valPitch.innerText = (currentPitch >= 0 ? '+' : '') + currentPitch.toFixed(1) + '°';
            valRoll.innerText = (currentRoll >= 0 ? '+' : '') + currentRoll.toFixed(1) + '°';
            valYaw.innerText = (currentYaw >= 0 ? '+' : '') + currentYaw.toFixed(1) + '°';
            valDist.innerText = currentDist.toFixed(1);
            valLux.innerText = currentLux;

            // HUD
            hudAngles.innerText = `P: ${(currentPitch >= 0 ? '+' : '') + currentPitch.toFixed(1)}° | R: ${(currentRoll >= 0 ? '+' : '') + currentRoll.toFixed(1)}° | Y: ${(currentYaw >= 0 ? '+' : '') + currentYaw.toFixed(1)}°`;
            hudDistance.innerText = `Khoảng cách: ${currentDist.toFixed(1)} cm`;

            // Active Warnings Evaluation
            const warnings = [];
            let isDanger = false;
            let isCaution = false;

            // 1. Pitch Warning (> 15° or < -15°)
            if (currentPitch > 15) {
                isDanger = true;
                warnings.push({
                    type: 'danger',
                    icon: '🚨',
                    title: `Cúi đầu quá sâu (${(currentPitch >= 0 ? '+' : '') + currentPitch.toFixed(1)}° > 15°)`,
                    desc: 'Nguy cơ thoái hóa đốt sống cổ & gù lưng. Hãy nâng cao màn hình hoặc ngẩng thẳng đầu!'
                });
                tagPitch.innerText = 'Cúi quá sâu';
                tagPitch.className = 'angle-status bg-red';
                valPitch.className = 'angle-value color-red';
                meterPitch.style.background = 'var(--accent-rose)';
            } else if (currentPitch < -15) {
                isDanger = true;
                warnings.push({
                    type: 'danger',
                    icon: '🚨',
                    title: `Ngửa đầu quá cao (${currentPitch.toFixed(1)}° < -15°)`,
                    desc: 'Gây căng cơ gáy cổ. Hãy hạ tầm mắt ngang 1/3 phía trên màn hình!'
                });
                tagPitch.innerText = 'Ngửa quá cao';
                tagPitch.className = 'angle-status bg-red';
                valPitch.className = 'angle-value color-red';
                meterPitch.style.background = 'var(--accent-rose)';
            } else if (Math.abs(currentPitch) > 10) {
                isCaution = true;
                tagPitch.innerText = currentPitch > 0 ? 'Hơi cúi' : 'Hơi ngửa';
                tagPitch.className = 'angle-status bg-amber';
                valPitch.className = 'angle-value color-amber';
                meterPitch.style.background = 'var(--accent-amber)';
            } else {
                tagPitch.innerText = 'Chuẩn (Tốt)';
                tagPitch.className = 'angle-status bg-green';
                valPitch.className = 'angle-value color-green';
                meterPitch.style.background = 'var(--accent-cyan)';
            }

            // Pitch Indicator Position (-45 to +45 deg mapped to 0% to 100%)
            const clampedPitch = Math.max(-45, Math.min(45, currentPitch));
            const pitchPercent = ((clampedPitch + 45) / 90) * 100;
            meterPitch.style.left = pitchPercent + '%';

            // 2. Roll Warning (|Roll| > 15°)
            if (Math.abs(currentRoll) > 15) {
                isDanger = true;
                warnings.push({
                    type: 'danger',
                    icon: '🚨',
                    title: `Nghiêng đầu lệch trục (${currentRoll.toFixed(1)}°)`,
                    desc: 'Gây vẹo cột sống cổ. Hãy giữ đầu thẳng và cân bằng hai bên vai!'
                });
                tagRoll.innerText = currentRoll > 0 ? 'Nghiêng Phải' : 'Nghiêng Trái';
                tagRoll.className = 'angle-status bg-red';
                valRoll.className = 'angle-value color-red';
                meterRoll.style.background = 'var(--accent-rose)';
            } else if (Math.abs(currentRoll) > 10) {
                isCaution = true;
                tagRoll.innerText = currentRoll > 0 ? 'Nghiêng nhẹ Phải' : 'Nghiêng nhẹ Trái';
                tagRoll.className = 'angle-status bg-amber';
                valRoll.className = 'angle-value color-amber';
                meterRoll.style.background = 'var(--accent-amber)';
            } else {
                tagRoll.innerText = 'Cân bằng (Tốt)';
                tagRoll.className = 'angle-status bg-green';
                valRoll.className = 'angle-value color-green';
                meterRoll.style.background = 'var(--accent-cyan)';
            }

            // Roll Horizon Rotation
            meterRoll.style.transform = `rotate(${-currentRoll}deg)`;

            // 3. 3D Model Transformation
            head3D.style.transform = `rotateX(${-currentPitch * 1.2}deg) rotateZ(${-currentRoll}deg) rotateY(${currentYaw * 0.8}deg)`;
            if (isDanger) {
                head3D.style.borderColor = 'var(--accent-rose)';
                head3D.style.boxShadow = '0 0 20px rgba(244, 63, 94, 0.4)';
            } else if (isCaution) {
                head3D.style.borderColor = 'var(--accent-amber)';
                head3D.style.boxShadow = '0 0 15px rgba(245, 158, 11, 0.3)';
            } else {
                head3D.style.borderColor = 'var(--accent-cyan)';
                head3D.style.boxShadow = '0 0 20px rgba(56, 189, 248, 0.2)';
            }

            // 4. Distance Warning (< 40cm)
            const distPercent = Math.min(100, (currentDist / 100) * 100);
            barDist.style.width = distPercent + '%';

            if (currentDist > 0 && currentDist < 40) {
                isDanger = true;
                warnings.push({
                    type: 'danger',
                    icon: '📏',
                    title: `Ngồi quá gần màn hình (${currentDist.toFixed(1)} cm < 40 cm)`,
                    desc: 'Khoảng cách mắt quá gần! Nguy cơ tăng độ cận và nhức mỏi mắt. Hãy lùi về 50 - 70 cm!'
                });
                distStatus.innerText = 'Quá gần (<40cm)';
                distStatus.className = 'color-red';
                barDist.style.background = 'var(--accent-rose)';
            } else if (currentDist >= 40 && currentDist <= 75) {
                distStatus.innerText = 'Chuẩn (50-70cm)';
                distStatus.className = 'color-green';
                barDist.style.background = 'var(--accent-green)';
            } else if (currentDist > 75) {
                distStatus.innerText = 'Hơi xa (>75cm)';
                distStatus.className = 'color-amber';
                barDist.style.background = 'var(--accent-amber)';
            }

            // 5. Lighting Warning (< 300 Lux)
            const luxPercent = Math.min(100, (currentLux / 1000) * 100);
            barLux.style.width = luxPercent + '%';

            if (currentLux < 300) {
                warnings.push({
                    type: 'caution',
                    icon: '💡',
                    title: `Môi trường thiếu ánh sáng (${currentLux} Lux < 300 Lux)`,
                    desc: 'Ánh sáng phòng quá tối gây mỏi mắt và suy giảm thị lực. Hãy bật thêm đèn bàn!'
                });
                luxStatus.innerText = 'Thiếu sáng (<300)';
                luxStatus.className = 'color-amber';
                barLux.style.background = 'var(--accent-amber)';
            } else {
                luxStatus.innerText = 'Đủ sáng (Chuẩn)';
                luxStatus.className = 'color-green';
                barLux.style.background = 'var(--accent-green)';
            }

            // Render Banner & Alerts
            if (isDanger) {
                statusBanner.className = 'status-banner status-danger';
                statusTitle.innerText = '🔴 CẢNH BÁO NGUY HIỂM: SAI TƯ THẾ!';
                statusDesc.innerText = 'Phát hiện tư thế ngồi không đúng chuẩn. Vui lòng điều chỉnh ngay theo hướng dẫn bên dưới.';
                statusBigIcon.innerText = '⚠️';
                playWarningBeep();
            } else if (isCaution || warnings.length > 0) {
                statusBanner.className = 'status-banner status-caution';
                statusTitle.innerText = '🟡 LƯU Ý: CẦN ĐIỀU CHỈNH TƯ THẾ';
                statusDesc.innerText = 'Tư thế đang chớm vi phạm góc nghiêng hoặc môi trường ánh sáng chưa tối ưu.';
                statusBigIcon.innerText = '🔔';
            } else {
                statusBanner.className = 'status-banner status-good';
                statusTitle.innerText = '🟢 TƯ THẾ TỐT - ĐẠT CHUẨN CÔNG THÁI HỌC';
                statusDesc.innerText = 'Bạn đang duy trì tư thế ngồi làm việc rất tốt. Hãy tiếp tục phát huy!';
                statusBigIcon.innerText = '✅';
            }

            // Render Alert Cards
            if (warnings.length > 0) {
                alertList.innerHTML = warnings.map(w => `
                    <div class="alert-item ${w.type}">
                        <div class="alert-icon">${w.icon}</div>
                        <div class="alert-content">
                            <strong>${w.title}</strong>
                            <span>${w.desc}</span>
                        </div>
                    </div>
                `).join('');
            } else {
                alertList.innerHTML = `
                    <div class="alert-item safe">
                        <div class="alert-icon">✨</div>
                        <div class="alert-content">
                            <strong>Trạng thái bình thường</strong>
                            <span>Mọi chỉ số góc đầu, khoảng cách ngồi và ánh sáng đều đạt tiêu chuẩn an toàn.</span>
                        </div>
                    </div>
                `;
            }
        }

        // Setup Real-time Firebase Stream via Server-Sent Events (SSE)
        let sseSource = null;
        function initFirebaseSSE() {
            try {
                if (sseSource) sseSource.close();
                sseSource = new EventSource(`${FIREBASE_BASE}/ai_data.json`);
                sseSource.addEventListener('put', (e) => {
                    if (!e.data) return;
                    try {
                        const parsed = JSON.parse(e.data);
                        if (parsed && parsed.data) {
                            firebaseStatusText.innerText = 'Firebase RTDB: Trực tiếp (Live SSE)';
                            updateDashboard(parsed.data, null);
                        }
                    } catch (err) {}
                });
                sseSource.addEventListener('patch', (e) => {
                    if (!e.data) return;
                    try {
                        const parsed = JSON.parse(e.data);
                        if (parsed && parsed.data) {
                            firebaseStatusText.innerText = 'Firebase RTDB: Trực tiếp (Live SSE)';
                            updateDashboard(parsed.data, null);
                        }
                    } catch (err) {}
                });
                sseSource.onerror = () => {
                    firebaseStatusText.innerText = 'Firebase RTDB: Đang đồng bộ...';
                };
            } catch (err) {}
        }

        // Fetch Data from Firebase Realtime Database (Fallback / Initial)
        async function fetchFirebaseData() {
            try {
                // Fetch AI Data
                const resAi = await fetch(`${FIREBASE_BASE}/ai_data.json`, { cache: 'no-store' });
                let aiData = null;
                if (resAi.ok) {
                    aiData = await resAi.json();
                }

                // Fetch Sensor Data
                const resSensor = await fetch(`${FIREBASE_BASE}/sensor_data.json`, { cache: 'no-store' });
                let sensorData = null;
                if (resSensor.ok) {
                    sensorData = await resSensor.json();
                }

                if (aiData || sensorData) {
                    if (!sseSource || sseSource.readyState !== EventSource.OPEN) {
                        firebaseStatusText.innerText = 'Firebase RTDB: Đã kết nối';
                    }
                    updateDashboard(aiData, sensorData);
                }
            } catch (e) {
                // Fallback: Query local ESP32 /sensors endpoint
                fetchLocalSensors();
            }
        }

        // Fetch Local ESP32 Sensor Endpoint (/sensors)
        async function fetchLocalSensors() {
            try {
                const res = await fetch('/sensors', { cache: 'no-store' });
                if (res.ok) {
                    const data = await res.json();
                    updateDashboard(null, data);
                    firebaseStatusText.innerText = 'ESP32 Cục bộ: Đang đọc cảm biến';
                }
            } catch (e) {}
        }

        // Fetch Ergonomics AI Advice
        async function fetchAdvice() {
            try {
                const res = await fetch(`${FIREBASE_BASE}/advice.json`, { cache: 'no-store' });
                if (res.ok) {
                    const data = await res.json();
                    if (data) {
                        const keys = Object.keys(data);
                        if (keys.length > 0) {
                            const latestKey = keys[keys.length - 1];
                            const item = data[latestKey];
                            let formattedHtml = '';
                            if (item.summary) {
                                formattedHtml += `<p style="margin-bottom: 8px;">${item.summary.replace(/\n/g, '<br>')}</p>`;
                            }
                            if (item.recommendations && Array.isArray(item.recommendations)) {
                                formattedHtml += '<ul>' + item.recommendations.map(r => `<li>${r}</li>`).join('') + '</ul>';
                            }
                            if (formattedHtml) {
                                adviceText.innerHTML = formattedHtml;
                            }
                        }
                    }
                }
            } catch (e) {}
        }

        // Initialize Dashboard
        window.addEventListener('DOMContentLoaded', () => {
            initStream();
            initFirebaseSSE();
            fetchFirebaseData();
            fetchAdvice();
            animate();

            // Real-time Poll fallback: AI Data every 200ms
            setInterval(fetchFirebaseData, 200);
            
            // Poll Advice every 6 seconds
            setInterval(fetchAdvice, 6000);
        });
    </script>
</body>
</html>
)rawliteral";

static esp_err_t index_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
}

static esp_err_t sensors_handler(httpd_req_t *req)
{
    int light_adc = read_light_adc();
    float lux = read_lux();
    float distance = read_distance();

    char json_response[128];
    snprintf(json_response, sizeof(json_response),
             "{\"light_adc\":%d,\"lux\":%.2f,\"distance\":%.2f}",
             light_adc, lux, distance);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, json_response, strlen(json_response));
}

void startCameraServer()
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 16;
    config.stack_size = 16384; // 16 KB stack size cho HTTPD Task

    httpd_uri_t index_uri = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = index_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t sensors_uri = {
        .uri = "/sensors",
        .method = HTTP_GET,
        .handler = sensors_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t status_uri = {
        .uri = "/status",
        .method = HTTP_GET,
        .handler = status_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t cmd_uri = {
        .uri = "/control",
        .method = HTTP_GET,
        .handler = cmd_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t capture_uri = {
        .uri = "/capture",
        .method = HTTP_GET,
        .handler = capture_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t stream_uri = {
        .uri = "/stream",
        .method = HTTP_GET,
        .handler = stream_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t bmp_uri = {
        .uri = "/bmp",
        .method = HTTP_GET,
        .handler = bmp_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t xclk_uri = {
        .uri = "/xclk",
        .method = HTTP_GET,
        .handler = xclk_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t reg_uri = {
        .uri = "/reg",
        .method = HTTP_GET,
        .handler = reg_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t greg_uri = {
        .uri = "/greg",
        .method = HTTP_GET,
        .handler = greg_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t pll_uri = {
        .uri = "/pll",
        .method = HTTP_GET,
        .handler = pll_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    httpd_uri_t win_uri = {
        .uri = "/resolution",
        .method = HTTP_GET,
        .handler = win_handler,
        .user_ctx = NULL
#ifdef CONFIG_HTTPD_WS_SUPPORT
        ,
        .is_websocket = true,
        .handle_ws_control_frames = false,
        .supported_subprotocol = NULL
#endif
    };

    ra_filter_init(&ra_filter, 20);

#if CONFIG_ESP_FACE_RECOGNITION_ENABLED
    recognizer.set_partition(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "fr");

    // load ids from flash partition
    recognizer.set_ids_from_flash();
#endif

    log_i("Starting web server on port: '%d'", config.server_port);
    if (httpd_start(&camera_httpd, &config) == ESP_OK)
    {
        httpd_register_uri_handler(camera_httpd, &index_uri);
        httpd_register_uri_handler(camera_httpd, &sensors_uri);
        httpd_register_uri_handler(camera_httpd, &stream_uri);
        httpd_register_uri_handler(camera_httpd, &cmd_uri);
        httpd_register_uri_handler(camera_httpd, &status_uri);
        httpd_register_uri_handler(camera_httpd, &capture_uri);
        httpd_register_uri_handler(camera_httpd, &bmp_uri);

        httpd_register_uri_handler(camera_httpd, &xclk_uri);
        httpd_register_uri_handler(camera_httpd, &reg_uri);
        httpd_register_uri_handler(camera_httpd, &greg_uri);
        httpd_register_uri_handler(camera_httpd, &pll_uri);
        httpd_register_uri_handler(camera_httpd, &win_uri);
    }

    config.server_port += 1;
    config.ctrl_port += 1;
    log_i("Starting stream server on port: '%d'", config.server_port);
    if (httpd_start(&stream_httpd, &config) == ESP_OK)
    {
        httpd_register_uri_handler(stream_httpd, &stream_uri);
    }
}

void setupLedFlash(int pin) 
{
    #if CONFIG_LED_ILLUMINATOR_ENABLED
    ledcSetup(LED_LEDC_CHANNEL, 5000, 8);
    ledcAttachPin(pin, LED_LEDC_CHANNEL);
    #else
    log_i("LED flash is disabled -> CONFIG_LED_ILLUMINATOR_ENABLED = 0");
    #endif
}
