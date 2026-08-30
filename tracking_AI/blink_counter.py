import numpy as np
import cv2 as cv
from FaceMeshModule import FaceMeshGenerator
from utils import DrawingUtils
import os
import threading
import time
import json
import urllib.request


class FirebaseSyncWorker:
    """Non-blocking background worker to sync AI tracking metrics & warnings to Firebase RTDB."""
    def __init__(self, database_url="https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app", on_calibrate=None):
        self.url = database_url.rstrip("/") + "/ai_data.json"
        self.latest_data = None
        self.lock = threading.Lock()
        self.running = True
        self.on_calibrate = on_calibrate
        self.last_calib_req = None
        self.calib_check_counter = 0
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()

    def update_state(self, pitch, roll, yaw, dist_cm, ear, blinks, warnings=None, posture_status="GOOD", nose_x=0.5, nose_y=0.55):
        data = {
            "pitch": round(float(pitch), 2) if pitch is not None else 0.0,
            "roll": round(float(roll), 2) if roll is not None else 0.0,
            "yaw": round(float(yaw), 2) if yaw is not None else 0.0,
            "camera_distance_cm": round(float(dist_cm), 1) if dist_cm is not None else 0.0,
            "ai_distance_cm": round(float(dist_cm), 1) if dist_cm is not None else 0.0,
            "distance_cm": round(float(dist_cm), 1) if dist_cm is not None else 0.0,
            "ear": round(float(ear), 3) if ear is not None else 0.0,
            "blinks": int(blinks),
            "blink_rate": int(blinks),
            "head_pitch": round(float(pitch), 2) if pitch is not None else 0.0,
            "head_roll": round(float(roll), 2) if roll is not None else 0.0,
            "head_yaw": round(float(yaw), 2) if yaw is not None else 0.0,
            "warnings": warnings if warnings else [],
            "posture_status": posture_status,
            "head_pose_thresholds": {
                "pitch_down_max_deg": 5.0,
                "pitch_up_max_deg": 5.0,
                "roll_max_deg": 10.0,
                "yaw_max_deg": 20.0,
                "distance_min_cm": 40.0
            },
            "nose_x": round(float(nose_x), 3) if nose_x is not None else 0.5,
            "nose_y": round(float(nose_y), 3) if nose_y is not None else 0.55,
            "timestamp": time.time()
        }
        with self.lock:
            self.latest_data = data

    def _worker_loop(self):
        last_sent = None
        while self.running:
            data_to_send = None
            with self.lock:
                if self.latest_data is not None:
                    data_to_send = self.latest_data.copy()

            if data_to_send is not None and data_to_send != last_sent:
                try:
                    payload = json.dumps(data_to_send, ensure_ascii=False).encode('utf-8')
                    req = urllib.request.Request(
                        self.url,
                        data=payload,
                        method='PATCH',
                        headers={'Content-Type': 'application/json; charset=utf-8'}
                    )
                    with urllib.request.urlopen(req, timeout=1.0) as res:
                        pass
                    last_sent = data_to_send
                except Exception:
                    pass

            self.calib_check_counter += 1
            if self.calib_check_counter >= 3 and self.on_calibrate:
                self.calib_check_counter = 0
                try:
                    calib_url = self.url.replace(".json", "/calibrate_req.json")
                    with urllib.request.urlopen(calib_url, timeout=0.8) as resp:
                        calib_val = json.loads(resp.read().decode('utf-8'))
                        if calib_val is not None and calib_val != self.last_calib_req:
                            self.last_calib_req = calib_val
                            self.on_calibrate()
                except Exception:
                    pass

            time.sleep(0.15)

    def stop(self):
        self.running = False


