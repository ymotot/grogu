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

port_handler = PortHandler(PORT_NAME)
packet_handler = scscl(port_handler)

if port_handler.openPort() and port_handler.setBaudRate(BAUDRATE):
    print(f"✅ [Pi 5] SCSサーボ用シリアルポート '{PORT_NAME}' 初期化成功")
else:
    print(f"❌ [Pi 5] SCSサーボ用シリアルポート初期化失敗")

def angle_to_position(angle: float) -> int:
    pos = int(500 + (angle * (1023.0 / 300.0)))
    return max(0, min(1023, pos))

def check_and_protect_servo(servo_id: int) -> bool:
    try:
        load_raw, result, _ = packet_handler.read2ByteTxRx(servo_id, 60)
        if result != COMM_SUCCESS:
            return True
        load = load_raw % 1024
        temperature, result_temp, _ = packet_handler.read1ByteTxRx(servo_id, 63)
        temp_val = temperature if result_temp == COMM_SUCCESS else 0

        if load > 750:
            print(f"🚨 [過負荷警告] サーボID {servo_id} (Load: {load}) トルクOFF")
            packet_handler.write1ByteTxRx(servo_id, 40, 0)
            return False
        if temp_val > 70:
            print(f"🔥 [過熱警告] サーボID {servo_id} ({temp_val}℃) トルクOFF")
            packet_handler.write1ByteTxRx(servo_id, 40, 0)
            return False
    except Exception as e:
        print(f"⚠️ [Servo Safety Check Error] ID {servo_id}: {e}")
    return True

def process_audio_and_send(wav_filepath):
    print(f"🎙️ [Audio Processing] 音声認識開始: {wav_filepath}")
    recognized_text = ""
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(wav_filepath) as source:
            audio_data = r.record(source)
            recognized_text = r.recognize_google(audio_data, language="ja-JP")
            print(f"✍️ [STT Success] 認識結果: {recognized_text}")
    except Exception as e:
        print(f"⚠️ [STT Error] 音声認識失敗: {e}")

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
    except Exception as e:
        print(f"⚠️ [Send Error] ロボットへのテキスト送信失敗: {e}")

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
            threading.Thread(target=process_audio_and_send, args=(wav_filepath,), daemon=True).start()
            return jsonify({"status": "success", "saved": wav_filepath}), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def run_servo_tcp():
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket.bind(("0.0.0.0", SERVO_TCP_PORT))
            server_socket.listen(5)
            while True:
                conn, _ = server_socket.accept()
                data = conn.recv(1024).decode("utf-8").strip()
                if data:
                    parts = data.split(",")
                    if len(parts) == 2:
                        servo_id, angle_str = int(parts[0]), parts[1]
                        try:
                            if angle_str == "None":
                                packet_handler.write1ByteTxRx(servo_id, 40, 0)
                            else:
                                angle = float(angle_str)
                                if not check_and_protect_servo(servo_id):
                                    conn.close()
                                    continue
                                packet_handler.write1ByteTxRx(servo_id, 40, 1)
                                position = angle_to_position(angle)
                                packet_handler.write2ByteTxRx(servo_id, 42, position)
                        except Exception as e:
                            print(f"⚠️ [Servo Control Error] ID {servo_id}: {e}")
                conn.close()
        except Exception as e:
            print(f"⚠️ [Servo TCP Error] {e}")

    threading.Thread(target=run_servo_tcp, daemon=True).start()
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=AUDIO_SERVER_PORT, debug=False, use_reloader=False)

# grogu_support8.py の上部や適当な位置に DHT11Reader クラスを追加

