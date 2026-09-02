import os
os.environ["GLOG_minloglevel"] = "3"
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import cv2 as cv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from FaceMeshModule import FaceMeshGenerator
from utils import DrawingUtils, ThreadedVideoStream
import threading
import time
import json
import ssl
import urllib.request
import http.server
import socketserver
from collections import deque
from pathlib import Path

_latest_frame_lock = threading.Lock()

# Create default connecting placeholder JPEG to prevent HTTP stream timeouts
try:
    _placeholder_img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv.putText(_placeholder_img, "CONNECTING TO CAMERA...", (140, 240),
               cv.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv.LINE_AA)
    _, _placeholder_buf = cv.imencode('.jpg', _placeholder_img)
    _latest_jpeg_bytes = _placeholder_buf.tobytes()
except Exception:
    _latest_jpeg_bytes = None

class MJPEGStreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/stream', '/video_feed', '/']:
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                while True:
                    with _latest_frame_lock:
                        frame_bytes = _latest_jpeg_bytes
                    if frame_bytes is not None:
                        self.wfile.write(b'--frame\r\n')
                        self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    time.sleep(0.04)
            except Exception:
                pass
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # Suppress HTTP access logs

def start_mjpeg_server(port=8089):
    default_port = port
    for p in [default_port, default_port + 1, default_port + 2]:
        try:
            class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
                daemon_threads = True
                allow_reuse_address = True

            server = ThreadedHTTPServer(('0.0.0.0', p), MJPEGStreamHandler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            print(f"📡 [MJPEG STREAM SERVER] Relaying AI video feed at http://127.0.0.1:{p}/stream")
            return p
        except Exception as e:
            print(f"⚠️ Port {p} busy, trying next port...")
    print("❌ Could not bind MJPEG stream server to any port")


# Overlay / Firebase defaults if GET /api/rules and rules.json both fail
_DEFAULT_THRESHOLDS = {
    "pitch_down_max_deg": 5.0,
    "pitch_up_max_deg": 5.0,
    "roll_max_deg": 15.0,
    "yaw_max_deg": 20.0,
    "distance_min_cm": 40.0,
}

CAMERA_FLAGS = ("too_close", "head_too_low", "head_too_high", "head_tilted", "head_turned")
_CALIBRATION_PATH = Path(__file__).resolve().parent / "data" / "calibration.json"
_BLINK_RATE_WINDOW_SEC = 60.0


def _parse_thresholds(rules):
    hp = rules.get("head_pose") or {}
    dist = rules.get("distance_cm") or {}
    return {
        "pitch_down_max_deg": float(hp.get("pitch_down_max_deg", hp.get("pitch_forward_max_deg", 5))),
        "pitch_up_max_deg": float(hp.get("pitch_up_max_deg", 5)),
        "roll_max_deg": float(hp.get("roll_max_deg", 15)),
        "yaw_max_deg": float(hp.get("yaw_max_deg", 20)),
        "distance_min_cm": float(dist.get("target_min", 40)),
    }


def _load_posture_thresholds():
    """Load head-pose + distance thresholds: API → rules.json → hardcoded defaults."""
    api_base = os.environ.get("POSTURECARE_API_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        with urllib.request.urlopen(f"{api_base}/api/rules", timeout=2.0) as resp:
            rules = json.loads(resp.read().decode("utf-8"))
            if isinstance(rules, dict) and rules:
                print(f"📋 [RULES] Loaded thresholds from {api_base}/api/rules")
                return _parse_thresholds(rules)
    except Exception as err:
        print(f"⚠️ [RULES] API load failed ({api_base}/api/rules): {err}")

    try:
        rules_path = Path(__file__).resolve().parent.parent / "dashboard" / "gPBL" / "backend" / "data" / "rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        if isinstance(rules, dict) and rules:
            print(f"📋 [RULES] Loaded thresholds from {rules_path}")
            return _parse_thresholds(rules)
    except Exception as err:
        print(f"⚠️ [RULES] File load failed: {err}")

    print("📋 [RULES] Using hardcoded defaults")
    return dict(_DEFAULT_THRESHOLDS)


class FirebaseSyncWorker:
    """Non-blocking background worker to sync AI tracking metrics & warnings to Firebase RTDB."""
    def __init__(self, database_url="https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app", on_calibrate=None, on_calibrate_pose=None, on_calibrate_dist=None, thresholds=None):
        self.url = database_url.rstrip("/") + "/ai_data.json"
        self.latest_data = None
        self.lock = threading.Lock()
        self.running = True
        self.on_calibrate = on_calibrate
        self.on_calibrate_pose = on_calibrate_pose
        self.on_calibrate_dist = on_calibrate_dist
        self.thresholds = thresholds or dict(_DEFAULT_THRESHOLDS)
        self.last_pose_req = None
        self.last_dist_req = None
        self.calib_check_counter = 0
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()

    def update_state(
        self,
        pitch,
        roll,
        yaw,
        dist_cm,
        ear,
        blinks,
        warnings=None,
        posture_status="GOOD",
        nose_x=None,
        nose_y=None,
        blink_rate_bpm=None,
        face_present=True,
        face_lost_sec=0.0,
        flag_durations=None,
        user_calibrated=False,
    ):
        def _num(value, ndigits):
            if value is None:
                return None
            try:
                return round(float(value), ndigits)
            except (TypeError, ValueError):
                return None

        face_present = bool(face_present)
        if not face_present:
            pitch = roll = yaw = dist_cm = ear = blink_rate_bpm = None
            if posture_status in ("GOOD", "WARNING", "DANGER"):
                posture_status = "NO_FACE"
            nose_x = None
            nose_y = None

        data = {
            "face_present": face_present,
            "face_lost_sec": round(float(face_lost_sec or 0.0), 1),
            "flag_durations": {
                str(k): round(float(v), 2)
                for k, v in (flag_durations or {}).items()
                if isinstance(v, (int, float))
            },
            "pitch": _num(pitch, 2),
            "roll": _num(roll, 2),
            "yaw": _num(yaw, 2),
            "camera_distance_cm": _num(dist_cm, 1),
            "ai_distance_cm": _num(dist_cm, 1),
            "ear": _num(ear, 3),
            "blinks": int(blinks) if blinks is not None else 0,
            "blink_rate": _num(blink_rate_bpm, 1),
            "blink_rate_bpm": _num(blink_rate_bpm, 1),
            "head_pitch": _num(pitch, 2),
            "head_roll": _num(roll, 2),
            "head_yaw": _num(yaw, 2),
            "warnings": warnings if warnings else [],
            "posture_status": posture_status,
            "head_pose_thresholds": {
                "pitch_down_max_deg": self.thresholds["pitch_down_max_deg"],
                "pitch_up_max_deg": self.thresholds["pitch_up_max_deg"],
                "roll_max_deg": self.thresholds["roll_max_deg"],
                "yaw_max_deg": self.thresholds["yaw_max_deg"],
                "distance_min_cm": self.thresholds["distance_min_cm"],
            },
            "nose_x": _num(nose_x, 3),
            "nose_y": _num(nose_y, 3),
            "user_calibrated": bool(user_calibrated),
            "timestamp": time.time(),
        }
        with self.lock:
            self.latest_data = data

    def _worker_loop(self):
        last_sent = None
        sensor_url = self.url.replace("ai_data.json", "sensor_data.json")
        ctx = ssl._create_unverified_context()
        while self.running:
            data_to_send = None
            with self.lock:
                if self.latest_data is not None:
                    data_to_send = self.latest_data.copy()

            if data_to_send is not None and data_to_send != last_sent:
                try:
                    payload = json.dumps(data_to_send, ensure_ascii=False).encode('utf-8')
                    # Update /ai_data.json
                    req1 = urllib.request.Request(
                        self.url,
                        data=payload,
                        method='PATCH',
                        headers={'Content-Type': 'application/json; charset=utf-8'}
                    )
                    with urllib.request.urlopen(req1, timeout=1.5, context=ctx) as res:
                        pass
                    # Update /sensor_data.json so poller & dashboard pick up AI metrics
                    req2 = urllib.request.Request(
                        sensor_url,
                        data=payload,
                        method='PATCH',
                        headers={'Content-Type': 'application/json; charset=utf-8'}
                    )
                    with urllib.request.urlopen(req2, timeout=1.5, context=ctx) as res:
                        pass
                    last_sent = data_to_send
                except Exception as sync_err:
                    print(f"⚠️ [FIREBASE SYNC ERROR] {sync_err}")

            # Periodically check for calibration requests from web UI (~every 0.45s)
            self.calib_check_counter += 1
            if self.calib_check_counter >= 3:
                self.calib_check_counter = 0
                if self.on_calibrate_pose or self.on_calibrate:
                    try:
                        calib_url = self.url.replace(".json", "/calibrate_pose_req.json")
                        with urllib.request.urlopen(calib_url, timeout=0.8, context=ctx) as resp:
                            calib_val = json.loads(resp.read().decode('utf-8'))
                            if calib_val is not None and calib_val != self.last_pose_req:
                                self.last_pose_req = calib_val
                                fn = self.on_calibrate_pose or self.on_calibrate
                                fn()
                                print("🎯 [WEB CALIBRATE] Head Pose Calibrated via Web Dashboard!")
                    except Exception:
                        pass
                if self.on_calibrate_dist:
                    try:
                        calib_url = self.url.replace(".json", "/calibrate_dist_req.json")
                        with urllib.request.urlopen(calib_url, timeout=0.8) as resp:
                            calib_val = json.loads(resp.read().decode('utf-8'))
                            if calib_val is not None and calib_val != self.last_dist_req:
                                self.last_dist_req = calib_val
                                self.on_calibrate_dist(50.0)
                                print("🎯 [WEB CALIBRATE] Distance Calibrated (50cm) via Web Dashboard!")
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
        self.user_calibrated = False
        self.thresholds = _load_posture_thresholds()

        # Real-time Firebase Sync Worker
        self.firebase_sync = FirebaseSyncWorker(
            on_calibrate_pose=self.calibrate_head_pose,
            on_calibrate_dist=self.calibrate_distance,
            thresholds=self.thresholds,
        )
        start_mjpeg_server(port=8089)

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
        self._load_calibration()

        # Initialize video saving parameters
        self._init_video_saving(save_video, output_filename)
        
        # Initialize tracking variables
        self._init_tracking_variables()
        
        # Initialize plotting
        self._init_plot()

    def _load_calibration(self):
        """Restore K_factor and pose zero from tracking_AI/data/calibration.json if present."""
        path = _CALIBRATION_PATH
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print(f"⚠️ [CALIB] Ignored invalid calibration file: {path}")
                return

            k_factor = self.K_factor
            if data.get("K_factor") is not None:
                k_factor = float(data["K_factor"])

            pose_reference = self.pose_reference
            pose_ref = data.get("pose_reference")
            if pose_ref is not None and len(pose_ref) == 3:
                pose_reference = tuple(float(x) for x in pose_ref)

            pose_reference_rotation = self.pose_reference_rotation
            rot = data.get("pose_reference_rotation")
            if rot is not None:
                arr = np.array(rot, dtype=np.float64)
                if arr.shape == (3, 3):
                    pose_reference_rotation = arr

            roll_reference = self.roll_reference
            if data.get("roll_reference") is not None:
                roll_reference = float(data["roll_reference"])

            # Loaded user flag only; auto-zero on first frame never writes this file.
            user_calibrated = bool(data.get("user_calibrated", False))

            self.K_factor = k_factor
            self.pose_reference = pose_reference
            self.pose_reference_rotation = pose_reference_rotation
            self.roll_reference = roll_reference
            self.user_calibrated = user_calibrated
            print(
                f"📋 [CALIB] Loaded from {path} "
                f"(K={self.K_factor:.2f}, user_calibrated={self.user_calibrated})"
            )
        except Exception as err:
            print(f"⚠️ [CALIB] Failed to load {path}: {err}")

    def _save_calibration(self):
        """Persist user calibration (🎯 pose and/or distance) to calibration.json."""
        path = _CALIBRATION_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            rot = self.pose_reference_rotation
            payload = {
                "K_factor": float(self.K_factor),
                "pose_reference": (
                    [float(x) for x in self.pose_reference]
                    if self.pose_reference is not None
                    else None
                ),
                "pose_reference_rotation": rot.tolist() if rot is not None else None,
                "roll_reference": (
                    float(self.roll_reference) if self.roll_reference is not None else None
                ),
                "user_calibrated": bool(self.user_calibrated),
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"💾 [CALIB] Saved to {path}")
        except Exception as err:
            print(f"⚠️ [CALIB] Failed to save {path}: {err}")

    def calibrate_distance(self, known_distance_cm=50.0):
        """Fine-tune the distance multiplier K when user sits at a known distance (e.g. 50cm)."""
        if self.current_eye_pixel_dist > 0:
            self.K_factor = known_distance_cm * self.current_eye_pixel_dist
            self.user_calibrated = True
            self._save_calibration()
            print(f"[CALIBRATE SUCCESS] New K_factor: {self.K_factor:.2f} at {known_distance_cm} cm")

    def calibrate_head_pose(self):
        """Set the current head orientation as the zero-angle reference."""
        if self.last_raw_pose is not None and self.last_raw_rotation is not None:
            self.pose_reference = self.last_raw_pose
            self.pose_reference_rotation = self.last_raw_rotation.copy()
            self.roll_reference = self.last_raw_roll
            self.filtered_roll = 0.0
            self.user_calibrated = True
            self._save_calibration()
            print(
                "[POSE CALIBRATE SUCCESS] Reference set to "
                f"Pitch={self.pose_reference[0]:.1f}, "
                f"Yaw={self.pose_reference[1]:.1f}, "
                f"Roll={self.pose_reference[2]:.1f} degrees"
            )

    def estimate_distance(self, landmarks):
        """Estimate distance from eyes to camera (in cm) using Interpupillary Distance in pixels."""
        if not landmarks or 33 not in landmarks or 263 not in landmarks:
            return None
        left_eye = np.array(landmarks[33])
        right_eye = np.array(landmarks[263])
        self.current_eye_pixel_dist = np.linalg.norm(left_eye - right_eye)
        if self.current_eye_pixel_dist > 0:
            raw_dist = float(self.K_factor / self.current_eye_pixel_dist)
            if not hasattr(self, 'filtered_dist_cm') or self.filtered_dist_cm is None:
                self.filtered_dist_cm = raw_dist
            else:
                self.filtered_dist_cm = 0.75 * self.filtered_dist_cm + 0.25 * raw_dist
            return round(self.filtered_dist_cm, 1)
        return None

    def estimate_head_pose(self, landmarks, frame_w, frame_h):
        """Calculate Pitch, Yaw, Roll angles of the head using solvePnP safely."""
        if not landmarks or not isinstance(landmarks, dict):
            return None, None, None
        for idx in self.head_pose_landmarks:
            if idx not in landmarks:
                return None, None, None

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
            # Auto-zero on first frame only — does not mark user_calibrated.
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
                self.roll_reference = raw_roll  # first-frame auto-zero, not user calib
            roll = raw_roll - self.roll_reference
            roll = (roll + 180.0) % 360.0 - 180.0
            if self.filtered_roll is None:
                self.filtered_roll = roll
            else:
                self.filtered_roll = 0.75 * self.filtered_roll + 0.25 * roll
            roll = self.filtered_roll
            self.last_raw_pose = (pitch, yaw, roll)

            if self.pose_reference is None:
                self.pose_reference = self.last_raw_pose  # first-frame snapshot, not user calib
            return pitch, yaw, roll
        return None, None, None

    def draw_head_axes(self, frame, landmarks, pitch=None, yaw=None, roll=None, length=50):
        """Draw X/Y/Z head axes with the actual nose tip (landmark 1) as the origin,
        dynamically rotating according to the calibrated Pitch, Yaw, Roll angles."""
        if pitch is None or yaw is None or roll is None or not landmarks or 1 not in landmarks:
            return

        # Origin is always attached to the nose tip (Landmark index 1)
        origin = (int(landmarks[1][0]), int(landmarks[1][1]))

        # Convert calibrated angles from degrees to radians
        p = np.radians(pitch)
        y = np.radians(yaw)
        r = np.radians(roll)

        # Rotation matrix: Pitch (around X-axis), Yaw (around Y-axis), Roll (around Z-axis)
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

        # Combined rotation matrix (Roll * Pitch * Yaw)
        R = Rz @ Rx @ Ry

        # 3 standard axis vectors in space (pixels):
        # X-axis (Red): Points to the right
        # Y-axis (Green): Points down along the face axis
        # Z-axis (Blue): Points straight out from the nose (-Z points toward camera)
        axis_x = R @ np.array([length, 0, 0], dtype=np.float64)
        axis_y = R @ np.array([0, length, 0], dtype=np.float64)
        axis_z = R @ np.array([0, 0, -length], dtype=np.float64)

        # Project 2D onto frame from nose origin
        endpoint_x = (int(origin[0] + axis_x[0]), int(origin[1] + axis_x[1]))
        endpoint_y = (int(origin[0] + axis_y[0]), int(origin[1] + axis_y[1]))
        endpoint_z = (int(origin[0] + axis_z[0]), int(origin[1] + axis_z[1]))

        # Draw axes X (Red), Y (Green), Z (Blue)
        cv.line(frame, origin, endpoint_x, (0, 0, 255), 3, cv.LINE_AA)     # X: Red
        cv.putText(frame, "X", endpoint_x, cv.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv.LINE_AA)

        cv.line(frame, origin, endpoint_y, (0, 255, 0), 3, cv.LINE_AA)     # Y: Green
        cv.putText(frame, "Y", endpoint_y, cv.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv.LINE_AA)

        cv.line(frame, origin, endpoint_z, (255, 0, 0), 3, cv.LINE_AA)     # Z: Blue
        cv.putText(frame, "Z", endpoint_z, cv.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1, cv.LINE_AA)

        # Draw circle point at the center of the nose tip
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
        self.blink_times = deque()
        self.start_time = None
        self.frame_counter = 0
        self.frame_number = 0
        self.ear_values = []
        self.frame_numbers = []
        self.max_frames = 100
        self.new_w = self.new_h = None
        # Add default y-axis limits
        self.default_ymin = 0.18  # Typical minimum EAR value
        self.default_ymax = 0.44  # Typical maximum EAR value
        self.face_present = False
        self.face_lost_since = None
        self.last_landmarks = None
        self.flag_durations = {flag: 0.0 for flag in CAMERA_FLAGS}
        self._flag_tick_at = time.monotonic()

    def _face_lost_sec(self) -> float:
        if self.face_lost_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.face_lost_since)

    def _set_face_present(self, present: bool) -> float:
        now = time.monotonic()
        if present:
            self.face_present = True
            self.face_lost_since = None
            return 0.0
        self.face_present = False
        if self.face_lost_since is None:
            self.face_lost_since = now
        return now - self.face_lost_since

    def _tick_flag_durations(self, active_flags: set) -> dict:
        now = time.monotonic()
        last = getattr(self, "_flag_tick_at", None)
        dt = 0.0 if last is None else max(0.0, now - last)
        self._flag_tick_at = now
        if not hasattr(self, "flag_durations"):
            self.flag_durations = {flag: 0.0 for flag in CAMERA_FLAGS}
        for flag in CAMERA_FLAGS:
            if flag in active_flags:
                self.flag_durations[flag] = self.flag_durations.get(flag, 0.0) + dt
            else:
                self.flag_durations[flag] = 0.0
        return dict(self.flag_durations)

    def _sliding_blink_bpm(self):
        """Blink rate over a sliding 60s window (warmup: n / elapsed * 60)."""
        now = time.time()
        if self.start_time is None:
            self.start_time = now
        if not hasattr(self, "blink_times"):
            self.blink_times = deque()
        cutoff = now - _BLINK_RATE_WINDOW_SEC
        while self.blink_times and self.blink_times[0] < cutoff:
            self.blink_times.popleft()
        n = len(self.blink_times)
        elapsed = now - self.start_time
        window = min(elapsed, _BLINK_RATE_WINDOW_SEC)
        if window <= 0:
            return 0.0
        return round(n / window * 60.0, 1)

    def _publish_no_face(self, extra_warnings=None):
        lost_sec = self._set_face_present(False)
        self._tick_flag_durations(set())
        if hasattr(self, "firebase_sync"):
            self.firebase_sync.update_state(
                pitch=None,
                roll=None,
                yaw=None,
                dist_cm=None,
                ear=None,
                blinks=self.blink_counter,
                warnings=extra_warnings or [],
                posture_status="NO_FACE",
                blink_rate_bpm=None,
                face_present=False,
                face_lost_sec=lost_sec,
                flag_durations=self.flag_durations,
                user_calibrated=getattr(self, "user_calibrated", False),
            )

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
        Calculate the eye aspect ratio (EAR) for given eye landmarks safely.
        """
        if not landmarks or not isinstance(landmarks, dict):
            return 0.3
        for idx in eye_landmarks:
            if idx not in landmarks:
                return 0.3
        try:
            A = np.linalg.norm(np.array(landmarks[eye_landmarks[1]]) - 
                              np.array(landmarks[eye_landmarks[5]]))
            B = np.linalg.norm(np.array(landmarks[eye_landmarks[2]]) - 
                              np.array(landmarks[eye_landmarks[4]]))
            C = np.linalg.norm(np.array(landmarks[eye_landmarks[0]]) - 
                              np.array(landmarks[eye_landmarks[3]]))
            return (A + B) / (2.0 * C) if C > 0 else 0.3
        except Exception:
            return 0.3

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
        
        # Auto-gamma low-light boost if camera feed is dark
        try:
            mean_lum = float(np.mean(frame))
            if mean_lum < 60.0:
                gamma = 1.7 if mean_lum < 30.0 else 1.4
                inv_gamma = 1.0 / gamma
                table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
                frame = cv.LUT(frame, table)
        except Exception:
            pass

        frame, face_landmarks = self.generator.create_face_mesh(frame, draw=False)
        
        if face_landmarks and 33 in face_landmarks and 263 in face_landmarks:
            self.last_landmarks = face_landmarks
            right_ear = self.eye_aspect_ratio(self.RIGHT_EYE_EAR, face_landmarks)
            left_ear = self.eye_aspect_ratio(self.LEFT_EYE_EAR, face_landmarks)
            ear = (right_ear + left_ear) / 2.0
            dist_cm = self.estimate_distance(face_landmarks)
            pitch, yaw, roll = self.estimate_head_pose(face_landmarks, fw, fh)
            color = self.COLORS['BLUE']['bgr'] if ear < self.EAR_THRESHOLD else self.COLORS['GREEN']['bgr']
            self._draw_frame_elements(frame, face_landmarks, color, dist_cm, pitch, yaw, roll, ear)
            return frame, ear
        else:
            lost_sec = self._set_face_present(False)
            last_pose = getattr(self, "last_raw_pose", None)
            last_lm = getattr(self, "last_landmarks", None)
            if last_pose and last_lm:
                self.draw_head_axes(frame, last_lm, last_pose[0], last_pose[1], last_pose[2])
            DrawingUtils.draw_text_with_bg(
                frame, f"Blinks: {self.blink_counter}", (10, 30),
                font_scale=0.7, thickness=2,
                bg_color=self.COLORS['RED']['bgr'] or (0, 0, 255),
                text_color=(0, 0, 0)
            )
            cv.putText(
                frame,
                f"FACE LOST ({lost_sec:.0f}s)",
                (10, 65),
                cv.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 255),
                2,
                cv.LINE_AA,
            )
            self._publish_no_face()
            global _latest_jpeg_bytes
            try:
                ret_jpg, jpeg_buf = cv.imencode('.jpg', frame, [cv.IMWRITE_JPEG_QUALITY, 92])
                if ret_jpg:
                    with _latest_frame_lock:
                        _latest_jpeg_bytes = jpeg_buf.tobytes()
            except Exception:
                pass
            return frame, None

    def _draw_frame_elements(self, frame, landmarks, color, dist_cm=None, pitch=None, yaw=None, roll=None, ear=None):
        """Draw eye landmarks, blink counter, distance, pose & warnings on frame."""
        if landmarks and isinstance(landmarks, dict):
            for eye in [self.RIGHT_EYE, self.LEFT_EYE]:
                for loc in eye:
                    if loc in landmarks:
                        cv.circle(frame, landmarks[loc], 2, color, cv.FILLED)

        self.draw_head_axes(frame, landmarks, pitch, yaw, roll)
        
        DrawingUtils.draw_text_with_bg(
            frame, f"Blinks: {self.blink_counter}", (10, 30),
            font_scale=0.7, thickness=2,
            bg_color=color, text_color=(0, 0, 0)
        )

        if dist_cm is not None:
            dist_bg = (0, 0, 255) if dist_cm < self.thresholds["distance_min_cm"] else (30, 46, 209)
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

        # Thresholds from GET /api/rules → rules.json → hardcoded defaults
        PITCH_DOWN_MAX = self.thresholds["pitch_down_max_deg"]
        PITCH_UP_MAX = self.thresholds["pitch_up_max_deg"]
        ROLL_MAX = self.thresholds["roll_max_deg"]
        YAW_MAX = self.thresholds["yaw_max_deg"]
        DIST_MIN = self.thresholds["distance_min_cm"]

        # Warnings collection and visualization
        active_warnings = []
        is_danger = False
        active_flags = set()

        warning_y = 140
        if pitch is not None and pitch > PITCH_DOWN_MAX:
            cv.putText(frame, "WARNING: HEAD TOO LOW!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Head tilted down too much ({pitch:.1f}° > {PITCH_DOWN_MAX:.0f}°)")
            is_danger = True
            active_flags.add("head_too_low")

        if pitch is not None and pitch < -PITCH_UP_MAX:
            cv.putText(frame, "WARNING: HEAD TOO HIGH!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Head tilted back too much ({pitch:.1f}° < -{PITCH_UP_MAX:.0f}°)")
            is_danger = True
            active_flags.add("head_too_high")

        if roll is not None and abs(roll) > ROLL_MAX:
            cv.putText(frame, "WARNING: HEAD TILTED!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Head tilted off axis ({roll:.1f}° > {ROLL_MAX:.0f}°)")
            is_danger = True
            active_flags.add("head_tilted")

        if yaw is not None and abs(yaw) > YAW_MAX:
            cv.putText(frame, "WARNING: HEAD TURNED!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2, cv.LINE_AA)
            warning_y += 25
            active_warnings.append(f"Head turned away ({yaw:.1f}° > {YAW_MAX:.0f}°)")
            active_flags.add("head_turned")

        if dist_cm is not None and dist_cm < DIST_MIN:
            cv.putText(frame, "WARNING: TOO CLOSE!", (10, warning_y),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv.LINE_AA)
            active_warnings.append(f"Sitting too close to screen ({dist_cm:.1f} cm < {DIST_MIN:.0f} cm)")
            is_danger = True
            active_flags.add("too_close")

        self._set_face_present(True)
        flag_durations = self._tick_flag_durations(active_flags)

        posture_status = "DANGER" if is_danger else ("WARNING" if (abs(pitch or 0) > 3.5 or abs(roll or 0) > 7.0 or abs(yaw or 0) > 15.0) else "GOOD")

        # Extract normalized nose tip (landmark index 1)
        fh, fw = frame.shape[:2]
        nose_pt = landmarks[1] if (landmarks is not None and 1 in landmarks) else (fw // 2, fh // 2)
        norm_nose_x = nose_pt[0] / fw if fw > 0 else 0.5
        norm_nose_y = nose_pt[1] / fh if fh > 0 else 0.55

        calc_bpm = self._sliding_blink_bpm()

        # Sync to Firebase RTDB in background thread
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
                nose_y=norm_nose_y,
                blink_rate_bpm=calc_bpm,
                face_present=True,
                face_lost_sec=0.0,
                flag_durations=flag_durations,
                user_calibrated=self.user_calibrated,
            )

        # Encode processed frame with 3D pose & landmarks overlay for local web streaming (High Resolution Crisp Quality: 92)
        global _latest_jpeg_bytes
        try:
            ret_jpg, jpeg_buf = cv.imencode('.jpg', frame, [cv.IMWRITE_JPEG_QUALITY, 92])
            if ret_jpg:
                with _latest_frame_lock:
                    _latest_jpeg_bytes = jpeg_buf.tobytes()
        except Exception:
            pass

    def process_video(self):
        """Process the video / webcam / ESP32-CAM stream."""
        try:
            # Open video capture (supports 0 for webcam, file paths, or ESP32-CAM HTTP URLs)
            if isinstance(self.video_path, str) and self.video_path.startswith("http"):
                print(f"📡 [ESP32-CAM STREAM ENGINE] Starting HTTP stream decoder for: {self.video_path}")
                cap = ThreadedVideoStream(self.video_path)
                cap.start()
            elif isinstance(self.video_path, int):
                # Webcam: use ThreadedVideoStream to avoid latency
                cap = ThreadedVideoStream(self.video_path)
                if cap.stream is not None:
                    cap.set(cv.CAP_PROP_FRAME_WIDTH, 1280)
                    cap.set(cv.CAP_PROP_FRAME_HEIGHT, 720)
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
            if getattr(self, 'show_gui', True):
                try:
                    cv.namedWindow("Video with EAR Plot & Pose Estimator", cv.WINDOW_NORMAL)
                except Exception:
                    pass

            self._process_video_frames(cap)
            
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            if 'cap' in locals() and cap and cap.isOpened():
                cap.release()
            if self.out:
                self.out.release()
            if getattr(self, 'show_gui', True):
                try:
                    cv.destroyAllWindows()
                except Exception:
                    pass

    def _process_video_frames(self, cap):
        """Process individual frames from video capture with auto-reconnect resilience."""
        is_live_stream = isinstance(self.video_path, int) or (
            isinstance(self.video_path, str) and self.video_path.startswith("http")
        )

        consecutive_read_failures = 0
        while getattr(self, 'running', True):
            ret, frame = False, None
            if cap is not None and hasattr(cap, 'isOpened') and cap.isOpened():
                try:
                    ret, frame = cap.read()
                except Exception:
                    ret, frame = False, None

            if not ret or frame is None:
                consecutive_read_failures += 1
                if hasattr(self, 'firebase_sync') and consecutive_read_failures % 10 == 0:
                    self._publish_no_face(
                        extra_warnings=[f"Connecting to video stream ({self.video_path})..."]
                    )
                time.sleep(0.05)

                # Reconnect attempt every 3 seconds for HTTP / RTSP / live stream
                if is_live_stream and consecutive_read_failures % 60 == 0:
                    print(f"🔄 Reconnecting to video stream: {self.video_path}...")
                    try:
                        if cap and hasattr(cap, 'release'):
                            cap.release()
                        if isinstance(self.video_path, str) and self.video_path.startswith("http"):
                            cap = ThreadedVideoStream(self.video_path)
                            cap.start()
                        elif isinstance(self.video_path, int):
                            cap = ThreadedVideoStream(self.video_path)
                            if cap.stream is not None:
                                cap.set(cv.CAP_PROP_FRAME_WIDTH, 1280)
                                cap.set(cv.CAP_PROP_FRAME_HEIGHT, 720)
                            cap.start()
                        else:
                            cap = cv.VideoCapture(self.video_path)
                    except Exception as err:
                        print(f"⚠️ Reconnect error: {err}")
                continue

            consecutive_read_failures = 0

            # Process frame safely without stopping the script on unexpected frame errors
            try:
                frame, ear = self.process_frame(frame)
                if ear is not None:
                    self._update_blink_detection(ear)
                    self._update_visualization(frame, ear, is_live_stream)
            except Exception as frame_err:
                import traceback
                print(f"⚠️ [FRAME SKIP] Exception: {frame_err}\n{traceback.format_exc()}")

            if getattr(self, 'show_gui', True):
                wait_time = 1 if is_live_stream else 30
                key = cv.waitKey(wait_time) & 0xFF
                if key == ord('p'):
                    break
                elif key == ord('c'):
                    self.calibrate_distance(known_distance_cm=50.0)
                elif key == ord('h'):
                    self.calibrate_head_pose()
            else:
                time.sleep(0.01)

    def _update_blink_detection(self, ear):
        """Update blink detection based on EAR value."""
        self.ear_values.append(ear)
        self.frame_numbers.append(self.frame_number)
        
        if ear < self.EAR_THRESHOLD:
            self.frame_counter += 1
        else:
            if self.frame_counter >= self.CONSEC_FRAMES:
                self.blink_counter += 1
                if not hasattr(self, "blink_times"):
                    self.blink_times = deque()
                self.blink_times.append(time.time())
            self.frame_counter = 0
        
        self.frame_number += 1

    def _update_visualization(self, frame, ear, is_live_stream=False):
        """Update the visualization including the plot and video output."""
        if not getattr(self, 'show_gui', True) and not getattr(self, 'save_video', False):
            return

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

        if getattr(self, 'show_gui', True):
            try:
                display_frame = cv.resize(
                    stacked_frame,
                    None,
                    fx=self.display_scale,
                    fy=self.display_scale,
                    interpolation=cv.INTER_LINEAR
                )
                cv.imshow("Video with EAR Plot & Pose Estimator", display_frame)
            except Exception:
                pass

    def plot_to_image(self):
        """Convert the matplotlib plot to an OpenCV-compatible image."""
        self.canvas.draw()
        buffer = self.canvas.buffer_rgba()
        img_array = np.asarray(buffer)
        return cv.cvtColor(img_array, cv.COLOR_RGBA2RGB)


if __name__ == "__main__":
    import sys
    input_video_path = 0
    show_gui = True

    for a in sys.argv[1:]:
        if a in ("--no-gui", "--headless"):
            show_gui = False
        elif a.isdigit():
            input_video_path = int(a)
        elif a.lower() in ("0", "webcam", "cam"):
            input_video_path = 0
        elif not a.startswith("--"):
            input_video_path = a

    print(f"🚀 Starting PostureCare AI Tracking with video source: {input_video_path} (GUI: {show_gui})")
    blink_counter = BlinkCounterandEARPlot(
        video_path=input_video_path,
        threshold=0.294,
        consec_frames=3,
        save_video=False
    )
    blink_counter.show_gui = show_gui
    blink_counter.process_video()



