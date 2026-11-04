import network
import socket
import utime
from machine import Pin, PWM


# --- 1. カメラチルト（ステッピングモーター）クラス ---
# （中身は完全に同じなので省略せずにそのまま使用します）
class StepperCameraTilt:
    def __init__(
        self,
        motor_pins=(18, 17, 16, 19),
        cycles_per_rev=512,
        deg_per_rev=45.0,
        step_delay_ms=3,
    ):
        self.motorPins = motor_pins
        self.motors = [Pin(pin, Pin.OUT) for pin in self.motorPins]
        self.CCWStep = (0x01, 0x02, 0x04, 0x08)
        self.CWStep = (0x08, 0x04, 0x02, 0x01)
        self.CYCLES_PER_REV = cycles_per_rev
        self.DEG_PER_REV = deg_per_rev
        self.CYCLES_PER_DEG = self.CYCLES_PER_REV / self.DEG_PER_REV
        self.STEP_DELAY_MS = step_delay_ms
        self.current_angle = 0.0

    def _move_one_period(self, direction, ms):
        for j in range(4):
            for i in range(4):
                step_pattern = (
                    self.CCWStep[j] if direction == 1 else self.CWStep[j]
                )
                bit_val = 1 if (step_pattern == (1 << i)) else 0
                self.motors[i].value(bit_val)
            utime.sleep_ms(max(ms, 3))

    def _move_cycles(self, direction, ms, cycles):
        for _ in range(cycles):
            self._move_one_period(direction, ms)

    def _move_deg(self, deg):
        if deg == 0:
            return
        direction = 0 if deg > 0 else 1
        cycles = int(abs(deg) * self.CYCLES_PER_DEG)
        self._move_cycles(direction, self.STEP_DELAY_MS, cycles)
        self.current_angle += deg

    def pan(self, deg):
        self._move_deg(deg)

    def return_to_home(self):
        if self.current_angle == 0:
            return
        self._move_deg(-self.current_angle)
        self.current_angle = 0.0

    def motor_stop(self):
        for m in self.motors:
            m.value(0)


# --- 2. ピン設定 ---
audio_pwm = PWM(Pin(12))
audio_pwm.freq(125000) 

tilt = StepperCameraTilt(motor_pins=(18, 17, 16, 19))

print("--- Program Start ---")

# --- 3. Wi-Fi接続設定 ---
SSID = "WARPSTAR-g-412d63"
PASSWORD = "5e4c1f81a99aa"

wlan = network.WLAN(network.STA_IF)
wlan.active(True)

if not wlan.isconnected():
    print(f"Connecting to {SSID}...")
    wlan.connect(SSID, PASSWORD)
    retry = 0
    while not wlan.isconnected() and retry < 15:
        print(f"Waiting for Wi-Fi... ({retry+1}/15)")
        utime.sleep(1)
        retry += 1

if not wlan.isconnected():
    print("Error: Wi-Fi connection failed!")
    raise SystemExit

print("Connected! Pico IP:", wlan.ifconfig()[0])


# --- 4. 音声再生関数 ---
def play_raw_audio(raw_data):
    for sample in raw_data:
        audio_pwm.duty_u16(sample * 257)
        utime.sleep_us(120)
    audio_pwm.duty_u16(32768)


# --- 5. ソケット設定 ---
PORT_AUDIO = 5006
PORT_TILT = 5007

# 音声用ソケット (UDPのまま非ブロック)
# sock_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# sock_audio.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# sock_audio.bind(("0.0.0.0", PORT_AUDIO))
# sock_audio.setblocking(False)

# 音声用ソケットをTCPに変更 (非ブロックで待機)
addr_audio = socket.getaddrinfo("0.0.0.0", PORT_AUDIO)[0][-1]
server_audio = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_audio.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_audio.bind(addr_audio)
server_audio.listen(1)
server_audio.setblocking(False)  # 接続待ちでプログラムを止めない

# チルト用ソケット (確実に動いたTCPに変更し、非ブロックで待機)
addr_tilt = socket.getaddrinfo("0.0.0.0", PORT_TILT)[0][-1]
server_tilt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_tilt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_tilt.bind(addr_tilt)
server_tilt.listen(1)
server_tilt.setblocking(False)  # ← ここが重要：接続待ちでプログラムを止めない

# print(f"Server Active -> Audio(UDP): {PORT_AUDIO}, Tilt(TCP): {PORT_TILT}")
print(f"Server Active -> Audio(TCP): {PORT_AUDIO}, Tilt(TCP): {PORT_TILT}")


# --- 6. メインループ ---
audio_buffer = bytearray()

# try:
#     while True:
#         # --- A. 音声再生リクエストの処理 (UDP) ---
#         try:
#             data, addr = sock_audio.recvfrom(2048)
#             if data:
#                 if data == b"START":
#                     audio_buffer = bytearray()
#                 elif data == b"END":
#                     play_raw_audio(audio_buffer)
#                     audio_buffer = bytearray()
#                 else:
#                     audio_buffer.extend(data)
#         except OSError:
#             pass  # データが届いていない時は無視して進む

# --- 6. メインループ ---
try:
    while True:
        # --- A. 音声再生リクエストの処理 (TCP) ---
        try:
            conn_audio, addr_a = server_audio.accept()
            conn_audio.settimeout(5.0)
            audio_buffer = bytearray()
            try:
                while True:
                    chunk = conn_audio.recv(1024)
                    if not chunk:
                        break
                    audio_buffer.extend(chunk)
                
                # データを受け取り終わったら再生
                if audio_buffer:
                    play_raw_audio(audio_buffer)
            except Exception as e:
                print("Audio TCP Receive Error:", e)
            finally:
                conn_audio.close()  # 必ずここでコネクションを完全に切断する
        except OSError:
            pass  # 接続要求が来ていない時はスルー

        # --- B. チルト制御リクエストの処理 (TCP) ---
        try:
            # クライアントからのTCP接続要求があるかチェック（無ければエラーになり下でpassされる）
            conn, addr = server_tilt.accept()
            print("Connected from:", addr)
            
            # 接続されたら少しだけ待機時間を設けてデータを受け取る
            conn.settimeout(2.0)
            try:
                data = conn.recv(1024).decode("utf-8").strip()
                if data:
                    print(f"Received command: {data}")
                    if data.upper() == "HOME":
                        tilt.return_to_home()
                        tilt.motor_stop()
                        conn.send(b"SUCCESS: Returned to Home\n")
                    else:
                        try:
                            deg = float(data)
                            tilt.pan(deg)
                            tilt.motor_stop()
                            res = f"SUCCESS: Moved {deg} deg. Current angle: {tilt.current_angle}\n"
                            conn.send(res.encode())
                        except ValueError:
                            conn.send(b"ERROR: Invalid input.\n")
            except Exception as e:
                print("TCP Receive Error:", e)
            finally:
                conn.close()  # 通信が終わったら必ず閉じる

        except OSError:
            pass  # 接続要求が来ていない時は無視してループを続ける

        utime.sleep_ms(2)  # CPU負荷を下げるためのウエイト

# except KeyboardInterrupt:
#     print("Program interrupted.")
# finally:
#     tilt.return_to_home()
#     tilt.motor_stop()
#     sock_audio.close()
#     server_tilt.close()
#     print("Returned to home & Sockets closed.")

except KeyboardInterrupt:
    print("Program interrupted.")
finally:
    tilt.return_to_home()
    tilt.motor_stop()
    server_audio.close()  # ← server_audio に変更する
    server_tilt.close()
    print("Returned to home & Sockets closed.")