# Tracking AI - Eye Blink Counter & Pose Estimator

Hệ thống theo dõi nháy mắt và tư thế người dùng thời gian thực sử dụng MediaPipe & OpenCV. Hỗ trợ Webcam và ESP32-CAM IP Camera.

## 🚀 Tính năng

- **`blink_counter.py`**: Chương trình đếm số lần nháy mắt gọn nhẹ, tối ưu cho CPU.
- **`blink_counter_and_EAR_plot.py`**: Đếm nháy mắt kết hợp vẽ đồ thị EAR thời gian thực, ước tính khoảng cách mắt - màn hình và góc cúi đầu (Head Pose).

## 🛠 Cài đặt

```bash
pip install -r requirements.txt
```

## 💻 Cách chạy

Chạy đếm nháy mắt đơn giản:
```bash
python blink_counter.py
```

Chạy đếm nháy mắt + đồ thị EAR:
```bash
python blink_counter_and_EAR_plot.py
```

*Lưu ý: Để thay đổi nguồn video (Webcam 0 hoặc ESP32-CAM URL), chỉnh tham số `input_video_path` ở cuối file.*
