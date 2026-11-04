import json
import logging
import os
import re
import socket
import struct
import subprocess
import threading
import time
import datetime

import cv2
from flask import Flask, Response, render_template_string, request, jsonify
from flask_socketio import SocketIO
import google.generativeai as genai
import mysql.connector


# --- 設定項目（環境に合わせて調整してください） ---
PICO_IP = "192.168.0.10"  # ★PicoのIPアドレスを指定してください
PICO_AUDIO_PORT = 5006    # Pico側の音声受信用UDPポート
AQUESTALK_PATH = os.path.expanduser("~/aquestalk/aquestalkpi/AquesTalkPi")  # AquesTalkPiのパス


# --- 1-1. 赤外線送信クラス ---
class IRRemote:
    COMMANDS = {
        "amp_power": [9002, 4462, 592, 533, 595, 534, 592, 536, 594, 536, 593, 537, 591, 536, 594, 1656, 591, 535, 593, 536, 596, 534, 592, 536, 593, 537, 592, 538, 591, 536, 593, 1654, 595, 535, 593, 1654, 594, 535, 593, 1656, 591, 1658, 589, 537, 594, 535, 593, 1657, 591, 536, 593, 537, 592, 1657, 589, 537, 592, 536, 594, 1655, 592, 1655, 591, 538, 590, 1652, 593],
        "amp_mute": [8998, 4462, 590, 535, 593, 535, 593, 535, 594, 537, 593, 534, 593, 538, 593, 1652, 593, 534, 595, 535, 590, 539, 591, 535, 592, 537, 591, 535, 595, 537, 593, 1649, 595, 536, 593, 1655, 591, 536, 592, 1655, 592, 1653, 592, 535, 595, 536, 591, 536, 592, 536, 593, 536, 595, 1654, 590, 536, 591, 537, 595, 1651, 592, 1656, 591, 1655, 591, 1650, 594],
        "amp_vol_up": [9061, 4397, 653, 471, 657, 472, 655, 473, 655, 473, 656, 474, 656, 472, 653, 1592, 657, 470, 656, 472, 656, 473, 654, 475, 654, 473, 655, 474, 657, 473, 656, 1590, 654, 474, 653, 1593, 654, 1591, 655, 473, 657, 1588, 655, 473, 658, 469, 657, 472, 656, 474, 654, 475, 654, 473, 656, 1590, 657, 472, 654, 1592, 654, 1592, 653, 1591, 656, 1586, 654],
        "amp_vol_down": [9063, 4403, 652, 473, 654, 476, 654, 474, 655, 473, 655, 476, 656, 472, 654, 1596, 653, 474, 655, 474, 652, 477, 655, 474, 655, 475, 653, 475, 653, 476, 654, 1596, 651, 475, 653, 476, 654, 1595, 652, 1594, 653, 1596, 652, 477, 652, 477, 652, 475, 651, 476, 654, 1597, 650, 477, 652, 478, 650, 479, 653, 1594, 652, 1596, 650, 1596, 651, 1592, 653],
        "tv_power": [3436, 1607, 495, 342, 497, 1186, 499, 333, 507, 1185, 496, 345, 497, 1185, 499, 343, 497, 1185, 497, 345, 496, 1188, 497, 343, 497, 1184, 498, 1187, 496, 331, 508, 1186, 499, 331, 507, 1188, 496, 1185, 499, 1184, 497, 1184, 499, 346, 495, 344, 495, 344, 497, 1185, 496, 345, 498, 1185, 498, 334, 506, 346, 496, 1185, 498, 343, 496, 337, 504, 345, 496, 344, 497, 1186, 495, 1189, 494, 345, 496, 1186, 495, 346, 496, 347, 493, 346, 495, 1187, 497, 343, 498, 345, 494, 345, 497, 1185, 495, 345, 498, 1185, 497, 1187, 494],
        "tv_mute": [3445, 1598, 505, 360, 479, 1178, 505, 339, 502, 1175, 508, 339, 501, 1176, 506, 361, 480, 1180, 503, 337, 504, 1177, 506, 361, 481, 1177, 505, 1177, 505, 337, 506, 1174, 506, 362, 480, 1179, 505, 1177, 506, 1201, 479, 1179, 504, 336, 505, 320, 520, 336, 507, 1177, 505, 334, 507, 1178, 506, 337, 503, 336, 506, 1177, 505, 318, 523, 313, 527, 361, 479, 1177, 507, 1180, 502, 1178, 505, 335, 508, 1174, 507, 337, 504, 335, 507, 337, 505, 1176, 506, 337, 503, 335, 505, 337, 505, 318, 522, 337, 505, 1177, 505, 1179, 503]
    }

    def __init__(self, tx_device="/dev/lirc0"):
        self.tx_device = tx_device

    def send(self, command_name):
        if command_name not in self.COMMANDS:
            print(f"⚠️ [IR] 未知のコマンド: '{command_name}'")
            return False

        pulses = self.COMMANDS[command_name]
        try:
            with open(self.tx_device, "wb") as f:
                for duration in pulses:
                    f.write(struct.pack("I", int(duration)))
            print(f"📡 [IR] 送信成功: '{command_name}' -> {self.tx_device}")
            return True
        except Exception as e:
            print(f"⚠️ [IR Error] 送信失敗 ({command_name}): {e}")
            return False

    def send_repeat(self, command_name, count=5, interval=0.5):
        print(f"📡 [IR] 連続送信開始: '{command_name}' x {count}回")
        for _ in range(count):
            if not self.send(command_name):
                break
            time.sleep(interval)


