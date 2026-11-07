#!/usr/bin/env python3
import json
import os
import socket
import sys
import threading
import time
import wave
import subprocess

# OpenCV全体の非必要ログ・警告を非表示にする
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
if "QT_QPA_PLATFORM_PLUGIN_PATH" in os.environ:
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")

import cv2 as cv_recog
import numpy as np
from flask import Flask, request, jsonify
import mediapipe as mp

# モジュール読み込みパスの設定
sys.path.append("/home/pi/Downloads")
from sface import SFace
from yunet import YuNet

# --- SCSサーボ SDK のインポート ---
from scservo_sdk import *

# ==========================================
# ネットワーク & シリアル通信設定
# ==========================================
PI_ZERO_IP = "192.168.0.15"  # Raspberry Pi Zero 2のIPアドレス (メインコントローラー)
STREAM_URL = f"http://{PI_ZERO_IP}:5000/video_feed"
ROBOT_UDP_PORT = 5005
SERVO_TCP_PORT = 5008        # サーボ受信用TCPポート
AUDIO_SERVER_PORT = 5001     # 音声受信用Flaskポート

# FE-URT2-C001 シリアル通信設定 (Pi 5に接続)
PORT_NAME = '/dev/ttyACM0'
BAUDRATE = 1000000

# ポートとパケットハンドラーの初期化
port_handler = PortHandler(PORT_NAME)
packet_handler = scscl(port_handler)

if port_handler.openPort() and port_handler.setBaudRate(BAUDRATE):
    print(f"✅ [Pi 5] SCSサーボ用シリアルポート '{PORT_NAME}' 初期化成功")
else:
    print(f"❌ [Pi 5] SCSサーボ用シリアルポート初期化失敗（配線やポートを確認してください）")

def angle_to_position(angle: float) -> int:
    """角度（度）をSCS0009のポジション値（0〜1023）に変換する"""
    pos = int(500 + (angle * (1023.0 / 300.0)))
    return max(0, min(1023, pos))


# ==========================================
# サーボの過負荷・ストール保護チェック関数
# ==========================================
def check_and_protect_servo(servo_id: int) -> bool:
    """サーボの負荷と温度を読み出し、限界値を超えていればトルクOFFにして保護する"""
    try:
        # 1. 現在負荷の読み出し (レジスタ 60, 2バイト)
        load_raw, result, error = packet_handler.read2ByteTxRx(servo_id, 60)
        if result != COMM_SUCCESS:
            return True  # 読み出し失敗時は処理を止めない
            
        load = load_raw % 1024  0〜1023の範囲に正規化

        # 2. 現在温度の読み出し (レジスタ 63, 1バイト)
        temperature, result_temp, _ = packet_handler.read1ByteTxRx(servo_id, 63)
        temp_val = temperature if result_temp == COMM_SUCCESS else 0

        # 閾値設定（必要に応じて調整してください）
        LOAD_THRESHOLD = 750  # 過負荷・ストール判定の閾値 (max 1023)
        TEMP_THRESHOLD = 70   # 過熱保護の閾値 (℃)

        if load > LOAD_THRESHOLD:
            print(f"🚨 [過負荷警告] サーボID {servo_id} に強い負荷 (Load: {load}) を検知！ストール防止のためトルクをOFFにします。")
            packet_handler.write1ByteTxRx(servo_id, 40, 0)  # トルクOFF
            return False

        if temp_val > TEMP_THRESHOLD:
            print(f"🔥 [過熱警告] サーボID {servo_id} が高温 ({temp_val}℃) です！保護のためトルクをOFFにします。")
            packet_handler.write1ByteTxRx(servo_id, 40, 0)  # トルクOFF
            return False

    except Exception as e:
        print(f"⚠️ [Servo Safety Check Error] ID {servo_id}: {e}")
        
    return True


# ==========================================
# 音声認識＆メインコントローラーへのテキスト送信関数
# ==========================================
def process_audio_and_send(wav_filepath):
    print(f"🎙️ [Audio Processing] 音声認識を開始します: {wav_filepath}")
    recognized_text = ""
    
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(wav_filepath) as source:
            audio_data = r.record(source)
            recognized_text = r.recognize_google(audio_data, language="ja-JP")
            print(f"✍️ [STT Success] 認識結果: {recognized_text}")
    except ImportError:
        print("⚠️ speech_recognition がインストールされていません。")
        recognized_text = "こんにちは"
    except Exception as e:
        print(f"⚠️ [STT Error] 音声認識失敗: {e}")
        recognized_text = ""

    if recognized_text:
        send_text_to_robot(recognized_text)

    if os.path.exists(wav_filepath):
        os.remove(wav_filepath)

