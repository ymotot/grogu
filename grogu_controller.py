import asyncio
import math
import os
import socket
import subprocess
import threading
import time
from bleak import BleakScanner
from flask import Flask, jsonify, request

from gpiozero import DistanceSensor

# --- ロボット関節・サーボ設定 ---
JOINT_MAP = {0: 0, 1: 15, 8: 8, 9: 9}
BASE_ANGLE = 90.0
DIRECTIONS = {9: -1}
OFFSETS = {}

# 初期角度管理
CURRENT_ANGLES = {0: 0.0, 1: 0.0, 8: -60.0, 9: -90.0}

from eye_controller5 import GroguEyeController
from grogu_services import (
    CatChatManager,
    IRRemote,
    PiCameraClient,
    RemoteSupportManager,
)

PI5_IP = "192.168.0.14"  # ★Raspberry Pi 5 のIPアドレスを指定
SERVO_PORT = 5008

# 関節ごとの論理可動範囲 (deg)
JOINT_LIMITS = {
    0: (-60.0, 60.0),
    1: (-60.0, 60.0),
    8: (-60.0, 90.0),
    9: (-90.0, 90.0),
}

# ホームポジション設定
HOME_POSITIONS = {
    0: 0.0,
    1: 0.0,
    8: -60.0,
    9: -30.0,
}

# Pico W 設定
PICO_IP = "192.168.0.11"
PICO_PORT = 5005


