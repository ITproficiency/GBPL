import numpy as np
import cv2 as cv
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from FaceMeshModule import FaceMeshGenerator
from utils import DrawingUtils, ThreadedVideoStream
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
            "distance_cm": round(float(dist_cm), 1) if dist_cm is not None else 0.0,
            "ear": round(float(ear), 3) if ear is not None else 0.0,
            "blinks": int(blinks),
            "warnings": warnings if warnings else [],
            "posture_status": posture_status,
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

            # Periodically check for calibration requests from web UI (~every 1.5s)
            self.calib_check_counter += 1
            if self.calib_check_counter >= 10 and self.on_calibrate:
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

            time.sleep(0.15)  # ~6-7 Hz update rate for smooth UI sync

    def stop(self):
        self.running = False


class BlinkCounterandEARPlot:
    """
    A class to detect and count eye blinks in a video using facial landmarks.
    
    This class processes video frames to detect faces, track eye movements,
    calculate Eye Aspect Ratio (EAR), plot EAR, and count blinks in real-time.
    """
    
    # Define facial landmark indices for eyes
    RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
    LEFT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
    RIGHT_EYE_EAR = [33, 159, 158, 133, 153, 145]  # Points for EAR calculation
    LEFT_EYE_EAR = [362, 380, 374, 263, 386, 385]  # Points for EAR calculation
    
    # Define colors for visualization
    COLORS = {
        'GREEN': {'hex': '#56f10d', 'bgr': (86, 241, 13)},
        'BLUE': {'hex': '#0329fc', 'bgr': (30, 46, 209)},
        'RED': {'hex': '#f70202', 'bgr': None}
    }

    def __init__(self, video_path, threshold, consec_frames, save_video=False, output_filename=None):
        """
        Initialize the BlinkCounter with video and detection parameters.
        
        Args:
            video_path (str/int): Path to the input video file or 0 for webcam
            threshold (float): EAR threshold for blink detection
            consec_frames (int): Number of consecutive frames below threshold to count as a blink
            save_video (bool): Whether to save the processed video
            output_filename (str): Name of the output video file if saving
        """
        # Initialize core parameters (num_faces=1 for CPU optimization)
        self.generator = FaceMeshGenerator(num_faces=1)
        self.video_path = video_path
        self.EAR_THRESHOLD = threshold
        self.CONSEC_FRAMES = consec_frames
        self.cached_plot_img = None
        self.display_scale = 2.0
        self.pose_reference = None
        self.pose_reference_rotation = None
        self.last_raw_pose = None
        self.last_raw_rotation = None
        self.last_raw_roll = None
        self.roll_reference = None
        self.filtered_roll = None

        # Real-time Firebase Sync Worker
        self.firebase_sync = FirebaseSyncWorker(on_calibrate=self.calibrate_head_pose)

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
        self.K_factor = 4200.0
        self.current_eye_pixel_dist = 0.0
        
        # Initialize video saving parameters
        self._init_video_saving(save_video, output_filename)
        
        # Initialize tracking variables
        self._init_tracking_variables()
        
        # Initialize plotting
        self._init_plot()

    def calibrate_distance(self, known_distance_cm=50.0):
        """Fine-tune the distance multiplier K when user sits at a known distance (e.g. 50cm)."""
        if self.current_eye_pixel_dist > 0:
            self.K_factor = known_distance_cm * self.current_eye_pixel_dist
            print(f"[CALIBRATE SUCCESS] New K_factor: {self.K_factor:.2f} at {known_distance_cm} cm")

    def calibrate_head_pose(self):
        """Set the current head orientation as the zero-angle reference."""
        if self.last_raw_pose is not None and self.last_raw_rotation is not None:
            self.pose_reference = self.last_raw_pose
            self.pose_reference_rotation = self.last_raw_rotation.copy()
            self.roll_reference = self.last_raw_roll
            self.filtered_roll = 0.0
            print(
                "[POSE CALIBRATE SUCCESS] Reference set to "
                f"Pitch={self.pose_reference[0]:.1f}, "
                f"Yaw={self.pose_reference[1]:.1f}, "
                f"Roll={self.pose_reference[2]:.1f} degrees"
            )

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
            rmat_old, _ = cv.Rodrigues(rvec)

            # The model points use X=right, Y=up, Z=depth. Convert them to
            # the displayed axes: X=forward, Y=right, Z=up.
            old_axes_from_new_axes = np.array([
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
                [1.0, 0.0, 0.0]
            ])
            rmat_new = rmat_old @ old_axes_from_new_axes
            self.last_raw_rotation = rmat_new.copy()
            if self.pose_reference_rotation is None:
                self.pose_reference_rotation = rmat_new.copy()

            # Compare rotations as matrices before extracting Euler angles.
            # This avoids roll changing artificially when pitch changes.
            relative_rotation = self.pose_reference_rotation.T @ rmat_new
            angles, _, _, _, _, _ = cv.RQDecomp3x3(relative_rotation)

            # Use the relative rotation for pitch and yaw. Roll is measured
            # from the eye line so pitch does not create a false roll angle.
            pitch = angles[1]
            yaw = angles[2]

            eye_vector = image_points_2d[3] - image_points_2d[2]
            raw_roll = np.degrees(np.arctan2(eye_vector[1], eye_vector[0]))
            self.last_raw_roll = raw_roll
            if self.roll_reference is None:
                self.roll_reference = raw_roll
            roll = raw_roll - self.roll_reference
            roll = (roll + 180.0) % 360.0 - 180.0
            if self.filtered_roll is None:
                self.filtered_roll = roll
            else:
                self.filtered_roll = 0.75 * self.filtered_roll + 0.25 * roll
            roll = self.filtered_roll
            self.last_raw_pose = (pitch, yaw, roll)

            if self.pose_reference is None:
                self.pose_reference = self.last_raw_pose
            return pitch, yaw, roll
        return None, None, None

    def draw_head_axes(self, frame, landmarks):
        """Draw X/Y/Z head axes with the nose tip as the origin."""
        frame_h, frame_w = frame.shape[:2]
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
            self.model_points_3d,
            image_points_2d,
            camera_matrix,
            dist_coeffs,
            flags=cv.SOLVEPNP_ITERATIVE
        )
        if not success:
            return

        axis_length = 100.0
        axis_points_3d = np.array([
            (0.0, 0.0, 0.0),
            (0.0, 0.0, axis_length),
            (axis_length, 0.0, 0.0),
            (0.0, axis_length, 0.0)
        ], dtype=np.float64)
        axis_points_2d, _ = cv.projectPoints(
            axis_points_3d, rvec, tvec, camera_matrix, dist_coeffs
        )
        axis_points_2d = axis_points_2d.reshape(-1, 2).astype(int)
        origin = tuple(axis_points_2d[0])

        for endpoint, color, label in zip(
            axis_points_2d[1:],
            ((0, 0, 255), (0, 255, 0), (255, 0, 0)),
            ("X", "Y", "Z")
        ):
            endpoint = tuple(endpoint)
            cv.line(frame, origin, endpoint, color, 3, cv.LINE_AA)
            cv.putText(frame, label, endpoint, cv.FONT_HERSHEY_SIMPLEX,
                       0.7, color, 2, cv.LINE_AA)

        cv.circle(frame, origin, 4, (255, 255, 255), cv.FILLED)

    def _init_video_saving(self, save_video, output_filename):
        """Initialize video saving parameters and create output directory if needed."""
        self.save_video = save_video
        self.output_filename = output_filename
        self.out = None
        
        if self.save_video and self.output_filename:
            save_dir = "DATA/VIDEOS/OUTPUTS"
            os.makedirs(save_dir, exist_ok=True)
            self.output_filename = os.path.join(save_dir, self.output_filename)

    def _init_tracking_variables(self):
        """Initialize variables used for tracking blinks and frame processing."""
        self.blink_counter = 0
        self.frame_counter = 0
        self.frame_number = 0
        self.ear_values = []
        self.frame_numbers = []
        self.max_frames = 100
        self.new_w = self.new_h = None
        # Add default y-axis limits
        self.default_ymin = 0.18  # Typical minimum EAR value
        self.default_ymax = 0.44  # Typical maximum EAR value

    def _init_plot(self):
        """Initialize the matplotlib plot for EAR visualization."""
        # Set up dark theme plot (dpi=80 for fast CPU rendering)
        plt.style.use('dark_background')
        plt.ioff()
        self.fig, self.ax = plt.subplots(figsize=(6, 3), dpi=80)
        self.canvas = FigureCanvas(self.fig)
        
        # Configure plot aesthetics
        self._configure_plot_aesthetics()
        
        # Initialize plot data
        self._init_plot_data()

        self.fig.canvas.draw()

    def _configure_plot_aesthetics(self):
        """Configure the aesthetic properties of the plot."""
        # Set background colors
        self.fig.patch.set_facecolor('#000000')
        self.ax.set_facecolor('#000000')
        
        # Configure axes with default limits initially
        self.ax.set_ylim(self.default_ymin, self.default_ymax)
        self.ax.set_xlim(0, self.max_frames)
        
        # Set labels and title
        self.ax.set_xlabel("Frame Number", color='white', fontsize=10)
        self.ax.set_ylabel("EAR", color='white', fontsize=10)
        self.ax.set_title("Real-Time Eye Aspect Ratio (EAR)", 
                         color='white', pad=8, fontsize=14, fontweight='bold')
        
        # Configure grid and spines
        self.ax.grid(True, color='#707b7c', linestyle='--', alpha=0.7)
        for spine in self.ax.spines.values():
            spine.set_color('white')
        
        # Configure ticks and legend
        self.ax.tick_params(colors='white', which='both')

    def _init_plot_data(self):
        """Initialize the plot data and curves."""
        self.x_vals = list(range(self.max_frames))
        self.y_vals = [0] * self.max_frames
        self.Y_vals = [self.EAR_THRESHOLD] * self.max_frames
        
        # Create curves with explicit labels
        self.EAR_curve, = self.ax.plot(
            self.x_vals, 
            self.y_vals,
            color=self.COLORS['GREEN']['hex'],
            label="Eye Aspect Ratio",
            linewidth=2
        )
        
        self.threshold_line, = self.ax.plot(
            self.x_vals,
            self.Y_vals,
            color=self.COLORS['RED']['hex'],
            label="Blink Threshold",
            linewidth=2,
            linestyle='--'
        )
        
        # Add legend 
        self.legend = self.ax.legend(
            handles=[self.EAR_curve, self.threshold_line],
            loc='upper right',
            fontsize=8,
            facecolor='black',
            edgecolor='white',
            labelcolor='white',
            framealpha=0.8,
            borderpad=1,
            handlelength=2
        )

    def eye_aspect_ratio(self, eye_landmarks, landmarks):
        """
        Calculate the eye aspect ratio (EAR) for given eye landmarks.
        """
        A = np.linalg.norm(np.array(landmarks[eye_landmarks[1]]) - 
                          np.array(landmarks[eye_landmarks[5]]))
        B = np.linalg.norm(np.array(landmarks[eye_landmarks[2]]) - 
                          np.array(landmarks[eye_landmarks[4]]))
        C = np.linalg.norm(np.array(landmarks[eye_landmarks[0]]) - 
                          np.array(landmarks[eye_landmarks[3]]))
        return (A + B) / (2.0 * C)

    def _update_plot(self, ear):
        """Update the plot with new EAR values."""
        if len(self.ear_values) > self.max_frames:
            self.ear_values.pop(0)
            self.frame_numbers.pop(0)
            
        color = self.COLORS['BLUE']['hex'] if ear < self.EAR_THRESHOLD else self.COLORS['GREEN']['hex']
        
        self.EAR_curve.set_xdata(self.frame_numbers)
        self.EAR_curve.set_ydata(self.ear_values)
        self.EAR_curve.set_color(color)
        
        self.threshold_line.set_xdata(self.frame_numbers)
        self.threshold_line.set_ydata([self.EAR_THRESHOLD] * len(self.frame_numbers))
        
        if len(self.frame_numbers) > 1:
            x_min = min(self.frame_numbers)
            x_max = max(self.frame_numbers)
            if x_min == x_max:
                x_min -= 0.5
                x_max += 0.5
            self.ax.set_xlim(x_min, x_max)
        else:
            self.ax.set_xlim(0, self.max_frames)

        if self.legend not in self.ax.get_children():
            self.legend = self.ax.legend(
                handles=[self.EAR_curve, self.threshold_line],
                loc='upper right',
                fontsize=8,
                facecolor='black',
                edgecolor='white',
                labelcolor='white',
                framealpha=0.8,
                borderpad=1,
                handlelength=2
            )
        
        self.ax.draw_artist(self.ax.patch)
        self.ax.draw_artist(self.EAR_curve)
        self.ax.draw_artist(self.threshold_line)
        self.ax.draw_artist(self.legend)
        self.fig.canvas.flush_events()

    def process_frame(self, frame):
        """
        Process a single frame to detect eyes, head pose, and distance.
        """
        fh, fw, _ = frame.shape
        frame, face_landmarks = self.generator.create_face_mesh(frame, draw=False)
        
        if not face_landmarks:
            return frame, None
            
        # 1. Calculate EAR
        right_ear = self.eye_aspect_ratio(self.RIGHT_EYE_EAR, face_landmarks)
        left_ear = self.eye_aspect_ratio(self.LEFT_EYE_EAR, face_landmarks)
        ear = (right_ear + left_ear) / 2.0
        
        # 2. Estimate Distance
        dist_cm = self.estimate_distance(face_landmarks)

        # 3. Estimate Head Pose
        pitch, yaw, roll = self.estimate_head_pose(face_landmarks, fw, fh)

        # Determine visualization color
        color = self.COLORS['BLUE']['bgr'] if ear < self.EAR_THRESHOLD else self.COLORS['GREEN']['bgr']
        
        # Draw landmarks and info
        self._draw_frame_elements(frame, face_landmarks, color, dist_cm, pitch, yaw, roll, ear)
        
        return frame, ear

    def _draw_frame_elements(self, frame, landmarks, color, dist_cm=None, pitch=None, yaw=None, roll=None, ear=None):
        """Draw eye landmarks, blink counter, distance, pose & warnings on frame."""
        for eye in [self.RIGHT_EYE, self.LEFT_EYE]:
            for loc in eye:
                cv.circle(frame, (landmarks[loc]), 2, color, cv.FILLED)

        self.draw_head_axes(frame, landmarks)
        
        DrawingUtils.draw_text_with_bg(
            frame, f"Blinks: {self.blink_counter}", (10, 30),
            font_scale=0.7, thickness=2,
            bg_color=color, text_color=(0, 0, 0)
        )

        if dist_cm is not None:
            dist_bg = (0, 0, 255) if dist_cm < 40 else (30, 46, 209)
            DrawingUtils.draw_text_with_bg(
                frame, f"Dist: {dist_cm:.1f} cm", (10, 65),
                font_scale=0.6, thickness=2,
                bg_color=dist_bg, text_color=(255, 255, 255)
            )

        if pitch is not None:
            pose_color = (0, 0, 255) if abs(pitch) > 15 else (0, 255, 255)
            cv.putText(frame, f"Pitch: {pitch:.1f}deg | Yaw: {yaw:.1f}deg", (5, 98),
                       cv.FONT_HERSHEY_SIMPLEX, 0.35, pose_color, 1, cv.LINE_AA)
            cv.putText(frame, f"Roll: {roll:.1f}deg", (5, 116),
                       cv.FONT_HERSHEY_SIMPLEX, 0.35, pose_color, 1, cv.LINE_AA)

        # Warnings collection and visualization
        active_warnings = []
        is_danger = False

        warning_y = 140
        if pitch is not None and pitch > 15:
            cv.putText(frame, "CANH BAO: CUI DAU QUA SAU!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Cúi đầu quá sâu (+{pitch:.1f}° > 15°)")
            is_danger = True

        if pitch is not None and pitch < -15:
            cv.putText(frame, "CANH BAO: NGANG DAU QUA SAU!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Ngửa đầu quá cao ({pitch:.1f}° < -15°)")
            is_danger = True

        if roll is not None and abs(roll) > 15:
            cv.putText(frame, "CANH BAO: NGHIENG DAU QUA SAU!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Nghiêng đầu lệch trục ({roll:.1f}°)")
            is_danger = True

        if dist_cm is not None and dist_cm < 40:
            cv.putText(frame, "CANH BAO: NGOI QUA GAN!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            active_warnings.append(f"Ngồi quá gần màn hình ({dist_cm:.1f} cm < 40 cm)")
            is_danger = True

        posture_status = "DANGER" if is_danger else ("WARNING" if (abs(pitch or 0) > 10 or abs(roll or 0) > 10) else "GOOD")

        # Extract normalized nose tip (landmark index 1)
        fh, fw = frame.shape[:2]
        nose_pt = landmarks[1] if (landmarks is not None and len(landmarks) > 1) else (fw // 2, fh // 2)
        norm_nose_x = nose_pt[0] / fw if fw > 0 else 0.5
        norm_nose_y = nose_pt[1] / fh if fh > 0 else 0.55

        # Sync to Firebase RTDB in background thread
        if hasattr(self, 'firebase_sync'):
            self.firebase_sync.update_state(
                pitch=pitch,
                roll=roll,
                yaw=yaw,
                dist_cm=dist_cm,
                ear=ear if ear is not None else 0.3,
                blinks=self.blink_counter,
                warnings=active_warnings,
                posture_status=posture_status,
                nose_x=norm_nose_x,
                nose_y=norm_nose_y
            )

    def process_video(self):
        """Process the video / webcam / ESP32-CAM stream."""
        try:
            # Open video capture (supports 0 for webcam, file paths, or ESP32-CAM HTTP URLs)
            if isinstance(self.video_path, str) and self.video_path.startswith("http"):
                urls_to_try = [self.video_path]
                clean_url = self.video_path.rstrip("/")
                urls_to_try.extend([
                    f"{clean_url}:81/stream",
                    f"{clean_url}/stream",
                    f"{clean_url}/mjpeg"
                ])
                
                cap = None
                for url in urls_to_try:
                    print(f"[ESP32-CAM] Connecting to: {url} ...")
                    temp_cap = ThreadedVideoStream(url)
                    if temp_cap.isOpened():
                        ret, _ = temp_cap.read()
                        if ret:
                            print(f"[ESP32-CAM SUCCESS] Connected to {url}")
                            temp_cap.start()
                            cap = temp_cap
                            break
                        temp_cap.release()
                
                if cap is None or not cap.isOpened():
                    raise IOError(f"Failed to connect to ESP32-CAM stream at {self.video_path}")
            elif isinstance(self.video_path, int):
                # Webcam: use ThreadedVideoStream to avoid latency
                cap = ThreadedVideoStream(self.video_path)
                if not cap.isOpened():
                    raise IOError(f"Failed to open video source: {self.video_path}")
                cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
                cap.start()
            else:
                # Video file: use normal cv.VideoCapture to process all frames sequentially
                cap = cv.VideoCapture(self.video_path)
                if not cap.isOpened():
                    raise IOError(f"Failed to open video source: {self.video_path}")

            print("\n--- CONTROLS ---")
            print("Press 'c' to Calibrate Distance (sit 50cm from camera)")
            print("Press 'h' to Calibrate Head Pose (current angle becomes 0, 0, 0)")
            print("Press 'p' to Quit\n")
            cv.namedWindow("Video with EAR Plot & Pose Estimator", cv.WINDOW_NORMAL)

            self._process_video_frames(cap)
            
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            if 'cap' in locals() and cap and cap.isOpened():
                cap.release()
            if self.out:
                self.out.release()
            cv.destroyAllWindows()

    def _process_video_frames(self, cap):
        """Process individual frames from video capture."""
        is_live_stream = isinstance(self.video_path, int) or (
            isinstance(self.video_path, str) and self.video_path.startswith("http")
        )

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # Process frame and get EAR
            frame, ear = self.process_frame(frame)
            
            if ear is not None:
                self._update_blink_detection(ear)
                self._update_visualization(frame, ear, is_live_stream)

            wait_time = 1 if is_live_stream else 30
            key = cv.waitKey(wait_time) & 0xFF
            if key == ord('p'):
                break
            elif key == ord('c'):
                self.calibrate_distance(known_distance_cm=50.0)
            elif key == ord('h'):
                self.calibrate_head_pose()

    def _update_blink_detection(self, ear):
        """Update blink detection based on EAR value."""
        self.ear_values.append(ear)
        self.frame_numbers.append(self.frame_number)
        
        if ear < self.EAR_THRESHOLD:
            self.frame_counter += 1
        else:
            if self.frame_counter >= self.CONSEC_FRAMES:
                self.blink_counter += 1
            self.frame_counter = 0
        
        self.frame_number += 1

    def _update_visualization(self, frame, ear, is_live_stream=False):
        """Update the visualization including the plot and video output."""
        # Refresh plot image (every 2 frames on live stream for max CPU speed)
        if not is_live_stream or self.frame_number % 2 == 0 or self.cached_plot_img is None:
            self._update_plot(ear)
            self.cached_plot_img = self.plot_to_image()
            
        plot_img = self.cached_plot_img
        plot_img_resized = cv.resize(
            plot_img,
            (frame.shape[1], int(plot_img.shape[0] * frame.shape[1] / plot_img.shape[1]))
        )
        
        # Stack frame and plot vertically
        stacked_frame = cv.vconcat([frame, plot_img_resized])
        self._handle_video_output(stacked_frame)

    def _handle_video_output(self, stacked_frame):
        """Handle video output, including saving and display."""
        if self.new_w is None:
            self.new_w = stacked_frame.shape[1]
            self.new_h = stacked_frame.shape[0]
            if self.save_video:
                self.out = cv.VideoWriter(
                    self.output_filename,
                    cv.VideoWriter_fourcc(*"mp4v"),
                    30,
                    (self.new_w, self.new_h)
                )

        if self.save_video:
            self.out.write(stacked_frame)

        display_frame = cv.resize(
            stacked_frame,
            None,
            fx=self.display_scale,
            fy=self.display_scale,
            interpolation=cv.INTER_LINEAR
        )
        cv.imshow("Video with EAR Plot & Pose Estimator", display_frame)

    def plot_to_image(self):
        """Convert the matplotlib plot to an OpenCV-compatible image."""
        self.canvas.draw()
        buffer = self.canvas.buffer_rgba()
        img_array = np.asarray(buffer)
        return cv.cvtColor(img_array, cv.COLOR_RGBA2RGB)


if __name__ == "__main__":
    # ESP32-CAM HTTP Stream URL
    input_video_path = "http://172.20.10.3/"
    blink_counter = BlinkCounterandEARPlot(
        video_path=input_video_path,
        threshold=0.294,
        consec_frames=3,
        save_video=False
    )
    blink_counter.process_video()