class DHT11Reader:
    def __init__(self, device_path="/sys/bus/iio/devices/iio:device0", max_retries=3):
        self.device_path = device_path
        self.max_retries = max_retries

    def read(self):
        for _ in range(self.max_retries):
            try:
                with open(f"{self.device_path}/in_temp_input", "r") as f:
                    temp = int(f.read().strip()) / 1000.0
                with open(f"{self.device_path}/in_humidityrelative_input", "r") as f:
                    humidity = int(f.read().strip()) / 1000.0
                return temp, humidity
            except (OSError, ValueError):
                time.sleep(0.5)
        return None, None

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

        self.recognizer = SFace(modelPath=self.model_path, disType=self.dis_type, backendId=self.backend_id, targetId=self.target_id)
        self.detector = YuNet(modelPath="/home/pi/Downloads/face_detection_yunet_2023mar.onnx", inputSize=input_size, confThreshold=conf_threshold, nmsThreshold=0.3, topK=5000, backendId=self.backend_id, targetId=self.target_id)

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
        
        # --- MediaPipe Pose 関連（全身ポーズ検知） ---
        self.mp_pose = mp.solutions.pose
        self.pose_detector = self.mp_pose.Pose(min_detection_confidence=0.7, min_tracking_confidence=0.7)
        self.mp_drawing = mp.solutions.drawing_utils

        # ポーズ検知・カウンタ関連
        self.counter_hand_raise = 0
        self.counter_arm_fold = 0
        self.counter_sitting = 0
        self.counter_lying = 0
        self.POSE_THRESHOLD = 15

        self.in_state_hand_raise = False
        self.in_state_arm_fold = False
        self.in_state_sitting = False
        self.in_state_lying = False
        self.latest_detected_trigger = None

        # フレームスキップ用カウンタ（負荷軽減用：3フレームに1回処理）
        self.pose_frame_counter = 0
        self.POSE_SKIP_FRAMES = 3

    def _preload_target_faces(self):
        for name, path in self.face_images.items():
            if not os.path.exists(path): continue
            img = cv_recog.imread(path)
            if img is None: continue
            self.detector.setInputSize([img.shape[1], img.shape[0]])
            faces = self.detector.infer(img)
            if faces is not None and faces.shape[0] > 0:
                self.target_faces_cache[name] = (img, faces[0])

    def update_angles(self, face_center_x):
        center_x = self.w / 2.0
        offset = face_center_x - center_x
        DEADZONE = 15.0
        if abs(offset) < DEADZONE: return round(self.head_angle, 1)
        max_target_angle = 45.0
        target_angle = - (offset / center_x) * max_target_angle
        alpha = 0.3
        self.head_angle = (1.0 - alpha) * self.head_angle + alpha * target_angle
        self.head_angle = max(-60.0, min(60.0, self.head_angle))
        return round(self.head_angle, 1)

    def detect_faces(self, img):
        self.detector.setInputSize([img.shape[1], img.shape[0]])
        return self.detector.infer(img)

    def recognition_process(self, img2):
        # 左右反転（ミラー化）
        img2 = cv_recog.flip(img2, 1)
        self.w = img2.shape[1]
        
        faces2 = self.detect_faces(img2)
        raw_status = "None"
        face_center_x = None
        updated_angle = round(self.head_angle, 1)

        if faces2 is not None and faces2.shape[0] > 0:
            first_face = faces2[0]
            x_min, box_w = int(first_face[0]), int(first_face[2])
            face_center_x = x_min + (box_w / 2.0)
            updated_angle = self.update_angles(face_center_x)

            best_score = -1.0
            best_name = "Unknown"
            for name, (img1, face1) in self.target_faces_cache.items():
                score = self.recognizer.match(img1, face1[:-1], img2, first_face[:-1])[0]
                if score > best_score:
                    best_score = score
                    if score >= self.MATCH_THRESHOLD: best_name = name
            raw_status = best_name

        if raw_status == self.stable_status:
            self.pending_status = raw_status
            self.pending_count = 0
        else:
            if raw_status == self.pending_status: self.pending_count += 1
            else:
                self.pending_status = raw_status
                self.pending_count = 1
            if self.pending_count >= self.REQUIRED_STABLE_FRAMES:
                self.stable_status = self.pending_status
                self.pending_count = 0

        rgb_frame = cv_recog.cvtColor(img2, cv_recog.COLOR_BGR2RGB)
        
        # --- ポーズ検知（4大ポーズ & フレームスキップ間引き） ---
        self.pose_frame_counter += 1
        if self.pose_frame_counter >= self.POSE_SKIP_FRAMES:
            self.pose_frame_counter = 0
            pose_results = self.pose_detector.process(rgb_frame)
        else:
            pose_results = None

        self.latest_detected_trigger = None

        if pose_results and pose_results.pose_landmarks:
            self.mp_drawing.draw_landmarks(img2, pose_results.pose_landmarks, self.mp_pose.POSE_CONNECTIONS)
            lm = pose_results.pose_landmarks.landmark
            
            # 1. 手を上げる
            is_hand_raised = (lm[15].y < lm[11].y) or (lm[16].y < lm[12].y)
            if is_hand_raised:
                self.counter_hand_raise += 1
                if self.counter_hand_raise >= self.POSE_THRESHOLD and not self.in_state_hand_raise:
                    self.latest_detected_trigger = "hand_raised"
                    self.in_state_hand_raise = True
            else:
                self.counter_hand_raise = max(0, self.counter_hand_raise - 2)
                if self.counter_hand_raise < 5: self.in_state_hand_raise = False

            # 2. 腕組み
            is_crossed = (lm[15].x < lm[13].x) and (lm[16].x > lm[14].x)
            is_leveled = abs((lm[15].y - lm[13].y) + (lm[16].y - lm[14].y)) < 0.2
            if is_crossed and is_leveled:
                self.counter_arm_fold += 1
                if self.counter_arm_fold >= self.POSE_THRESHOLD and not self.in_state_arm_fold:
                    self.latest_detected_trigger = "arm_folded"
                    self.in_state_arm_fold = True
            else:
                self.counter_arm_fold = max(0, self.counter_arm_fold - 2)
                if self.counter_arm_fold < 5: self.in_state_arm_fold = False

            # --- 3. 座る / 4. 寝る（肩と腰の位置を考慮した高度な判定） ---
            nose_y = lm[0].y
            
            # 肩と腰のY座標の平均を計算（MediaPipeのインデックス: 肩=11,12 / 腰=23,24）
            shoulder_y = (lm[11].y + lm[12].y) / 2
            hip_y = (lm[23].y + lm[24].y) / 2
            
            # 胴体の縦方向の長さ（上体が起きているほど大きくなり、寝転ぶと小さくなる）
            torso_height = hip_y - shoulder_y

            # 判定条件の定義
            # 【座っている】鼻が極端に低くなく、かつ上体が起きている（胴体の縦幅が一定以上ある）
            is_sit = (0.35 <= nose_y < 0.72) and (torso_height > 0.12)
            
            # 【寝ている】鼻の位置が低い、または上体が倒れていて胴体の縦幅が極端に潰れている
            is_lie = (nose_y >= 0.70) or (torso_height <= 0.12)

            if is_sit:
                self.counter_sitting += 1
                self.counter_lying = 0
                self.in_state_lying = False
                
                if self.counter_sitting >= self.POSE_THRESHOLD and not self.in_state_sitting:
                    self.latest_detected_trigger = "sitting"
                    self.in_state_sitting = True
            else:
                self.counter_sitting = max(0, self.counter_sitting - 2)
                if self.counter_sitting < 5: self.in_state_sitting = False

            if is_lie:
                self.counter_lying += 1
                self.counter_sitting = 0
                self.in_state_sitting = False
                
                if self.counter_lying >= self.POSE_THRESHOLD and not self.in_state_lying:
                    self.latest_detected_trigger = "lying"
                    self.in_state_lying = True
            else:
                self.counter_lying = max(0, self.counter_lying - 2)
                if self.counter_lying < 5: self.in_state_lying = False    
        elif pose_results is None:
            pass  # スキップ時は状態を維持
        else:
            self.counter_hand_raise = max(0, self.counter_hand_raise - 1)
            self.counter_arm_fold = max(0, self.counter_arm_fold - 1)
            self.counter_sitting = max(0, self.counter_sitting - 1)
            self.counter_lying = max(0, self.counter_lying - 1)
            if self.counter_hand_raise == 0: self.in_state_hand_raise = False
            if self.counter_arm_fold == 0: self.in_state_arm_fold = False
            if self.counter_sitting == 0: self.in_state_sitting = False
            if self.counter_lying == 0: self.in_state_lying = False
            
        # ▼【追加】有効な顔（人物名）が認識されている場合のみ、ポーズトリガーを有効にする
        if self.latest_detected_trigger is not None:
            if self.stable_status == "None" or self.stable_status == "Unknown":
                # 顔が認識されていない、またはUnknownの場合はトリガーを破棄する
                self.latest_detected_trigger = None

        return self.stable_status, face_center_x, updated_angle, self.latest_detected_trigger, img2

