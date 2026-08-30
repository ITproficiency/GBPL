import cv2
import mediapipe as mp
import numpy as np
import os
import csv
import time
import sys


def collect_face_data(output_csv="tracking_AI/dataset/face_pose_dataset.csv", source=0):
    """
    Face Mesh Data Collector Script.
    Extracts 468 3D MediaPipe face landmarks and records posture dataset to CSV.
    """
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"❌ Failed to open video source: {source}")
        return

    print("==========================================================")
    print(f"🚀 Face Landmark & Posture Data Collector Started")
    print(f"📁 Saving dataset to: {output_csv}")
    print("👉 Press 's' to Save current face frame to dataset")
    print("👉 Press 'q' to Quit collector")
    print("==========================================================")

    sample_count = 0
    file_exists = os.path.isfile(output_csv)

    with open(output_csv, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            header = []
            for i in range(468):
                header.extend([f"lm_{i}_x", f"lm_{i}_y", f"lm_{i}_z"])
            header.append("timestamp")
            writer.writerow(header)

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Stream disconnected or frame empty.")
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0]
                h, w, _ = frame.shape

                # Draw 468 landmark mesh points
                for lm in landmarks.landmark:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(frame, (cx, cy), 1, (0, 255, 120), -1)

                cv2.putText(frame, "FACE DETECTED - Press 's' to Save", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            else:
                cv2.putText(frame, "NO FACE DETECTED", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            cv2.putText(frame, f"Saved Samples: {sample_count}", (20, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.imshow("PostureCare AI - Face Data Collector", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('s') and results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0]
                row = []
                for lm in landmarks.landmark:
                    row.extend([round(lm.x, 6), round(lm.y, 6), round(lm.z, 6)])
                row.append(round(time.time(), 3))
                writer.writerow(row)
                f.flush()
                sample_count += 1
                print(f"✅ [SAMPLE #{sample_count}] Saved 468 face landmarks to {output_csv}")
            elif key == ord('q'):
                break

    cap.release()
    cv2.destroyAllWindows()
    print(f"🏁 Collection session ended. Total new samples saved: {sample_count}")


if __name__ == "__main__":
    src = 0
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        src = int(arg) if arg.isdigit() else arg
    collect_face_data(source=src)