def send_text_to_robot(text):
    try:
        import requests
        url = f"http://{PI_ZERO_IP}:5000/mic_message"
        payload = {"message": text}
        requests.post(url, json=payload, timeout=3.0)
        print(f"📤 [Sent to Robot] テキストを送信しました: {text}")
    except Exception as e:
        print(f"⚠️ [Send Error] ロボットへのテキスト送信失敗: {e}")


# ==========================================
# 音声受信用 Flask ＆ SCSサーボ制御サーバー
# ==========================================
def start_audio_and_servo_server():
    app = Flask(__name__)
    UPLOAD_DIR = "./received_audio"
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    @app.route('/voice_upload', methods=['POST'])
    def voice_upload():
        try:
            audio_data = request.data
            if not audio_data:
                return jsonify({"status": "error", "message": "Empty audio"}), 400

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_filepath = os.path.join(UPLOAD_DIR, f"audio_{timestamp}.wav")

            with wave.open(wav_filepath, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data)

            print(f"📁 [Voice Received] 音声を保存しました: {wav_filepath}")
            threading.Thread(target=process_audio_and_send, args=(wav_filepath,), daemon=True).start()

            return jsonify({"status": "success", "saved": wav_filepath}), 200
        except Exception as e:
            print(f"❌ [Voice Upload Error] {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    def run_servo_tcp():
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket.bind(("0.0.0.0", SERVO_TCP_PORT))
            server_socket.listen(5)
            print(f"🚀 [Pi 5] SCSサーボ制御TCPサーバー待機中 (Port: {SERVO_TCP_PORT})...")
            while True:
                conn, _ = server_socket.accept()
                data = conn.recv(1024).decode("utf-8").strip()
                if data:
                    parts = data.split(",")
                    if len(parts) == 2:
                        servo_id = int(parts[0])  # サーボID (1や9)[cite: 5]
                        angle_str = parts[1]
                        
                        try:
                            if angle_str == "None":
                                # トルクOFF（脱力）: レジスタ40に 0 を書き込む[cite: 5]
                                packet_handler.write1ByteTxRx(servo_id, 40, 0)
                            else:
                                angle = float(angle_str)
                                
                                # 動き出す前に過負荷・ストール状態でないか安全確認
                                if not check_and_protect_servo(servo_id):
                                    conn.close()
                                    continue

                                # 1. トルクON (レジスタ40 = 1)[cite: 5]
                                packet_handler.write1ByteTxRx(servo_id, 40, 1)
                                # 2. 位置移動 (レジスタ42にポジション値を書き込む)[cite: 5]
                                position = angle_to_position(angle)
                                packet_handler.write2ByteTxRx(servo_id, 42, position)
                                
                                # 移動直後にも軽く負荷をチェックして保護
                                time.sleep(0.01)
                                check_and_protect_servo(servo_id)

                        except Exception as e:
                            print(f"⚠️ [Servo Control Error] ID {servo_id}: {e}")
                conn.close()
        except Exception as e:
            print(f"⚠️ [Servo TCP Error] {e}")

    threading.Thread(target=run_servo_tcp, daemon=True).start()

    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print(f"🌐 音声受信用APIサーバーを起動します (Port: {AUDIO_SERVER_PORT})...")
    app.run(host="0.0.0.0", port=AUDIO_SERVER_PORT, debug=False, use_reloader=False)


# ==========================================
# 最新フレームのみを保持するキャプチャクラス
# ==========================================
class FreshFrameReader:
    def __init__(self, src):
        self.cap = cv_recog.VideoCapture(src)
        self.ret = False
        self.frame = None
        self.running = True
        self.lock = threading.Lock()

        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.ret = ret
                    self.frame = frame
            else:
                time.sleep(0.01)

    def read(self):
        with self.lock:
            if self.frame is None:
                return False, None
            return self.ret, self.frame.copy()

    def isOpened(self):
        return self.cap.isOpened()

    def release(self):
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()


class FaceAndObjectServer:
    def __init__(
        self,
        model_path="/home/pi/Downloads/face_recognition_sface_2021dec.onnx",
        backend_target_id=0,
        dis_type=0,
        conf_threshold=0.5,
        input_size=(320, 320),
    ):
        self.model_path = model_path
        self.backend_target_pairs = [
            [cv_recog.dnn.DNN_BACKEND_OPENCV, cv_recog.dnn.DNN_TARGET_CPU],
            [cv_recog.dnn.DNN_BACKEND_CUDA, cv_recog.dnn.DNN_TARGET_CUDA],
            [cv_recog.dnn.DNN_BACKEND_CUDA, cv_recog.dnn.DNN_TARGET_CUDA_FP16],
            [cv_recog.dnn.DNN_BACKEND_TIMVX, cv_recog.dnn.DNN_TARGET_NPU],
            [cv_recog.dnn.DNN_BACKEND_CANN, cv_recog.dnn.DNN_TARGET_NPU],
        ]

        self.backend_id = self.backend_target_pairs[backend_target_id][0]
        self.target_id = self.backend_target_pairs[backend_target_id][1]
        self.dis_type = dis_type

        self.w = input_size[0]
        self.head_angle = 0.0

        self.recognizer = SFace(
            modelPath=self.model_path,
            disType=self.dis_type,
            backendId=self.backend_id,
            targetId=self.target_id,
        )

        self.detector = YuNet(
            modelPath="/home/pi/Downloads/face_detection_yunet_2023mar.onnx",
            inputSize=input_size,
            confThreshold=conf_threshold,
            nmsThreshold=0.3,
            topK=5000,
            backendId=self.backend_id,
            targetId=self.target_id,
        )

        self.face_images = {
            "Tsutomu": "/home/pi/Downloads/target/tsutomu.png",
            "Eriko": "/home/pi/Downloads/target/eriko.png",
        }
        self.target_faces_cache = {}
        self._preload_target_faces()

        self.MATCH_THRESHOLD = 0.42
        self.stable_status = "None"
        self.pending_status = "None"
        self.pending_count = 0
        self.REQUIRED_STABLE_FRAMES = 3

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # --- 👁️ MediaPipe Face Mesh（居眠り検出用） ---
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.LEFT_EYE = [33, 160, 158, 133, 153, 144]
        self.RIGHT_EYE = [362, 385, 387, 263, 373, 380]
        
        self.FALLBACK_EAR_THRESHOLD = 0.18
        self.EAR_DROP_RATIO = 0.70
        self.CONSEC_FRAMES = 500
        self.drowsy_counter = 0
        self.is_drowsy = False
        
        self.ear_history = []
        self.MAX_HISTORY_LEN = 150

    def _preload_target_faces(self):
        for name, path in self.face_images.items():
            if not os.path.exists(path):
                continue
            img = cv_recog.imread(path)
            if img is None:
                continue
            self.detector.setInputSize([img.shape[1], img.shape[0]])
            faces = self.detector.infer(img)
            if faces is not None and faces.shape[0] > 0:
                self.target_faces_cache[name] = (img, faces[0])

    def update_angles(self, face_center_x):
        center_x = self.w / 2.0
        offset = face_center_x - center_x
        DEADZONE = 15.0
        if abs(offset) < DEADZONE:
            return round(self.head_angle, 1)

        max_target_angle = 45.0
        target_angle = - (offset / center_x) * max_target_angle
        alpha = 0.3
        self.head_angle = (1.0 - alpha) * self.head_angle + alpha * target_angle
        self.head_angle = max(-60.0, min(60.0, self.head_angle))
        return round(self.head_angle, 1)

    def detect_faces(self, img):
        self.detector.setInputSize([img.shape[1], img.shape[0]])
        faces = self.detector.infer(img)
        return faces

    def recognition_process(self, img2):
        self.w = img2.shape[1]
        image_height, image_width, _ = img2.shape
        
        faces2 = self.detect_faces(img2)
        raw_status = "None"
        face_center_x = None
        updated_angle = round(self.head_angle, 1)

        if faces2 is not None and faces2.shape[0] > 0:
            first_face = faces2[0]
            x_min, y_min, box_w, box_h = int(first_face[0]), int(first_face[1]), int(first_face[2]), int(first_face[3])
            face_center_x = x_min + (box_w / 2.0)
            updated_angle = self.update_angles(face_center_x)

            best_score = -1.0
            best_name = "Unknown"
            for name, (img1, face1) in self.target_faces_cache.items():
                match_res = self.recognizer.match(img1, face1[:-1], img2, first_face[:-1])
                score = match_res[0]
                if score > best_score:
                    best_score = score
                    if score >= self.MATCH_THRESHOLD:
                        best_name = name
            raw_status = best_name
        else:
            updated_angle = round(self.head_angle, 1)

        if raw_status == self.stable_status:
            self.pending_status = raw_status
            self.pending_count = 0
        else:
            if raw_status == self.pending_status:
                self.pending_count += 1
            else:
                self.pending_status = raw_status
                self.pending_count = 1

            if self.pending_count >= self.REQUIRED_STABLE_FRAMES:
                self.stable_status = self.pending_status
                self.pending_count = 0

        rgb_frame = cv_recog.cvtColor(img2, cv_recog.COLOR_BGR2RGB)
        mesh_results = self.face_mesh.process(rgb_frame)
        
        current_ear = 0.0
        baseline_ear = 0.0
        dynamic_threshold = self.FALLBACK_EAR_THRESHOLD
        self.is_drowsy = False

        if mesh_results.multi_face_landmarks:
            for face_landmarks in mesh_results.multi_face_landmarks:
                landmarks = face_landmarks.landmark
                left_ear = self.calculate_ear(self.LEFT_EYE, landmarks, image_width, image_height)
                right_ear = self.calculate_ear(self.RIGHT_EYE, landmarks, image_width, image_height)
                current_ear = (left_ear + right_ear) / 2.0
                
                if current_ear > 0.19:
                    self.ear_history.append(current_ear)
                    if len(self.ear_history) > self.MAX_HISTORY_LEN:
                        self.ear_history.pop(0)
                
                if len(self.ear_history) >= 20:
                    baseline_ear = float(np.mean(self.ear_history))
                    dynamic_threshold = baseline_ear * self.EAR_DROP_RATIO
                else:
                    baseline_ear = current_ear
                    dynamic_threshold = self.FALLBACK_EAR_THRESHOLD
                
                if current_ear < dynamic_threshold:
                    self.drowsy_counter += 1
                    if self.drowsy_counter >= self.CONSEC_FRAMES:
                        self.is_drowsy = True
                else:
                    self.drowsy_counter = 0
                    self.is_drowsy = False
        else:
            self.drowsy_counter = 0
            self.is_drowsy = False

        return self.stable_status, face_center_x, updated_angle, self.is_drowsy, current_ear, baseline_ear

    def draw_status_overlay(self, frame, status, face_center_x, head_angle, is_drowsy, ear_val, baseline_val):
        cv_recog.rectangle(frame, (10, 10), (360, 140), (0, 0, 0), -1)
        
        text = f"STATUS: {status}" if status != "None" else "STATUS: No Face"
        color = (0, 255, 0) if status not in ["None", "Unknown"] else (0, 255, 255)
        cv_recog.putText(frame, text, (20, 35), cv_recog.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv_recog.LINE_AA)
        cv_recog.putText(frame, f"NECK ANGLE: {head_angle} deg", (20, 60), cv_recog.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv_recog.LINE_AA)
        cv_recog.putText(frame, f"EAR: {ear_val:.2f} (Base: {baseline_val:.2f})", (20, 85), cv_recog.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1, cv_recog.LINE_AA)
        
        if is_drowsy:
            cv_recog.putText(frame, "DROWSY ALERT!", (20, 115), cv_recog.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv_recog.LINE_AA)
        else:
            cv_recog.putText(frame, "Awake (Relative)", (20, 115), cv_recog.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv_recog.LINE_AA)

    def run(self):
        cap = FreshFrameReader(STREAM_URL)
        time.sleep(1.0)
        last_send_time = 0
        send_interval = 0.5

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            status, face_center_x, current_angle, is_drowsy, ear_val, baseline_val = self.recognition_process(frame)
            
            now = time.time()
            if now - last_send_time >= send_interval:
                payload = {
                    "name": status, 
                    "neck_angle": current_angle,
                    "is_drowsy": is_drowsy,
                    "ear": round(ear_val, 2),
                    "baseline": round(baseline_val, 2)
                }
                self.sock.sendto(json.dumps(payload).encode("utf-8"), (PI_ZERO_IP, ROBOT_UDP_PORT))
                last_send_time = now

            self.draw_status_overlay(frame, status, face_center_x, current_angle, is_drowsy, ear_val, baseline_val)
            cv_recog.imshow("Grogu Recognition Server", frame)
            if cv_recog.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(0.1)

        cap.release()
        cv_recog.destroyAllWindows()
        self.sock.close()
        
        if port_handler:
            port_handler.closePort()

    def calculate_ear(self, eye_landmarks, landmarks, image_width, image_height):
        pts = []
        for idx in eye_landmarks:
            lm = landmarks[idx]
            pts.append(np.array([lm.x * image_width, lm.y * image_height]))
        
        vertical_dist1 = np.linalg.norm(pts[1] - pts[5])
        vertical_dist2 = np.linalg.norm(pts[2] - pts[4])
        horizontal_dist = np.linalg.norm(pts[0] - pts[3])
        
        if horizontal_dist == 0:
            return 0.0
        ear = (vertical_dist1 + vertical_dist2) / (2.0 * horizontal_dist)
        return ear


if __name__ == "__main__":
    threading.Thread(target=start_audio_and_servo_server, daemon=True).start()

    server = FaceAndObjectServer()
    server.run()