#     def draw_status_overlay(self, frame, status, face_center_x, head_angle, trigger):
#         cv_recog.rectangle(frame, (10, 10), (380, 130), (0, 0, 0), -1)
#         text = f"STATUS: {status}" if status != "None" else "STATUS: No Face"
#         color = (0, 255, 0) if status not in ["None", "Unknown"] else (0, 255, 255)
#         cv_recog.putText(frame, text, (20, 30), cv_recog.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv_recog.LINE_AA)
#         cv_recog.putText(frame, f"NECK ANGLE: {head_angle} deg", (20, 55), cv_recog.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv_recog.LINE_AA)
#         
#         status_str = f"R:{self.counter_hand_raise} F:{self.counter_arm_fold} S:{self.counter_sitting} L:{self.counter_lying}"
#         cv_recog.putText(frame, status_str, (20, 80), cv_recog.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv_recog.LINE_AA)
# 
#         if trigger:
#             cv_recog.putText(frame, f"TRIGGER: {trigger.upper()}!", (20, 105), cv_recog.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2, cv_recog.LINE_AA)
#         else:
#             cv_recog.putText(frame, "Monitoring poses...", (20, 105), cv_recog.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv_recog.LINE_AA)

    def draw_status_overlay(self, frame, status, face_center_x, head_angle, trigger):
        h, w = frame.shape[:2]
        footer_height = 80  # テキスト用の下部エリアの高さ
        
        # 元の映像の下に黒い余白（フッター）を結合する
        footer = np.zeros((footer_height, w, 3), dtype=np.uint8)
        combined_frame = np.vstack((frame, footer))
        
        # 結合したエリア（下部の黒帯部分）にテキストを描画
        text = f"STATUS: {status}" if status != "None" else "STATUS: No Face"
        color = (0, 255, 0) if status not in ["None", "Unknown"] else (0, 255, 255)
        cv_recog.putText(combined_frame, text, (15, h + 22), cv_recog.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv_recog.LINE_AA)
        cv_recog.putText(combined_frame, f"NECK ANGLE: {head_angle} deg", (15, h + 45), cv_recog.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv_recog.LINE_AA)
        
        status_str = f"R:{self.counter_hand_raise} F:{self.counter_arm_fold} S:{self.counter_sitting} L:{self.counter_lying}"
        cv_recog.putText(combined_frame, status_str, (15, h + 68), cv_recog.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv_recog.LINE_AA)

        if trigger:
            cv_recog.putText(combined_frame, f"TRIGGER: {trigger.upper()}!", (210, h + 22), cv_recog.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2, cv_recog.LINE_AA)
        else:
            cv_recog.putText(combined_frame, "Monitoring...", (210, h + 22), cv_recog.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1, cv_recog.LINE_AA)

        return combined_frame  # 拡張したフレームを返す

    def read_all_servo_statuses(self, servo_ids=[1, 9]):
        servo_statuses = {}
        for servo_id in servo_ids:
            try:
                load_raw, r1, _ = packet_handler.read2ByteTxRx(servo_id, 60)
                load = (load_raw % 1024) if r1 == COMM_SUCCESS else 0
                vol_raw, r2, _ = packet_handler.read1ByteTxRx(servo_id, 62)
                voltage = (vol_raw * 0.1) if r2 == COMM_SUCCESS else 0.0
                temp_raw, r3, _ = packet_handler.read1ByteTxRx(servo_id, 63)
                temperature = temp_raw if r3 == COMM_SUCCESS else 0
                mov_raw, r4, _ = packet_handler.read1ByteTxRx(servo_id, 66)
                is_moving = mov_raw if r4 == COMM_SUCCESS else 0

                servo_statuses[str(servo_id)] = {
                    "load": load, "voltage": round(voltage, 1), "temperature": temperature, "is_moving": is_moving
                }
            except Exception:
                servo_statuses[str(servo_id)] = {"load": 0, "voltage": 0.0, "temperature": 0, "is_moving": 0}
        return servo_statuses

    def run(self):
        cap = FreshFrameReader(STREAM_URL)
        time.sleep(1.0)
        last_send_time = 0
        send_interval = 0.3

        # DHT11センサーリーダーの初期化
        dht_reader = DHT11Reader()

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            status, face_center_x, current_angle, trigger, processed_frame = self.recognition_process(frame)
            
            all_servo_status = self.read_all_servo_statuses([1, 9])

            # 🌡️ DHT11センサーから温度と湿度を読み取る
            temp_val, humidity_val = dht_reader.read()

            now = time.time()
            if now - last_send_time >= send_interval:
                payload = {
                    "name": status, 
                    "neck_angle": current_angle,
                    "pose_trigger": trigger,          # 検出されたポーズトリガー
                    "servo_statuses": all_servo_status,
                    "temperature": temp_val,           # 送信データに温度を追加
                    "humidity": humidity_val           # 送信データに湿度を追加
                }
                self.sock.sendto(json.dumps(payload).encode("utf-8"), (PI_ZERO_IP, ROBOT_UDP_PORT))
                last_send_time = now
    
            # テキストエリアが下部に結合されたフレームを受け取って表示する
            display_frame = self.draw_status_overlay(processed_frame, status, face_center_x, current_angle, trigger)
            
            cv_recog.imshow("Grogu Recognition Server", display_frame)
            if cv_recog.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(0.01)
        
        cap.release()
        cv_recog.destroyAllWindows()
        self.sock.close()
        if port_handler: port_handler.closePort()

if __name__ == "__main__":
    threading.Thread(target=start_audio_and_servo_server, daemon=True).start()
    server = FaceAndObjectServer()
    server.run()