class BlinkCounter:
    """
    A class to detect and count eye blinks in a video using facial landmarks.
    """
    
    def __init__(self, video_path, ear_threshold, consec_frames, save_video=False, output_filename=None):
        # Initialize face mesh detector (num_faces=1 for CPU optimization)
        self.generator = FaceMeshGenerator(num_faces=1) 
        self.video_path = video_path
        self.save_video = save_video
        self.output_filename = output_filename
        self.pose_reference = None
        self.firebase_sync = FirebaseSyncWorker(on_calibrate=self.calibrate_head_pose)
        
        # Define facial landmarks for eye detection
        self.RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
        self.LEFT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
        
        # Specific landmarks for EAR calculation
        self.RIGHT_EYE_EAR = [33, 159, 158, 133, 153, 145]
        self.LEFT_EYE_EAR = [362, 380, 374, 263, 386, 385]

        # 3D Head Model Points for Head Pose Estimation (solvePnP)
        self.model_points_3d = np.array([
            (0.0, 0.0, 0.0),             # Nose tip (1)
            (0.0, -330.0, -65.0),        # Chin (152)
            (-225.0, 170.0, -135.0),     # Left eye outer corner (33)
            (225.0, 170.0, -135.0),      # Right eye outer corner (263)
            (-150.0, -150.0, -125.0),    # Left mouth corner (61)
            (150.0, -150.0, -125.0)      # Right mouth corner (291)
        ], dtype=np.float64)
        self.head_pose_landmarks = [1, 152, 33, 263, 61, 291]

        # Distance Estimation & Calibration parameters
        self.K_factor = 4200.0  # Calibration constant
        self.current_eye_pixel_dist = 0.0
        self.last_raw_pose = None
        
        # Blink detection parameters
        self.ear_threshold = ear_threshold  # Eye aspect ratio threshold for blink detection
        self.consec_frames = consec_frames  # Minimum consecutive frames for a valid blink
        self.blink_counter = 0    # Counter for total blinks detected
        self.frame_counter = 0    # Counter for consecutive frames below threshold
        
        # Define colors for visualization (in BGR format)
        self.GREEN_COLOR = (86, 241, 13)  # Used when eyes are open
        self.RED_COLOR = (30, 46, 209)    # Used when eyes are closed
        self.YELLOW_COLOR = (0, 255, 255)
        self.WARNING_COLOR = (0, 0, 255)
        
        # Set up output video directory and path if saving is enabled
        if self.save_video and self.output_filename:
            save_dir = "DATA/VIDEOS/OUTPUTS"
            os.makedirs(save_dir, exist_ok=True)
            self.output_filename = os.path.join(save_dir, self.output_filename)

    def calibrate_head_pose(self):
        """Set current orientation as zero reference."""
        if self.last_raw_pose is not None:
            self.pose_reference = self.last_raw_pose
            print(f"[POSE CALIBRATE SUCCESS] Reference set to Pitch={self.pose_reference[0]:.1f}, Yaw={self.pose_reference[1]:.1f}, Roll={self.pose_reference[2]:.1f}")

    def calibrate_distance(self, known_distance_cm=50.0):
        """Fine-tune the distance multiplier K when user sits at a known distance (e.g. 50cm)."""
        if self.current_eye_pixel_dist > 0:
            self.K_factor = known_distance_cm * self.current_eye_pixel_dist
            print(f"[CALIBRATE SUCCESS] New K_factor: {self.K_factor:.2f} at {known_distance_cm} cm")

    def estimate_distance(self, landmarks):
        """Estimate distance from eyes to camera (in cm) using Interpupillary Distance."""
        left_eye = np.array(landmarks[33])
        right_eye = np.array(landmarks[263])
        self.current_eye_pixel_dist = np.linalg.norm(left_eye - right_eye)
        if self.current_eye_pixel_dist > 0:
            return self.K_factor / self.current_eye_pixel_dist
        return None

    def estimate_head_pose(self, landmarks, frame_w, frame_h):
        """Calculate Pitch, Yaw, Roll angles of the head using solvePnP."""
        image_points_2d = np.array([
            landmarks[idx] for idx in self.head_pose_landmarks
        ], dtype=np.float64)

        focal_length = frame_w
        center = (frame_w / 2, frame_h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1))

        success, rvec, tvec = cv.solvePnP(
            self.model_points_3d, image_points_2d, camera_matrix, dist_coeffs, flags=cv.SOLVEPNP_ITERATIVE
        )

        if success:
            rmat, _ = cv.Rodrigues(rvec)
            angles, _, _, _, _, _ = cv.RQDecomp3x3(rmat)
            pitch, yaw, roll = angles[0], angles[1], angles[2]
            return pitch, yaw, roll
        return None, None, None

    def update_blink_count(self, ear):
        """
        Update blink counter based on current eye aspect ratio.
        """
        blink_detected = False
        
        if ear < self.ear_threshold:
            self.frame_counter += 1
        else:
            if self.frame_counter >= self.consec_frames:
                self.blink_counter += 1
                blink_detected = True
            self.frame_counter = 0
            
        return blink_detected

    def eye_aspect_ratio(self, eye_landmarks, landmarks):
        """
        Calculate the eye aspect ratio (EAR) for given eye landmarks.
        """
        A = np.linalg.norm(np.array(landmarks[eye_landmarks[1]]) - np.array(landmarks[eye_landmarks[5]]))
        B = np.linalg.norm(np.array(landmarks[eye_landmarks[2]]) - np.array(landmarks[eye_landmarks[4]]))
        C = np.linalg.norm(np.array(landmarks[eye_landmarks[0]]) - np.array(landmarks[eye_landmarks[3]]))
        return (A + B) / (2.0 * C)

    def set_colors(self, ear):
        """
        Determine visualization color based on eye aspect ratio.
        """
        return self.RED_COLOR if ear < self.ear_threshold else self.GREEN_COLOR

    def draw_eye_landmarks(self, frame, landmarks, eye_landmarks, color):
        """
        Draw landmarks around the eyes on the frame.
        """
        for loc in eye_landmarks:
            cv.circle(frame, (landmarks[loc]), 3, color, cv.FILLED)

    def draw_head_axes(self, frame, landmarks, pitch=None, yaw=None, roll=None, length=50):
        """Draw X/Y/Z head axes with the actual nose tip (landmark 1) as the origin,
        dynamically rotating according to Pitch, Yaw, Roll angles."""
        if pitch is None or yaw is None or roll is None:
            return

        origin = (int(landmarks[1][0]), int(landmarks[1][1]))

        p = np.radians(pitch)
        y = np.radians(yaw)
        r = np.radians(roll)

        Rx = np.array([
            [1, 0, 0],
            [0, np.cos(p), -np.sin(p)],
            [0, np.sin(p), np.cos(p)]
        ], dtype=np.float64)

        Ry = np.array([
            [np.cos(y), 0, np.sin(y)],
            [0, 1, 0],
            [-np.sin(y), 0, np.cos(y)]
        ], dtype=np.float64)

        Rz = np.array([
            [np.cos(r), -np.sin(r), 0],
            [np.sin(r), np.cos(r), 0],
            [0, 0, 1]
        ], dtype=np.float64)

        R = Rz @ Rx @ Ry

        axis_x = R @ np.array([length, 0, 0], dtype=np.float64)
        axis_y = R @ np.array([0, length, 0], dtype=np.float64)
        axis_z = R @ np.array([0, 0, -length], dtype=np.float64)

        endpoint_x = (int(origin[0] + axis_x[0]), int(origin[1] + axis_x[1]))
        endpoint_y = (int(origin[0] + axis_y[0]), int(origin[1] + axis_y[1]))
        endpoint_z = (int(origin[0] + axis_z[0]), int(origin[1] + axis_z[1]))

        cv.line(frame, origin, endpoint_x, (0, 0, 255), 3, cv.LINE_AA)     # X: Red
        cv.putText(frame, "X", endpoint_x, cv.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv.LINE_AA)

        cv.line(frame, origin, endpoint_y, (0, 255, 0), 3, cv.LINE_AA)     # Y: Green
        cv.putText(frame, "Y", endpoint_y, cv.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv.LINE_AA)

        cv.line(frame, origin, endpoint_z, (255, 0, 0), 3, cv.LINE_AA)     # Z: Blue
        cv.putText(frame, "Z", endpoint_z, cv.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1, cv.LINE_AA)

        cv.circle(frame, origin, 4, (255, 255, 255), cv.FILLED)

    def process_video(self):
        """
        Main method to process the video/webcam, detect blinks, head pose, and distance.
        """
        try:
            # Open video capture (supports 0 for webcam, file paths, or ESP32-CAM HTTP URLs)
            if isinstance(self.video_path, str) and self.video_path.startswith("http"):
                urls_to_try = [self.video_path]
                clean_url = self.video_path.rstrip("/")
                if not clean_url.endswith("/stream"):
                    urls_to_try.insert(0, f"{clean_url}:81/stream")
                    urls_to_try.append(f"{clean_url}/stream")

                cap = None
                for url in urls_to_try:
                    print(f"[ESP32-CAM] Connecting to: {url} ...")
                    temp_cap = cv.VideoCapture(url)
                    if temp_cap.isOpened():
                        ret, _ = temp_cap.read()
                        if ret:
                            print(f"[ESP32-CAM SUCCESS] Connected to {url}")
                            cap = temp_cap
                            break
                        temp_cap.release()
                
                if cap is None or not cap.isOpened():
                    raise IOError(f"Failed to connect to ESP32-CAM stream at {self.video_path}")
            else:
                cap = cv.VideoCapture(self.video_path)
                if not cap.isOpened():
                    print(f"Failed to open video source: {self.video_path}")
                    raise IOError("Error: couldn't open the video source!")

            # Reduce camera resolution for webcam if applicable
            if isinstance(self.video_path, int):
                cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)

            # Get video properties
            w, h, fps = (int(cap.get(x)) for x in (
                cv.CAP_PROP_FRAME_WIDTH,
                cv.CAP_PROP_FRAME_HEIGHT,
                cv.CAP_PROP_FPS
            ))
            if fps <= 0:
                fps = 30  # Default fallback for webcam / HTTP stream

            # Initialize video writer if saving is enabled
            if self.save_video:
                self.out = cv.VideoWriter(
                    self.output_filename,
                    cv.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (w, h)
                )

            # Main processing loop
            is_live_stream = isinstance(self.video_path, int) or (
                isinstance(self.video_path, str) and self.video_path.startswith("http")
            )

            print("\n--- CONTROLS ---")
            print("Press 'p' to Quit\n")

            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                # Resize frame for live stream to 480x360 for high CPU performance
                if is_live_stream:
                    frame = cv.resize(frame, (480, 360))

                # Detect facial landmarks
                frame, face_landmarks = self.generator.create_face_mesh(frame, draw=False)

                if len(face_landmarks) > 0:
                    # Calculate eye aspect ratio & blink detection
                    right_ear = self.eye_aspect_ratio(self.RIGHT_EYE_EAR, face_landmarks)
                    left_ear = self.eye_aspect_ratio(self.LEFT_EYE_EAR, face_landmarks)
                    ear = (right_ear + left_ear) / 2.0
                    self.update_blink_count(ear)

                    # Estimate distance & head pose
                    dist_cm = self.estimate_distance(face_landmarks)
                    fh, fw = frame.shape[:2]
                    pitch, yaw, roll = self.estimate_head_pose(face_landmarks, fw, fh)

                    # Threshold constants for ergonomic alerts
                    PITCH_DOWN_MAX = 5.0
                    PITCH_UP_MAX = 5.0
                    ROLL_MAX = 10.0
                    YAW_MAX = 20.0
                    DIST_MIN = 40.0

                    active_warnings = []
                    is_danger = False
                    if pitch is not None and pitch > PITCH_DOWN_MAX:
                        active_warnings.append(f"Head tilted down too much ({pitch:.1f}° > {PITCH_DOWN_MAX:.0f}°)")
                        is_danger = True
                    elif pitch is not None and pitch < -PITCH_UP_MAX:
                        active_warnings.append(f"Head tilted back too much ({pitch:.1f}° < -{PITCH_UP_MAX:.0f}°)")
                        is_danger = True

                    if roll is not None and abs(roll) > ROLL_MAX:
                        active_warnings.append(f"Head tilted off axis ({roll:.1f}° > {ROLL_MAX:.0f}°)")
                        is_danger = True

                    if yaw is not None and abs(yaw) > YAW_MAX:
                        active_warnings.append(f"Head turned away ({yaw:.1f}° > {YAW_MAX:.0f}°)")
                        is_danger = True

                    if dist_cm is not None and dist_cm < DIST_MIN:
                        active_warnings.append(f"Sitting too close to screen ({dist_cm:.1f} cm < {DIST_MIN:.0f} cm)")
                        is_danger = True

                    posture_status = "DANGER" if is_danger else ("WARNING" if (abs(pitch or 0) > 3.5 or abs(roll or 0) > 7.0 or abs(yaw or 0) > 15.0) else "GOOD")

                    nose_pt = face_landmarks[1] if (face_landmarks is not None and len(face_landmarks) > 1) else (fw // 2, fh // 2)
                    norm_nose_x = nose_pt[0] / fw if fw > 0 else 0.5
                    norm_nose_y = nose_pt[1] / fh if fh > 0 else 0.55

                    if hasattr(self, 'firebase_sync'):
                        self.firebase_sync.update_state(
                            pitch=pitch,
                            roll=roll,
                            yaw=yaw,
                            dist_cm=dist_cm,
                            ear=ear,
                            blinks=self.blink_counter,
                            warnings=active_warnings,
                            posture_status=posture_status,
                            nose_x=norm_nose_x,
                            nose_y=norm_nose_y
                        )

                    # Determine visualization color based on EAR
                    color = self.set_colors(ear)

                    # Draw eye landmarks & head axes at nose tip
                    self.draw_eye_landmarks(frame, face_landmarks, self.RIGHT_EYE, color)
                    self.draw_eye_landmarks(frame, face_landmarks, self.LEFT_EYE, color)
                    self.draw_head_axes(frame, face_landmarks, pitch, yaw, roll)

                    # Display Only Blinks Counter
                    DrawingUtils.draw_text_with_bg(frame, f"Blinks: {self.blink_counter}", (10, 40),
                                    font_scale=1.0, thickness=2,
                                    bg_color=color, text_color=(0, 0, 0))

                    if pitch is not None and roll is not None:
                        DrawingUtils.draw_text_with_bg(frame, f"P: {pitch:.1f} | R: {roll:.1f}", (10, 80),
                                        font_scale=0.7, thickness=2,
                                        bg_color=(30, 41, 59), text_color=(255, 255, 255))

                    # Save frame if enabled
                    if self.save_video:
                        self.out.write(frame)

                    # Display frame
                    cv.imshow("Blink Counter", frame)

                # Key controls
                wait_time = 1 if is_live_stream else (int(1000 / fps) if fps > 0 else 1)
                key = cv.waitKey(wait_time) & 0xFF
                if key == ord('p'):
                    break

            # Cleanup
            if cap and cap.isOpened():
                cap.release()
            if self.save_video:
                self.out.release()
            cv.destroyAllWindows()

        except Exception as e:
            print(f"An error occurred: {e}")


if __name__ == "__main__":
    # Direct ESP32-CAM stream URL
    input_video_path = "http://172.20.10.3:81/stream"
    
    # Create blink counter for ESP32-CAM
    blink_counter = BlinkCounter(
        video_path=input_video_path,
        ear_threshold=0.3,  
        consec_frames=4,    
        save_video=False
    )
    blink_counter.process_video()