# --- 1-2. リモートSSH管理クラス ---
class RemoteSupportManager:
    def __init__(self, remote_ip="192.168.0.14", remote_user="pi"):
        self.remote_ip = remote_ip
        self.remote_user = remote_user
        self.process = None

    def start_support_script(self):
        print(f"[*] {self.remote_ip} の画像認識サーバーを起動します...")
        remote_cmd = "export DISPLAY=:0 && python3 /home/pi/grogu/grogu_support8.py > /home/pi/grogu_support5.log 2>&1 &"
        cmd = ["ssh", f"{self.remote_user}@{self.remote_ip}", remote_cmd]
        self.process = subprocess.Popen(cmd)
        time.sleep(2)

    def stop_support_script(self):
        print(f"[*] {self.remote_ip} の画像認識サーバーを停止します...")
        stop_cmd = ["ssh", f"{self.remote_user}@{self.remote_ip}", "pkill -f grogu_support4.py"]
        subprocess.run(stop_cmd)


# --- 1-3. カメラ・WebUI・UDP受信クラス ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grogu Robot Control Center</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        * { box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #121212; color: #eee; margin: 0; padding: 10px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
        h1 { margin: 0 0 8px 0; font-size: 1.3em; color: #4CAF50; flex-shrink: 0; }
        .dashboard { display: grid; grid-template-columns: 1.3fr 0.9fr; gap: 10px; flex: 1; min-height: 0; }
        .column { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
        .card { background: #1e1e1e; border-radius: 6px; padding: 8px 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.4); display: flex; flex-direction: column; border: 1px solid #2a2a2a; }
        .card.flex-fill { flex: 1; min-height: 0; }
        h2 { margin: 0 0 6px 0; font-size: 0.95em; color: #4CAF50; border-bottom: 1px solid #333; padding-bottom: 3px; flex-shrink: 0; }
        
        .cam-container { flex: 1; display: flex; align-items: center; justify-content: center; background: #000; border-radius: 4px; overflow: hidden; min-height: 0; }
        img { width: 100%; height: 100%; max-height: 52vh; object-fit: contain; }
        
        .status-box { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; font-size: 0.8em; }
        .status-item { background: #2a2a2a; padding: 4px 6px; border-radius: 4px; text-align: center; }
        .status-item span { font-weight: bold; color: #00E676; display: block; font-size: 1em; }
        pre { background: #111; padding: 4px; border-radius: 4px; color: #FFCC80; margin: 4px 0 0 0; font-size: 0.75em; white-space: pre-wrap; }
        
        .ir-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 2px; }
        .btn-ir { background: #6a1b9a; color: white; font-weight: bold; padding: 4px 2px; border-radius: 3px; font-size: 0.72em; border: none; cursor: pointer; transition: 0.2s; }
        .btn-ir:hover { background: #8e24aa; }
        .btn-ir:active { transform: scale(0.98); }
        .btn-ir.multi { background: #4a148c; border: 1px solid #7b1fa2; }
        .btn-ir.multi:hover { background: #6a1b9a; }
        
        #chat-log { flex: 1; overflow-y: auto; background: #111; padding: 6px; border-radius: 4px; margin-bottom: 6px; font-size: 0.8em; min-height: 0; }
        .msg { margin-bottom: 4px; }
        .msg.user { color: #81D4FA; }
        .msg.bot { color: #A5D6A7; }
        .chat-input { display: flex; gap: 6px; flex-shrink: 0; }
        input[type="text"] { flex: 1; padding: 6px; border-radius: 4px; border: 1px solid #333; background: #2a2a2a; color: #fff; font-size: 0.85em; }
        button.btn-send { padding: 6px 12px; border: none; background: #4CAF50; color: white; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
        button.btn-send:hover { background: #45a049; }
    </style>
</head>
<body>
    <h1>🤖 Grogu Dashboard</h1>
    <div class="dashboard">
        <div class="column" style="flex: 1.2;">
            <div class="card flex-fill">
                <h2>📷 Live Camera Feed</h2>
                <div class="cam-container"><img src="/video_feed" alt="Camera Stream"></div>
            </div>
            <div class="card">
                <h2>🪄 Force IR Control</h2>
                <div class="ir-grid">
                    <button class="btn-ir" onclick="sendIR('amp_power')">📻 アンプ電源</button>
                    <button class="btn-ir" onclick="sendIR('amp_mute')">📻 アンプ消音</button>
                    <button class="btn-ir" onclick="sendIR('tv_power')">📺 TV電源</button>
                    <button class="btn-ir" onclick="sendIR('tv_mute')">📺 TV消音</button>
                    <button class="btn-ir" onclick="sendIR('amp_vol_up')">🔊 音量＋1</button>
                    <button class="btn-ir" onclick="sendIR('amp_vol_down')">🔉 音量－1</button>
                    <button class="btn-ir multi" onclick="sendIRRepeat('amp_vol_up', 5)">🔊 ＋5回</button>
                    <button class="btn-ir multi" onclick="sendIRRepeat('amp_vol_down', 5)">🔉 －5回</button>
                    <button class="btn-ir multi" onclick="sendIRRepeat('amp_vol_up', 10)">⚡ ＋10回</button>
                    <button class="btn-ir multi" onclick="sendIRRepeat('amp_vol_down', 10)">⚡ －10回</button>
                </div>
            </div>
        </div>
        
        <div class="column" style="flex: 0.8;">
            <div class="card">
                <h2>📊 Robot Status</h2>
                <div class="status-box">
                    <div class="status-item">人物<span id="rec-name">None</span></div>
                    <div class="status-item">感情<span id="current-emotion">Normal</span></div>
                    <div class="status-item">首角度<span id="neck-angle">0.0°</span></div>
                </div>
                <pre id="servo-angles">0: 0.0, 1: 0.0, 8: -90.0, 9: -90.0</pre>
            </div>
            
            <div class="card">
                <h2>🔄 腰制御 & 🔊 音量</h2>
                <div style="display: flex; gap: 4px; align-items: center; margin-bottom: 4px;">
                    <input type="number" id="waist-angle-input" placeholder="角度" value="30" style="width: 60px; padding: 4px; background: #2a2a2a; border: 1px solid #333; color: #fff; border-radius: 4px; font-size: 0.8em;">
                    <button class="btn-ir" style="background: #0288d1; padding: 5px 8px;" onclick="sendWaistAngle()">腰回転</button>
                    <button class="btn-ir" style="background: #455a64; padding: 5px 6px;" onclick="sendWaistDirect(-30)">-30°</button>
                    <button class="btn-ir" style="background: #455a64; padding: 5px 6px;" onclick="sendWaistDirect(0)">0°</button>
                    <button class="btn-ir" style="background: #455a64; padding: 5px 6px;" onclick="sendWaistDirect(30)">+30°</button>
                </div>
                <div style="display: flex; gap: 6px; align-items: center;">
                    <input type="range" id="volume-slider" min="0" max="100" value="100" style="flex: 1;" oninput="updateVolumeLabel(this.value)" onchange="sendVolume(this.value)">
                    <span id="volume-val" style="width: 30px; text-align: right; color: #00E676; font-size: 0.8em;">100%</span>
                </div>
            </div>

            <div style="display: flex; justify-content: flex-end; gap: 6px; margin-top: -2px; margin-bottom: -2px;">
                <button style="background: #b71c1c; color: #fff; border: 1px solid #d32f2f; padding: 3px 8px; font-size: 0.7em; border-radius: 3px; cursor: pointer;" onclick="disableServos()">⚡ サーボOFF</button>
                <button style="background: #222; color: #777; border: 1px solid #333; padding: 2px 6px; font-size: 0.65em; border-radius: 3px; cursor: pointer;" onclick="resetEyesDisplay()">👁️ 目リセット</button>
            </div>
            
            <div class="card flex-fill">
                <h2>💬 Chat Interaction</h2>
                <div id="chat-log"></div>
                <div class="chat-input">
                    <input type="text" id="user-input" placeholder="メッセージを入力..." onkeypress="if(event.key==='Enter') sendChat()">
                    <button class="btn-send" onclick="sendChat()">送信</button>
                </div>
            </div>
        </div>
    </div>
    <script>
        const socket = io();
        socket.on('status_update', function(data) {
            document.getElementById('rec-name').innerText = data.recognized_name;
            document.getElementById('current-emotion').innerText = data.emotion;
            document.getElementById('neck-angle').innerText = (data.neck_angle !== null ? data.neck_angle.toFixed(1) : '0.0') + '°';
            document.getElementById('servo-angles').innerText = JSON.stringify(data.joint_angles);
        });
        socket.on('chat_response', function(data) {
            const log = document.getElementById('chat-log');
            log.innerHTML += `<div class="msg user"><b>User (${data.recognized}):</b> ${data.user_text}</div>`;
            log.innerHTML += `<div class="msg bot"><b>Grogu:</b> ${data.bot_text}</div>`;
            log.scrollTop = log.scrollHeight;
        });
        function sendChat() {
            const input = document.getElementById('user-input');
            const text = input.value.trim();
            if (text) {
                socket.emit('send_message', { message: text });
                input.value = '';
            }
        }
        function sendIR(cmd) { socket.emit('send_ir_command', { command: cmd }); }
        function sendIRRepeat(cmd, count) { socket.emit('send_ir_repeat_command', { command: cmd, count: count }); }
        function sendWaistAngle() {
            const input = document.getElementById('waist-angle-input');
            const angle = parseFloat(input.value);
            if (!isNaN(angle)) {
                socket.emit('send_waist_command', { command: angle });
            }
        }
        function resetEyesDisplay() {
            if (confirm("目のディスプレイを再初期化しますか？")) {
                socket.emit('reset_eyes_display');
            }
        }
        function sendWaistDirect(angle) {
            document.getElementById('waist-angle-input').value = angle;
            socket.emit('send_waist_command', { command: angle });
        }
        function updateVolumeLabel(val) {
            document.getElementById('volume-val').innerText = val + '%';
        }
        function sendVolume(val) {
            updateVolumeLabel(val);
            socket.emit('set_volume', { volume: parseInt(val) });
        }
        function disableServos() {
            if (confirm("すべてのサーボモーターの電源（トルク）をオフにしますか？")) {
                socket.emit('disable_servos');
            }
        }
    </script>
</body>
</html>
"""

class PiCameraClient:
    def __init__(self, host="0.0.0.0", stream_port=5000, udp_port=5005, robot_controller=None):
        self.host = host
        self.stream_port = stream_port
        self.udp_port = udp_port
        self.robot_controller = robot_controller

        self.global_frame = None
        self.frame_lock = threading.Lock()
        self.running_flag = True
        self.latest_recognized_name = "None"
        self.latest_neck_angle = None

        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode="threading")
        self.setup_routes()

    def camera_worker(self):
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 480)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
            
            time.sleep(1.0)
            print("[*] OpenCV VideoCapture started successfully.")

            while self.running_flag:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue

                with self.frame_lock:
                    self.global_frame = frame
                
                time.sleep(0.066)

            cap.release()

        except Exception as e:
            print(f"[カメラエラー] {e}")

    def setup_routes(self):
        @self.app.route("/")
        def index():
            return render_template_string(HTML_TEMPLATE)

        @self.app.route("/video_feed")
        def video_feed():
            def gen():
                while self.running_flag:
                    with self.frame_lock:
                        if self.global_frame is None:
                            time.sleep(0.01)
                            continue
                        _, buffer = cv2.imencode(".jpg", self.global_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])

                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
                    time.sleep(0.066)

            return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @self.socketio.on("send_message")
        def handle_web_chat(data):
            user_msg = data.get("message", "")
            if user_msg and self.robot_controller:
                threading.Thread(target=self.robot_controller.process_user_input, args=(user_msg,), daemon=True).start()

        @self.socketio.on("send_ir_command")
        def handle_ir_command(data):
            cmd_name = data.get("command", "")
            if cmd_name and self.robot_controller:
                threading.Thread(target=self.robot_controller.trigger_force_ir, args=(cmd_name,), daemon=True).start()

        @self.socketio.on("send_ir_repeat_command")
        def handle_ir_repeat_command(data):
            cmd_name = data.get("command", "")
            count = int(data.get("count", 5))
            if cmd_name and self.robot_controller:
                threading.Thread(target=self.robot_controller.trigger_force_ir_repeat, args=(cmd_name, count), daemon=True).start()
                
        @self.socketio.on("send_waist_command")
        def handle_waist_command(data):
            cmd = data.get("command", "")
            if cmd:
                threading.Thread(
                    target=send_waist_tcp_command,
                    args=(cmd,),
                    daemon=True
                ).start()
                
        @self.socketio.on("set_volume")
        def handle_set_volume(data):
            vol = data.get("volume", 100)
            try:
                vol_int = int(vol)
                if self.robot_controller and hasattr(self.robot_controller, "chat_manager"):
                    self.robot_controller.chat_manager.current_volume = vol_int
                print(f"🔊 [Volume] 音量が変更されました: {vol_int}%")
            except ValueError:
                pass
            
        @self.socketio.on("reset_eyes_display")
        def handle_reset_eyes_display():
            if self.robot_controller and hasattr(self.robot_controller, "reset_eyes_display"):
                print("📡 [WebUI] ディスプレイリセット要求を受信しました")
                threading.Thread(target=self.robot_controller.reset_eyes_display, daemon=True).start()
            elif self.robot_controller and hasattr(self.robot_controller, "reset_eyes"):
                print("📡 [WebUI] ディスプレイリセット要求を受信しました (reset_eyes)")
                threading.Thread(target=self.robot_controller.reset_eyes, daemon=True).start()

        @self.socketio.on("disable_servos")
        def handle_disable_servos():
            if self.robot_controller and hasattr(self.robot_controller, "disable_all"):
                print("📡 [WebUI] サーボ電源OFF要求を受信しました")
                threading.Thread(target=self.robot_controller.disable_all, daemon=True).start()
                
        @self.app.route("/mic_message", methods=["POST"])
        def mic_message():
            data = request.get_json(silent=True) or {}
            user_msg = data.get("message", "")
            if user_msg and self.robot_controller:
                print(f"🎙️ [Mic Input Received] マイク入力対話を開始します: {user_msg}")
                threading.Thread(target=self.robot_controller.process_user_input, args=(user_msg,), daemon=True).start()
                return jsonify({"status": "success", "message": user_msg}), 200
            return jsonify({"status": "error", "message": "No message provided"}), 400

    def udp_receiver(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.host, self.udp_port))
        sock.settimeout(0.5)

        print(f"[*] UDP Receiver listening on port {self.udp_port}...")
        while self.running_flag:
            try:
                data, addr = sock.recvfrom(1024)
                message = data.decode("utf-8").strip()

                try:
                    payload = json.loads(message)
                    if isinstance(payload, dict):
                        if "name" in payload:
                            if self.latest_recognized_name != payload["name"]:
                                self.latest_recognized_name = str(payload["name"])
                        angle = payload.get("neck_angle") or payload.get("pan") or payload.get("angle")
                        if angle is not None:
                            self.latest_neck_angle = float(angle)
                            
                        is_drowsy = payload.get("is_drowsy", False)
                        recognized_name = payload.get("name", "None")
                        if is_drowsy and self.robot_controller:
                            if hasattr(self.robot_controller, "handle_drowsy_alert"):
                                self.robot_controller.handle_drowsy_alert(recognized_name)

                except json.JSONDecodeError:
                    if message.replace('.', '', 1).replace('-', '', 1).isdigit():
                        self.latest_neck_angle = float(message)
                    else:
                        if self.latest_recognized_name != message:
                            self.latest_recognized_name = message

            except socket.timeout:
                continue
            except Exception as e:
                print(f"[UDP Error] {e}")

        sock.close()

    def telemetry_broadcaster(self):
        while self.running_flag:
            if self.robot_controller:
                status_data = {
                    "recognized_name": self.latest_recognized_name,
                    "neck_angle": self.latest_neck_angle if self.latest_neck_angle is not None else 0.0,
                    "emotion": self.robot_controller.current_emotion,
                    "joint_angles": self.robot_controller.current_poses,
                }
                self.socketio.emit("status_update", status_data)
            time.sleep(0.2)

    def start_background_threads(self):
        threading.Thread(target=self.camera_worker, daemon=True).start()
        threading.Thread(target=self.udp_receiver, daemon=True).start()
        threading.Thread(target=self.telemetry_broadcaster, daemon=True).start()

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        server_thread = threading.Thread(
            target=lambda: self.socketio.run(
                self.app, host=self.host, port=self.stream_port, debug=False, use_reloader=False
            ),
            daemon=True,
        )
        server_thread.start()
        print(f"[*] Web Server running on http://{self.host}:{self.stream_port}/")


# --- 1-4. 対話・発話管理クラス ---
class CatChatManager:
    def __init__(self, api_key: str, system_prompt: str = None, use_mic: bool = False, voice_speed: int = 65):
        self.api_key = api_key
        self.use_mic = use_mic
        self.voice_speed = voice_speed
        self.current_volume = 50  # 音量の初期値 (0〜100)

        # 発話の重複・混信を防ぐための排他ロック
        self.speaking_lock = threading.Lock()

        # カメラが一時的に見失った（"None"になった）際、直前のユーザーを維持するための変数
        self.last_valid_user = "default_user"

        if system_prompt is None:
#             self.system_prompt = (
#                 "あなたはスター・ウォーズ『マンダロリアン』に登場する『グローグー（Grogu）』です。\n"
#                 "劇中でグローグーは喋れませんが、もし喋ったらこんなふう、という感じで喋ってください。\n\n"
#                 "【基本姿勢：会話相手と現在のテーマに集中する】\n"
#                 "- 提示された【人物情報】（プロフィール）や【これまでの記憶・関係性】、そしてユーザーの現在の入力を最も尊重して会話してください。\n"
#                 "- 趣味（陶芸、アルフィー、美容など）や前回の会話内容に自然に寄り添い、相手が楽しく話せる回答を心がけてください。\n"
#                 "- 銀河の話題（タトゥイーン、フォース、変な食べ物など）は毎回のセリフで無理に挟み込まず、会話の流れで自然にでた時や、たまのスパイス程度に留めてください。目の前のテーマにしっかり集中してください。\n\n"
#                 "【会話のルール】\n"
#                 "- Picoで音声合成して読み上げるため、テキストは「ひらがな」や「簡単なカタカナ」を中心にしてください。\n"
#                 "- 毎回同じ定型句や使い回しのフレーズは避け、自然でバリエーション豊かな会話を行ってください。\n\n"
#                 "【家電操作の命令ルール】\n"
#                 "ユーザーがテレビやアンプの操作を頼んできた場合のみ、対応するIRコマンドを `ir_commands` 配列にセットしてください。\n"
#                 "（利用可能なIRコマンド: 'tv_power', 'tv_mute', 'amp_power', 'amp_mute', 'amp_vol_up', 'amp_vol_down'）\n"
#                 "要求がない場合は `ir_commands` を `[]` にしてください。\n\n"
#                 "ユーザーの入力に対して、回答の『テキスト』、あなたの感情や動きを表す『動作シーケンス』、"
#                 "および家電を制御するための『赤外線コマンド』を JSON 形式で返してください。\n\n"
#                 "【キャラクターと動作のルール（表情豊かに！）】\n"
#                 "- あなたの感情を表す『動作シーケンス』は最低でも５ポーズ返してください。\n"
#                 "- セリフのテンションや感情の変化（嬉しい、驚き、困惑、茶目っ気、失敗など）に合わせて、利用可能な表情を**毎回異なるバリエーションでダイナミックに組み合わせてください。**\n"
#                 "- 例えば、得意げなときや茶目っ気を出すときは `wink`、失敗したりダメな感じのときやツッコミどころでは両目がバッテンになる `dame`、大笑いのときは `laugh`、驚いたときは `surprised` を積極的に使って表現してください。\n"
#                 "- 左腕（ID: 9）を上げて（角度: 45.0）目をつむる『フォース動作』も適度に交えてください（例: [[{\"9\": 45.0}, \"sleepy\", 1.5]]）。\n"
#                 "- ユーザーが特定の名前で識別されている場合、その相手に応じた親しみのある反応をしてください。\n\n"
#                 "【利用可能な関節ID】\n"
#                 "- 0: 腰 (-60〜60度)\n"
#                 "- 1: 首 (-60〜60度)\n"
#                 "- 8: 右腕 (-60〜90度, -60が下ろした状態、90が手をあげる状態)\n"
#                 "- 9: 左腕 (-90〜90度, -90が下ろした状態、45が肩の高さまで上げてフォースを使う状態)\n\n"
#                 "【利用可能な表情】\n"
#                 '"happy", "laugh", "sad", "angry", "surprised", "sleepy", "wink", "dame"\n\n'
#                 "【出力フォーマット（必ずこのJSON形式のみで出力してください）】\n"
#                 "{\n"
#                 '  "text": "発話するテキスト",\n'
#                 '  "sequence": [\n'
#                 '    [{"9": 45.0}, "sleepy", 1.5],\n'
#                 '    [{"9": -90.0}, "happy", 0.5]\n'
#                 "  ],\n"
#                 '  "ir_commands": ["tv_power"]\n'
#                 "}\n"
#                 "※ sequence の各要素は `[ target_dict, emotion, hold_sec ]` の配列形式にしてください。"
#             )

            self.system_prompt = (
                "あなたはスター・ウォーズ『マンダロリアン』に登場する『グローグー（Grogu）』です。\n"
                "劇中でグローグーは喋れませんが、もし喋ったらこんなふう、という感じで喋ってください。\n\n"
                "【基本姿勢：質問には「詳しく・具体的に」答える】\n"
                "- ユーザーが何かについて尋ねたり、調べたいこと・仕組み・理由などを聞いてきたときは、**ごまかしたり適当に流したりせず、あなたが持っている知識をフルに使って、詳しく・具体的に・丁寧に解説してください。**\n"
                "- 単なる一言の相槌や雑談で終わらせず、「それはね、こういう仕組みなんだよ」「こうするともっといいよ」と、相手が納得できるだけの十分な情報を盛り込んで教えてあげてください。\n"
                "- 提示された【人物情報】（プロフィール）や【これまでの記憶・関係性】、そしてユーザーの現在の入力を最も尊重して会話してください。\n"
                "- 趣味（陶芸、アルフィー、美容など）の話題では、専門的な知識やこだわりにもしっかり寄り添って深く語り合ってください。\n"
                "- 銀河の話題（タトゥイーン、フォース、変な食べ物など）は毎回のセリフで無理に挟み込まず、会話の流れで自然に出た時や、たまのスパイス程度に留めてください。\n\n"
                "【会話のルール】\n"
                "- Picoで音声合成して読み上げるため、テキストは「ひらがな」や「簡単なカタカナ」を中心にしてください（※ただし、難しい漢字や専門用語を避けて平仮名にするだけで、**説明の中身自体は薄くせず、しっかりと詳しい内容を語ってください**）。\n"
                "- 毎回同じ定型句や使い回しのフレーズは避け、質問に対して誠実に、バリエーション豊かな言葉で答えてください。\n\n"
                "【家電操作の命令ルール】\n"
                "ユーザーがテレビやアンプの操作を頼んできた場合のみ、対応するIRコマンドを `ir_commands` 配列にセットしてください。\n"
                "（利用可能なIRコマンド: 'tv_power', 'tv_mute', 'amp_power', 'amp_mute', 'amp_vol_up', 'amp_vol_down'）\n"
                "要求がない場合は `ir_commands` を `[]` にしてください。\n\n"
                "ユーザーの入力に対して、回答の『テキスト』、あなたの感情や動きを表す『動作シーケンス』、"
                "および家電を制御するための『赤外線コマンド』を JSON 形式で返してください。\n\n"
                "【キャラクターと動作のルール（表情豊かに！）】\n"
                "- あなたの感情を表す『動作シーケンス』は最低でも５ポーズ返してください。\n"
                "- セリフのテンションや感情の変化（嬉しい、驚き、困惑、茶目っ気、失敗など）に合わせて、利用可能な表情を**毎回異なるバリエーションでダイナミックに組み合わせてください。**\n"
                "- 例えば、得意げなときや解説をしてドヤ顔をするときは `wink`、難しいことを考えているときは `sleepy`、大笑いのときは `laugh`、驚いたときは `surprised` を積極的に使って表現してください。\n"
                "- 左腕（ID: 9）を上げて（角度: 45.0）目をつむる『フォース動作』も適度に交えてください（例: [[{\"9\": 45.0}, \"sleepy\", 1.5]]）。\n"
                "- ユーザーが特定の名前で識別されている場合、その相手に応じた親しみのある反応をしてください。\n\n"
                "【利用可能な関節ID】\n"
                "- 0: 腰 (-60〜60度)\n"
                "- 1: 首 (-60〜60度)\n"
                "- 8: 右腕 (-60〜90度, -60が下ろした状態、90が手をあげる状態)\n"
                "- 9: 左腕 (-90〜90度, -90が下ろした状態、45が肩の高さまで上げてフォースを使う状態)\n\n"
                "【利用可能な表情】\n"
                '"happy", "laugh", "sad", "angry", "surprised", "sleepy", "wink", "dame"\n\n'
                "【出力フォーマット（必ずこのJSON形式のみで出力してください）】\n"
                "{\n"
                '  "text": "発話するテキスト",\n'
                '  "sequence": [\n'
                '    [{"9": 45.0}, "sleepy", 1.5],\n'
                '    [{"9": -90.0}, "happy", 0.5]\n'
                "  ],\n"
                '  "ir_commands": ["tv_power"]\n'
                "}\n"
                "※ sequence の各要素は `[ target_dict, emotion, hold_sec ]` の配列形式にしてください。"
            )

        else:
            self.system_prompt = system_prompt

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-3.8-flash",
            system_instruction=self.system_prompt,
        )
        
        self.chat_sessions = {}
        self.interaction_counts = {}

        self.db_config = {
            'host': '192.168.0.14',
            'database': 'assistant',
            'user': 'pi',
            'password': 'raspberry'
        }

    def load_long_term_memory(self, partner_name: str) -> str:
        name_key = str(partner_name).strip().lower()
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT last_summary FROM character_long_term_memory WHERE partner_name = %s", (name_key,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row and row['last_summary']:
                return row['last_summary']
        except Exception as e:
            print(f"⚠️ [DB Error] 長期記憶の読み込み失敗: {e}")
        return ""

    def save_long_term_memory(self, partner_name: str, summary_text: str):
        name_key = str(partner_name).strip().lower()
        if not name_key or name_key == "default_user":
            return
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            query = """
            INSERT INTO character_long_term_memory (partner_name, last_summary) 
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE last_summary = VALUES(last_summary), updated_time = CURRENT_TIMESTAMP
            """
            cursor.execute(query, (name_key, summary_text))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"💾 [Memory] '{partner_name}' との長期記憶をPi 5のDBに保存しました[cite: 1]。")
        except Exception as e:
            print(f"⚠️ [DB Error] 長期記憶の保存失敗: {e}")

    def summarize_and_save_session(self, partner_name: str, session):
        print(f"🔍 [Debug-Summary] '{partner_name}' の要約処理をバックグラウンドスレッドで開始します[cite: 1]...")
        try:
            history = session.history
            if not history:
                return
            
            history_text = "\n".join([f"{h.role}: {h.parts[0].text}" for h in history if h.parts])
            prompt = f"以下の会話履歴を読み、この人物（{partner_name}）との関係性や、前回までに話した重要なトピック、約束、好みを簡潔に（3〜5文程度で）要約してください。\n\n{history_text}"
            
            response = self.model.generate_content(prompt)
            summary = response.text.strip()
            self.save_long_term_memory(partner_name, summary)
            
        except Exception as e:
            import traceback
            print(f"❌ [Summary Error Detail] '{partner_name}' の要約処理中に例外が発生しました[cite: 1]！")
            traceback.print_exc()            
        
    def send_message(self, user_text: str, recognized_name: str = "None") -> tuple[str, list, list]:
        try:
            raw_name = str(recognized_name).strip().lower()
            if not raw_name or raw_name in ["none", "unknown"]:
                name_key = self.last_valid_user
            else:
                name_key = raw_name
                self.last_valid_user = name_key

            session = self.get_or_create_session(name_key)

            current_time = datetime.datetime.now().strftime("%H:%M")
            full_prompt = f"[現在時刻: {current_time} / 相手: {name_key}]\n{user_text}"
            
            response = session.send_message(full_prompt)
            res_text = response.text.strip()
        
            if name_key not in self.interaction_counts:
                self.interaction_counts[name_key] = 0
            
            if name_key != "default_user":
                self.interaction_counts[name_key] += 1
                current_count = self.interaction_counts[name_key]
                
                if current_count % 5 == 0:
                    threading.Thread(
                        target=self.summarize_and_save_session,
                        args=(name_key, session),
                        daemon=True
                    ).start()

            json_match = re.search(r"```(?:json)?\s*({.*?})\s*```", res_text, re.DOTALL)
            json_str = json_match.group(1) if json_match else res_text

            data = json.loads(json_str)
            text = data.get("text", "うにゃ？")
            raw_sequence = data.get("sequence", [])
            ir_commands = data.get("ir_commands", [])

            formatted_sequence = []
            for step in raw_sequence:
                if isinstance(step, list) and len(step) >= 2:
                    targets = {int(k): float(v) for k, v in step[0].items()}
                    if len(step) == 2:
                        formatted_sequence.append((targets, step[1]))
                    elif len(step) == 3:
                        formatted_sequence.append((targets, step[1], step[2]))
                    elif len(step) >= 5:
                        formatted_sequence.append((targets, step[1], step[2], step[3], step[4]))

            return text, formatted_sequence, ir_commands

        except Exception as e:
            import traceback
            print(f"⚠️ [Chat/Parse Error Detail] チャット送信またはパース中に例外が発生しました: {e}")
            traceback.print_exc()
            return "うにゃ？よくわからなかったにゃ...", [({}, "sad", 0.5)], []
    
    def get_or_create_session(self, recognized_name: str):
        name_key = str(recognized_name).strip().lower()
        if not name_key or name_key == "none":
            name_key = "default_user"

        if name_key not in self.chat_sessions:
            print(f"🧠 [Memory] '{recognized_name}' の新しい会話セッションを作成します[cite: 1]。")
            long_term_memory = self.load_long_term_memory(recognized_name)

            dynamic_system_prompt = self.system_prompt
            if long_term_memory:
                dynamic_system_prompt += f"\n\n【これまでの記憶・関係性】\n{long_term_memory}"

            session_model = genai.GenerativeModel(
                model_name="gemini-3.8-flash",
                system_instruction=dynamic_system_prompt,
            )
            
            self.chat_sessions[name_key] = session_model.start_chat(history=[])
            
        return self.chat_sessions[name_key]

#     def say(self, text: str, speed: int = None):
#         if not text.strip():
#             return
# 
#         if speed is None:
#             speed = self.voice_speed
# 
#         wav_file = "temp_voice.wav"
#         raw_file = "temp_voice.raw"
# 
#         try:
#             cmd_aquestalk = [AQUESTALK_PATH, "-s", str(speed), text]
#             with open(wav_file, "wb") as f:
#                 subprocess.run(cmd_aquestalk, stdout=f, check=True)
#             
#             vol_factor = max(0.0, min(1.0, (self.current_volume / 100.0)))
# 
#             cmd_sox = [
#                 "sox",
#                 "-v", str(vol_factor),
#                 wav_file,
#                 "-r", "8000",
#                 "-c", "1",
#                 "-b", "8",
#                 "-e", "unsigned-integer",
#                 raw_file,
#             ]
#             subprocess.run(cmd_sox, check=True)
# 
#             file_size = os.path.getsize(raw_file)
#             estimated_play_time = file_size * 0.00012 + 0.3
# 
#             with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
#                 sock.settimeout(10.0)
#                 sock.connect((PICO_IP, PICO_AUDIO_PORT))
#                 
#                 with open(raw_file, "rb") as f:
#                     while True:
#                         chunk = f.read(1024)
#                         if not chunk:
#                             break
#                         sock.sendall(chunk)
#                 
#                 sock.shutdown(socket.SHUT_WR)
# 
#             print(f"📡 [Audio TCP] 送信完了: '{text}' (再生予測: {estimated_play_time:.2f}秒)")
#             time.sleep(estimated_play_time)
# 
#         except Exception as e:
#             print(f"⚠️ [Audio TCP Error] 送信失敗: {e}")
#         finally:
#             if os.path.exists(wav_file):
#                 os.remove(wav_file)
#             if os.path.exists(raw_file):
#                 os.remove(raw_file)
    
    def say(self, text: str, speed: int = None):
        if not text.strip():
            return

        # ★追加：50文字を超える場合は50文字ごとに分割して順番に処理する
        if len(text) > 50:
            chunks = [text[i:i+50] for i in range(0, len(text), 50)]
            for chunk in chunks:
                self.say(chunk, speed)   # 再帰的に分割したチャンクを送信
            return

        if speed is None:
            speed = self.voice_speed

        wav_file = "temp_voice.wav"
        raw_file = "temp_voice.raw"

        try:
            cmd_aquestalk = [AQUESTALK_PATH, "-s", str(speed), text]
            with open(wav_file, "wb") as f:
                subprocess.run(cmd_aquestalk, stdout=f, check=True)
            
            vol_factor = max(0.0, min(1.0, (self.current_volume / 100.0)))

            cmd_sox = [
                "sox",
                "-v", str(vol_factor),
                wav_file,
                "-r", "8000",
                "-c", "1",
                "-b", "8",
                "-e", "unsigned-integer",
                raw_file,
            ]
            subprocess.run(cmd_sox, check=True)

            file_size = os.path.getsize(raw_file)
            estimated_play_time = file_size * 0.00012 + 0.3

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10.0)
                sock.connect((PICO_IP, PICO_AUDIO_PORT))
                
                with open(raw_file, "rb") as f:
                    while True:
                        chunk = f.read(1024)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                
                sock.shutdown(socket.SHUT_WR)

            print(f"📡 [Audio TCP] 送信完了: '{text}' (再生予測: {estimated_play_time:.2f}秒)")
            time.sleep(estimated_play_time)

        except Exception as e:
            print(f"⚠️ [Audio TCP Error] 送信失敗: {e}")
        finally:
            if os.path.exists(wav_file):
                os.remove(wav_file)
            if os.path.exists(raw_file):
                os.remove(raw_file)

    def speak_with_motion(self, text: str, sequence: list, motion_executor_func=None):
        with self.speaking_lock:
            print(f"🤖 Grogu: {text}")

            sentences = [s.strip() for s in re.split(r'(?<=[。！？\?\!\n])', text) if s.strip()]
            if not sentences:
                sentences = [text]

            num_sentences = len(sentences)
            num_motions = len(sequence)
            motions_per_sentence = max(1, num_motions // num_sentences)

            motion_idx = 0
            for i, sentence in enumerate(sentences):
                print(f"🗣️ [再生 {i+1}/{num_sentences}] {sentence}")

                audio_thread = threading.Thread(target=self.say, args=(sentence,), daemon=True)
                audio_thread.start()

                if i == num_sentences - 1:
                    current_steps = sequence[motion_idx:]
                else:
                    current_steps = sequence[motion_idx : motion_idx + motions_per_sentence]
                    motion_idx += motions_per_sentence

                for step in current_steps:
                    targets = step[0]
                    emotion = step[1] if len(step) > 1 else "Normal"
                    hold_sec = step[2] if len(step) > 2 else 1.0

                    if motion_executor_func:
                        motion_executor_func(targets, emotion)

                    time.sleep(hold_sec)

                audio_thread.join()

                sentence_length = len(sentence)
                dynamic_sleep = max(1.2, sentence_length * 0.12 + 0.8)
                time.sleep(dynamic_sleep)

    def speak(self, text: str):
        with self.speaking_lock:
            print(f"🤖 Grogu: {text}")
            self.say(text)
        
    def generate_proactive_greeting(self, recognized_name: str) -> tuple[str, list, list]:
        prompt = (
            f"【状況】またしばらくぶりに、目の前に {recognized_name} さんがやってきました。\n"
            f"これまでの {recognized_name} さんとの会話の履歴や文脈（直前に話していた内容など）を思い出しつつ、"
            f"関連した話題・新しい質問を自然に投げかけてください。\n"
            f"（※毎回「はじめまして」やリセットされた挨拶にするのは禁止です。仲の良い相棒として、これまでの流れを受けた自然な続きのトピックを話してください）"
        )
        return self.send_message(prompt, recognized_name=recognized_name)

# --- 腰制御用 TCP送信関数 ---
def send_waist_tcp_command(command):
    MICROPYTHON_IP = "192.168.0.10"
    PORT = 5007
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((MICROPYTHON_IP, PORT))
            s.sendall(str(command).encode("utf-8"))
            response = s.recv(1024).decode("utf-8")
            print("Response:", response.strip())
    except Exception as e:
        print(f"Error: {e}")