class CatRobotController:

    def __init__(
        self, 
        gemini_api_key: str = None, 
        use_mic: bool = False, 
        remote_only_mode: bool = False, 
        enable_proactive: bool = False  # ★ 追加：自発発話の有効/無効オプション（デフォルトはFalse）
    ):
        self.remote_only_mode = remote_only_mode
        self.enable_proactive = enable_proactive
        
        self.current_poses = {
            jid: CURRENT_ANGLES.get(jid, 0.0) for jid in JOINT_MAP.keys()
        }

        # サーボの初期角度設定
        if 8 in JOINT_MAP:
            self.current_poses[8] = -60.0
            CURRENT_ANGLES[8] = -60.0
        if 9 in JOINT_MAP:
            self.current_poses[9] = -30.0
            CURRENT_ANGLES[9] = -30.0

        self.is_disabled = False
        self.current_emotion = "Normal"

        # 通信用の低レベルロックと、一連のモーション全体を保護する大元ロック
        self.servo_lock = threading.Lock()
        self.motion_lock = threading.Lock()

        self.neck_tracking_enabled = True
        self.is_neck_moving = False

        # M5Atom 死活監視の最終受信時刻管理
        self.last_m5_heartbeat = time.time()

        # 超音波センサーの初期化
        print("📏 超音波センサーを初期化中...")
        self.distance_sensor = DistanceSensor(
            echo=15, trigger=14, max_distance=3
        )

        # 腰の動作状態フラグ
        self.is_waist_moving = False

        self.ir = IRRemote()

        print("👁️ ディスプレイを初期化中...")
        self.eyes = GroguEyeController()

        print("📷 カメラ＆Web UIクライアントを起動中...")
        self.camera_client = PiCameraClient(robot_controller=self)
        self.camera_client.start_background_threads()
        
        # ──【リモコンモードの場合は重い機能をスキップ】──
        if not self.remote_only_mode:
            
            print("🌐 リモート画像認識サーバーを起動中...")
            self.remote_manager = RemoteSupportManager()
            self.remote_manager.start_support_script()

            api_key = gemini_api_key or os.environ.get(
                "GEMINI_API_KEY", "YOUR_GEMINI_API_KEY"
            )
            self.chat_manager = CatChatManager(api_key=api_key, use_mic=use_mic)

            self.chat_active_flag = False
            self.last_applied_neck_angle = None

            # 首追従スレッドの起動
            self.neck_tracker_thread = threading.Thread(
                target=self._neck_tracking_worker, daemon=True
            )
            self.neck_tracker_thread.start()
            
            self.last_m5_heartbeat = time.time()
            self.has_welcomed = False

            # M5Atom からの命令受信用 API サーバー（Flask）の起動
            self.api_thread = threading.Thread(
                target=self._run_api_server, daemon=True
            )
            self.api_thread.start()
            
            # ── ★ 追加・修正: 自発発話が有効な場合のみ監視スレッドを起動する ──
            if self.enable_proactive:
                print("✨ [自発発話機能] 有効（ON）に設定されました。監視を開始します。")
                self.last_proactive_time = 0.0
                self.proactive_cooldown = 180.0
                self.proactive_target_persons = ["tsutomu", "eriko"]
                self.is_proactive_speaking = False

                self.proactive_thread = threading.Thread(
                    target=self._proactive_person_watcher, daemon=True
                )
                self.proactive_thread.start()
            else:
                print("⚡ [自発発話機能] 無効（OFF）に設定されました。")
            
            self.last_chat_time = 0.0
            self.chat_cooldown = 180.0
            
            self.last_drowsy_alert_time = 0.0
            self.drowsy_cooldown = 30.0
            self.is_drowsy_handling = False
        
        else:
            print("⚡ 【リモコン専用モード】AI・カメラ追従・自動発話機能はオフに設定されました。")

    def _run_api_server(self):
        """M5Atom からの死活監視・赤外線命令・シャットダウンを受信するHTTPサーバー"""
        app = Flask(__name__)

        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        @app.route("/heartbeat", methods=["POST"])
        def heartbeat():
            data = request.get_json(silent=True) or {}
            device = data.get("device", "unknown")
            self.last_m5_heartbeat = time.time()
            print(f"💓 [M5 Heartbeat] M5Atom ({device}) からの生存信号を受信しました")
            return jsonify({"status": "ok"}), 200

        @app.route("/ir_command", methods=["POST"])
        def ir_command():
            data = request.get_json(silent=True) or {}
            cmd = data.get("command")
            if cmd:
                print(f"📡 [M5 IR Request] M5Atom から赤外線コマンド要求: {cmd}")
                self.trigger_force_ir(cmd)
                return jsonify({"status": "success", "command": cmd}), 200
            return jsonify({"status": "error", "message": "No command provided"}), 400

        @app.route("/shutdown", methods=["POST"])
        def shutdown():
            print("🛑 [M5 Shutdown] M5Atom からシャットダウン要求を受信しました")

            def _shutdown_sequence():
                time.sleep(1)
                try:
                    subprocess.run(
                        ["ssh", f"pi@{PI5_IP}", "sudo shutdown -h now"],
                        timeout=5,
                    )
                except Exception as e:
                    print(f"⚠️ Pi 5 シャットダウンエラー: {e}")

                os.system("sudo shutdown -h now")

            threading.Thread(target=_shutdown_sequence, daemon=True).start()
            return jsonify({"status": "shutdown initiated"}), 200

        print("🌐 M5Atom用 API サーバーを開始します (Port: 5001)...")
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)

    def _rotate_waist_async(self, deg: float):
        self.send_waist_command(str(deg))

    def send_waist_command(self, command: str):
        MICROPYTHON_IP = "192.168.0.11"
        PORT = 5007

        def _worker():
            self.is_waist_moving = True
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(5.0)
                    s.connect((MICROPYTHON_IP, PORT))
                    s.sendall(command.encode("utf-8"))

                    response = s.recv(1024).decode("utf-8")
                    print("Waist Response:", response.strip())
            except Exception as e:
                print(f"Waist Motor Socket Error: {e}")
            finally:
                self.is_waist_moving = False

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _set_servo_angle_safe(
        self, pin: int, angle: float, max_retries: int = 3
    ) -> bool:
        """Pi 5 のサーボサーバー経由で角度を設定（タイムアウト対策強化版）"""
        payload = f"{pin},{angle if angle is not None else 'None'}"

        for attempt in range(max_retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    # ★ 修正: タイムアウトを 0.2秒 から 1.0秒 に延長し、通信エラーを防ぐ
                    s.settimeout(1.0)
                    s.connect((PI5_IP, SERVO_PORT))
                    s.sendall(payload.encode("utf-8"))
                return True
            except Exception as e:
                if attempt == max_retries - 1:
                    print(
                        f"⚠️ [Pi 5 通信エラー] ピン {pin} への送信失敗: {e}"
                    )
                    return False
                # リトライ時の待機時間を少し確保
                time.sleep(0.02)
        return False

    def _neck_tracking_worker(self):
        """【首専用追従】目は素早く追い、首を遅れてサーボモーターで動かす"""
        EYE_UPDATE_INTERVAL = 0.05
        SERVO_CHECK_INTERVAL = 0.40
        ALPHA_EYE = 0.25
        SERVO_THRESHOLD = 15.0

        smoothed_angle = 0.0
        last_servo_time = time.time()

        while True:
            if self.neck_tracking_enabled and not self.motion_lock.locked():
                raw_angle = self.camera_client.latest_neck_angle

                if raw_angle is not None:
                    smoothed_angle = (
                        1.0 - ALPHA_EYE
                    ) * smoothed_angle + ALPHA_EYE * float(raw_angle)

                    eye_x = int(
                        round(max(-25.0, min(25.0, smoothed_angle * 0.5)))
                    )

                    if hasattr(self, "eyes") and self.eyes:
                        self.eyes.update_eyes(
                            left_x=eye_x,
                            right_x=eye_x,
                            left_y=0,
                            right_y=0,
                            left_blink=False,
                            right_blink=False,
                        )

                    now = time.time()
                    if (
                        now - last_servo_time >= SERVO_CHECK_INTERVAL
                        and not self.is_neck_moving
                    ):
                        clamped_target = max(
                            -60.0, min(60.0, round(smoothed_angle, 1))
                        )
                        current_neck = self.current_poses.get(1, 0.0)
                        neck_delta = abs(clamped_target - current_neck)

                        if neck_delta >= SERVO_THRESHOLD:
                            self.is_neck_moving = True
                            try:
                                self.move_smooth(
                                    {1: clamped_target},
                                    steps=12,
                                    interval=0.05,
                                    auto_off=False,
                                )
                                last_servo_time = time.time()
                            finally:
                                self.is_neck_moving = False

            time.sleep(EYE_UPDATE_INTERVAL)

    def _proactive_person_watcher(self):
        print("👀 [監視スレッド] 人物検知監視を開始しました...")

        while True:
            try:
                raw_name = self.camera_client.latest_recognized_name or "None"
                current_person = str(raw_name).strip().lower()
                now = time.time()
                
                time_since_last_chat = now - self.last_chat_time
                is_chat_cooling = time_since_last_chat < self.chat_cooldown

                is_name_match = (current_person in self.proactive_target_persons)
                is_cooldown_ok = (now - self.last_proactive_time > self.proactive_cooldown)
                
                is_not_busy = (
                    not self.is_proactive_speaking 
                    and not self.is_neck_moving 
                    and not is_chat_cooling
                    and not self.motion_lock.locked()
                )

                if is_name_match and is_cooldown_ok and is_not_busy:
                    print(f"✨ [自動検知成功] {raw_name} さんを検知！自動発話を起動します。")
                    self.last_proactive_time = now
                    self.is_proactive_speaking = True

                    threading.Thread(
                        target=self.trigger_proactive_talk,
                        args=(raw_name,),
                        daemon=True,
                    ).start()

            except Exception as e:
                print(f"⚠️ [Watcher Error] {e}")

            time.sleep(1.0)

    def trigger_proactive_talk(self, person_name: str):
        try:
            self.wait_for_neck_settle()

            reply_text, sequence, ir_commands = (
                self.chat_manager.generate_proactive_greeting(person_name)
            )

            print(f"🤖 Grogu(自発発話): {reply_text}")

            self.camera_client.socketio.emit(
                "chat_response",
                {
                    "user_text": f"（カメラで {person_name} を検知）",
                    "bot_text": reply_text,
                    "recognized": person_name,
                },
            )

            was_neck_tracking = self.neck_tracking_enabled
            self.neck_tracking_enabled = False

            try:
                def apply_motion(targets, emotion):
                    if emotion:
                        self._apply_eye_emotion(emotion)
                    if targets:
                        self.move_smooth(
                            targets, steps=30, interval=0.02, auto_off=False
                        )

                self.chat_manager.speak_with_motion(
                    text=reply_text,
                    sequence=sequence,
                    motion_executor_func=apply_motion,
                )

            finally:
                self.neck_tracking_enabled = was_neck_tracking
                self.disable_all()

        finally:
            self.is_proactive_speaking = False

    def wait_for_neck_settle(self, timeout: float = 2.0):
        start_time = time.time()
        while (self.is_neck_moving or self.motion_lock.locked()) and (time.time() - start_time < timeout):
            time.sleep(0.05)

    def _calculate_final_angle(self, jid: int, logical_angle: float) -> float:
        direction = DIRECTIONS.get(jid, 1)
        if isinstance(OFFSETS, list):
            offset = OFFSETS[jid] if jid < len(OFFSETS) else 0
        else:
            offset = OFFSETS.get(jid, 0)

        min_angle, max_angle = JOINT_LIMITS.get(jid, (-90.0, 90.0))
        clamped_logical = max(min_angle, min(max_angle, logical_angle))

        raw_angle = BASE_ANGLE + (clamped_logical * direction) + offset

        SAFE_MIN, SAFE_MAX = 5.0, 175.0
        return max(SAFE_MIN, min(SAFE_MAX, raw_angle))

    def move_smooth(
        self,
        targets: dict,
        steps: int = 60,
        interval: float = 0.02,
        auto_off: bool = True,
    ):
        """動作を指定角度までスムーズに補間移動させます（全体をmotion_lockで保護）"""
        if not targets:
            return

        safe_interval = max(0.02, interval)

        with self.motion_lock:
            with self.servo_lock:
                start_angles = {
                    jid: self.current_poses.get(jid, 0.0) for jid in targets.keys()
                }

            print(f"🎯 [サーボ動作] 指定された目標角度: {targets}")

            for s in range(1, steps + 1):
                progress = s / steps
                eased_progress = (1 - math.cos(progress * math.pi)) / 2

                for jid, target_angle in targets.items():
                    if jid not in JOINT_MAP:
                        continue

                    start = start_angles[jid]
                    inter_angle = (
                        start + (target_angle - start) * eased_progress
                    )

                    pin = JOINT_MAP[jid]
                    final_angle = self._calculate_final_angle(
                        jid, inter_angle
                    )

                    with self.servo_lock:
                        self._set_servo_angle_safe(pin, final_angle)

                time.sleep(safe_interval)

            with self.servo_lock:
                for jid, target_angle in targets.items():
                    self.current_poses[jid] = target_angle
                    CURRENT_ANGLES[jid] = target_angle

            if auto_off:
                time.sleep(0.05)
                self.disable_all()

    def reset_to_home(self, steps: int = 50, interval: float = 0.02):
        print("🏠 ホーム位置へ移動します...")
        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False
        try:
            home_targets = {
                jid: HOME_POSITIONS.get(jid, 0.0) for jid in JOINT_MAP.keys()
            }
            self.move_smooth(
                home_targets, steps=steps, interval=interval, auto_off=True
            )
        finally:
            self.neck_tracking_enabled = was_neck_tracking

    def force_action(self, hold_sec: float = 2.0):
        print("✨ [フォース発動] 腕と表情を同期動作させます")
        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False
        try:
            self._apply_eye_emotion("sleepy")
            self.move_smooth(
                {9: 30.0}, steps=40, interval=0.04, auto_off=False
            )
            time.sleep(hold_sec)
            self._apply_eye_emotion("happy")
            self.move_smooth({9: -30.0}, steps=40, interval=0.02, auto_off=True)
        finally:
            self.neck_tracking_enabled = was_neck_tracking

    def measure_distance_with_right_arm(
        self, raise_angle: float = 0.0, timeout: float = 2.0
    ) -> float:
        print(
            "📏 [距離測定] 右腕を上げてローカル超音波センサーで計測します..."
        )
        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False

        distance = None
        try:
            self._apply_eye_emotion("surprised")
            self.move_smooth(
                {8: raise_angle}, steps=40, interval=0.02, auto_off=False
            )

            time.sleep(0.3)

            raw_dist = self.distance_sensor.distance * 100
            distance = round(raw_dist, 2)
            print(f"📏 計測結果: {distance} cm")

            time.sleep(0.5)

        except Exception as e:
            print(f"⚠️ 距離計測エラー: {e}")
        finally:
            self._apply_eye_emotion("happy")
            self.move_smooth(
                {8: -90.0}, steps=40, interval=0.02, auto_off=True
            )
            self.neck_tracking_enabled = was_neck_tracking

        return distance

    def trigger_force_ir(self, command_name: str):
        print(
            f"🪄 [Force IR] コマンド '{command_name}'"
            " のフォース送信を開始します..."
        )
        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False
        try:
            self._apply_eye_emotion("sleepy")
            self.move_smooth(
                {9: 30.0}, steps=35, interval=0.015, auto_off=False
            )
            time.sleep(0.2)
            self.ir.send(command_name)
            time.sleep(0.8)
            self._apply_eye_emotion("happy")
            self.move_smooth(
                {9: -30.0}, steps=35, interval=0.015, auto_off=True
            )
        finally:
            self.neck_tracking_enabled = was_neck_tracking

    def trigger_force_ir_repeat(self, command_name: str, count: int = 5):
        print(
            f"⚡ [Force IR Repeat] コマンド '{command_name}' を {count}"
            " 回連続送信します..."
        )
        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False
        try:
            self._apply_eye_emotion("sleepy")
            self.move_smooth(
                {9: 30.0}, steps=35, interval=0.015, auto_off=False
            )
            time.sleep(0.2)
            self.ir.send_repeat(command_name, count=count, interval=0.15)
            time.sleep(0.5)
            self._apply_eye_emotion("happy")
            self.move_smooth(
                {9: -30.0}, steps=35, interval=0.015, auto_off=True
            )
        finally:
            self.neck_tracking_enabled = was_neck_tracking

    def _apply_eye_emotion(self, emotion: str):
        emo = emotion.lower()
        self.current_emotion = emo

        if hasattr(self.eyes, "set_emotion"):
            self.eyes.set_emotion(emo)
            return

        if emo in ["happy", "laugh"]:
            self.eyes.update_eyes(left_blink=False, right_blink=False)
        elif emo in ["sleepy", "closed"]:
            self.eyes.update_eyes(left_blink=True, right_blink=True)
        elif emo == "surprised":
            self.eyes.update_eyes(left_blink=False, right_blink=False)
        elif emo == "wink":
            self.eyes.update_eyes(left_blink=True, right_blink=False)
        elif emo == "dame":
            self.eyes.update_eyes(left_blink=False, right_blink=False)
        else:
            self.eyes.update_eyes(left_blink=False, right_blink=False)

    def disable_all(self):
        """全てのサーボトルクをオフ（消音）にする"""
        with self.servo_lock:
            for jid, pin in JOINT_MAP.items():
                self._set_servo_angle_safe(pin, None)
            self.is_disabled = True

    def process_user_input(self, user_input: str):
        if not user_input.strip():
            return
        
        self.last_chat_time = time.time()

        self.wait_for_neck_settle()
        current_recognized_person = self.camera_client.latest_recognized_name

        if any(
            kw in user_input.lower()
            for kw in ["距離", "測って", "はかって", "distance"]
        ):
            dist = self.measure_distance_with_right_arm()
            if dist is not None:
                user_input += f" (計測結果: {dist:.2f}cm)"

        elif any(kw in user_input.lower() for kw in ["フォース", "force"]):
            self.force_action(hold_sec=2.0)

        reply_text, sequence, ir_commands = self.chat_manager.send_message(
            user_text=user_input, recognized_name=current_recognized_person
        )

        print("🤖 [AIの生応答データ]")
        print(f"  - テキスト返答 (reply_text): {reply_text}")
        print(f"  - アクションシーケンス (sequence): {sequence}")
        print(f"  - IRコマンド (ir_commands): {ir_commands}")

        self.camera_client.socketio.emit(
            "chat_response",
            {
                "user_text": user_input,
                "bot_text": reply_text,
                "recognized": current_recognized_person,
            },
        )

        if ir_commands:
            print(f"🪄 [LLM IR Control] 送信要求コマンド: {ir_commands}")
            for cmd in ir_commands:
                self.trigger_force_ir(cmd)

        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False

        try:
            def apply_motion(targets, emotion):
                if emotion:
                    self._apply_eye_emotion(emotion)
                if targets:
                    self.move_smooth(
                        targets, steps=30, interval=0.02, auto_off=False
                    )

            self.chat_manager.speak_with_motion(
                text=reply_text,
                sequence=sequence,
                motion_executor_func=apply_motion,
            )

        finally:
            self.neck_tracking_enabled = was_neck_tracking
            self.disable_all()

    def start_chat_session(self):
        self.chat_active_flag = True
        self.reset_to_home(steps=50)
        self.eyes.update_eyes(
            left_blink=False, right_blink=False
        )

        print("\n💬 --- 対話モード開始 ---")
        while self.chat_active_flag:
            user_input = self.chat_manager.get_input()
            if user_input.lower() in ["quit", "exit", "終了"]:
                break
            self.process_user_input(user_input)

    def close(self):
        self.chat_active_flag = False
        self.camera_client.running_flag = False

        try:
            self.reset_to_home(steps=40, interval=0.02)
            time.sleep(0.2)
        except Exception as e:
            print(f"⚠️ 終了時ホーム復帰エラー: {e}")

        if hasattr(self, "distance_sensor"):
            print("📏 超音波センサーを閉じています...")
            self.distance_sensor.close()

        if hasattr(self, "eyes"):
            self.eyes.close()

        self.disable_all()

        if hasattr(self, "remote_manager"):
            self.remote_manager.stop_support_script()

    def wait_for_owner_return(
        self, target_name: str = "M5_TAG_01", rssi_threshold: int = -85
    ):
        print(f"\n🏠 [帰宅待機] '{target_name}' のBLE電波を探しています...")
        print("※ご主人が帰宅するまでロボットは待機状態になります。\n")

        detected = threading.Event()
        detected_rssi = [None]

        def _ble_callback(device, advertisement_data):
            name = advertisement_data.local_name or device.name
            if name == target_name:
                rssi = advertisement_data.rssi
                if rssi >= rssi_threshold:
                    detected_rssi[0] = rssi
                    detected.set()

        async def _scan():
            scanner = BleakScanner(detection_callback=_ble_callback)
            await scanner.start()
            while not detected.is_set():
                await asyncio.sleep(0.5)
            await scanner.stop()

        try:
            asyncio.run(_scan())
        except KeyboardInterrupt:
            print("\n帰宅待機をキャンセルしました。")
            return False

        print(
            f"\n🎉 [帰宅検知] ご主人のタグ '{target_name}' を検知しました！"
            f" (RSSI: {detected_rssi[0]} dBm)"
        )
        return True

    def _welcome_home_action(self):
        if self.has_welcomed:
            return

        self.has_welcomed = True

        print("🤖 Grogu: おかえりなさい！")

        was_neck_tracking = self.neck_tracking_enabled
        self.neck_tracking_enabled = False

        try:
            self._apply_eye_emotion("happy")

            welcome_text = (
                "おかえりなさい！きょうもおつかれさま！"
            )

            welcome_sequence = [
                [{8: 30.0, 9: 30.0}, "happy", 1.2],
                [{8: -30.0, 9: -30.0, 1: 20.0}, "wink", 1.0],
                [{8: -90.0, 9: -90.0, 1: 0.0}, "happy", 0.5],
            ]

            def apply_welcome_motion(targets, emotion):
                if emotion:
                    self._apply_eye_emotion(emotion)
                if targets:
                    self.move_smooth(
                        targets, steps=30, interval=0.02, auto_off=False
                    )

            self.chat_manager.speak_with_motion(
                text=welcome_text,
                sequence=welcome_sequence,
                motion_executor_func=apply_welcome_motion,
            )

        finally:
            self._apply_eye_emotion("happy")
            self.neck_tracking_enabled = was_neck_tracking
            self.disable_all()
            
    def reset_eyes_display(self):
        if hasattr(self, "eyes") and self.eyes:
            with self.servo_lock:
                self.eyes.reset_display()
                
    def handle_drowsy_alert(self, recognized_name: str = "None"):
        now = time.time()
        if self.is_drowsy_handling or self.motion_lock.locked():
            return
        if now - self.last_drowsy_alert_time < self.drowsy_cooldown:
            return

        self.last_drowsy_alert_time = now
        self.is_drowsy_handling = True

        def _alert_worker():
            print(f"🚨 [居眠り検知] {recognized_name} さんが眠っています！名前を呼んで起こします！")
            was_neck_tracking = self.neck_tracking_enabled
            self.neck_tracking_enabled = False

            try:
                self._apply_eye_emotion("surprised")

                clean_name = str(recognized_name).strip()
                if clean_name and clean_name not in ["None", "Unknown"]:
                    alert_text = f"{clean_name}さん！お昼寝の時間じゃないよ！起きるのだ！"
                else:
                    alert_text = "こらー！お昼寝の時間じゃないよ！起きるのだ！"
                
                alert_sequence = [
                    [{9: 45.0, 1: -15.0}, "surprised", 1.0],
                    [{9: 30.0, 1: 15.0}, "wink", 1.0],
                    [{9: -30.0, 1: 0.0}, "happy", 0.5],
                ]

                def apply_alert_motion(targets, emotion):
                    if emotion:
                        self._apply_eye_emotion(emotion)
                    if targets:
                        self.move_smooth(targets, steps=20, interval=0.02, auto_off=False)

                if hasattr(self, "chat_manager") and self.chat_manager:
                    self.chat_manager.speak_with_motion(
                        text=alert_text,
                        sequence=alert_sequence,
                        motion_executor_func=apply_alert_motion
                    )
                else:
                    print(f"🤖 Grogu: {alert_text}")
                    self.force_action(hold_sec=2.0)

            except Exception as e:
                print(f"⚠️ 居眠り対応エラー: {e}")
            finally:
                self._apply_eye_emotion("happy")
                self.neck_tracking_enabled = was_neck_tracking
                self.disable_all()
                self.is_drowsy_handling = False

        threading.Thread(target=_alert_worker, daemon=True).start()