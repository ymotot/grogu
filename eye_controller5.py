import sys
import time
import random
import threading
from PIL import Image, ImageDraw
import RPi.GPIO as GPIO
import spidev
import numpy as np

# --- ディスプレイ定数 ---
WIDTH = 240
HEIGHT = 240

# --- ピン設定 ---
DC_PIN = 25
RST_PIN = 27
CS_LEFT_PIN = 8   # 左目 CE0
CS_RIGHT_PIN = 7  # 右目 CE1

# --- カラー定義 ---
HOLLY_GREEN = (5, 45, 20)          # 背景メイン色
HOLLY_GREEN_LIGHT = (15, 75, 35)    # 背景ハイライト
HOLLY_GREEN_DARK = (2, 25, 10)      # 背景シャドウ

TOP_LID_COLOR = (0, 100, 50)
BOTTOM_LID_COLOR = (0, 100, 50)
LINE_COLOR = (120, 255, 170)
LINE_SHADOW = (0, 15, 5)


class GroguEyeController:
    def __init__(self, dc_pin=DC_PIN, rst_pin=RST_PIN, cs_left=CS_LEFT_PIN, cs_right=CS_RIGHT_PIN):
        self.dc_pin = dc_pin
        self.rst_pin = rst_pin
        self.cs_left = cs_left
        self.cs_right = cs_right

        # ★ 修正: 再入可能な RLock を使用してデッドロックを防ぐ
        self.lock = threading.RLock()

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.dc_pin, GPIO.OUT)
        GPIO.setup(self.rst_pin, GPIO.OUT)
        GPIO.setup(self.cs_left, GPIO.OUT)
        GPIO.setup(self.cs_right, GPIO.OUT)

        GPIO.output(self.cs_left, GPIO.HIGH)
        GPIO.output(self.cs_right, GPIO.HIGH)

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 24000000
        self.spi.mode = 0b00
        self.spi.no_cs = True

        self.reset_display()
        self.init_gc9a01(self.cs_left)
        self.init_gc9a01(self.cs_right)

    def send_command(self, cmd, cs_pin):
        with self.lock:
            GPIO.output(self.dc_pin, GPIO.LOW)
            GPIO.output(cs_pin, GPIO.LOW)
            self.spi.writebytes([cmd])
            GPIO.output(cs_pin, GPIO.HIGH)

    def send_data(self, data, cs_pin):
        with self.lock:
            GPIO.output(self.dc_pin, GPIO.HIGH)
            GPIO.output(cs_pin, GPIO.LOW)
            if isinstance(data, int):
                self.spi.writebytes([data])
            else:
                for i in range(0, len(data), 4096):
                    self.spi.writebytes(data[i:i+4096])
            GPIO.output(cs_pin, GPIO.HIGH)

    def reset_display(self):
        with self.lock:
            GPIO.output(self.rst_pin, GPIO.HIGH)
            time.sleep(0.01)
            GPIO.output(self.rst_pin, GPIO.LOW)
            time.sleep(0.05)
            GPIO.output(self.rst_pin, GPIO.HIGH)
            time.sleep(0.05)
        
        self.init_gc9a01(self.cs_left)
        self.init_gc9a01(self.cs_right)

    def init_gc9a01(self, cs_pin, rotation_cmd=0x48):
        self.send_command(0xEF, cs_pin)
        self.send_command(0xEB, cs_pin); self.send_data(0x14, cs_pin)
        self.send_command(0xFE, cs_pin)
        self.send_command(0xEF, cs_pin)
        self.send_command(0xEB, cs_pin); self.send_data(0x14, cs_pin)
        self.send_command(0x84, cs_pin); self.send_data(0x40, cs_pin)
        self.send_command(0x85, cs_pin); self.send_data(0xFF, cs_pin)
        self.send_command(0x86, cs_pin); self.send_data(0xFF, cs_pin)
        self.send_command(0x87, cs_pin); self.send_data(0xFF, cs_pin)
        self.send_command(0x88, cs_pin); self.send_data(0x0A, cs_pin)
        self.send_command(0x89, cs_pin); self.send_data(0x21, cs_pin)
        self.send_command(0x8A, cs_pin); self.send_data(0x00, cs_pin)
        self.send_command(0x8B, cs_pin); self.send_data(0x80, cs_pin)
        self.send_command(0x8C, cs_pin); self.send_data(0x01, cs_pin)
        self.send_command(0x8D, cs_pin); self.send_data(0x01, cs_pin)
        self.send_command(0x8E, cs_pin); self.send_data(0xFF, cs_pin)
        self.send_command(0x8F, cs_pin); self.send_data(0xFF, cs_pin)
        self.send_command(0xB6, cs_pin); self.send_data([0x00, 0x00], cs_pin)
        self.send_command(0x36, cs_pin); self.send_data(rotation_cmd, cs_pin)
        self.send_command(0x3A, cs_pin); self.send_data(0x05, cs_pin)
        self.send_command(0x90, cs_pin); self.send_data([0x08, 0x08, 0x08, 0x08], cs_pin)
        self.send_command(0xBD, cs_pin); self.send_data(0x06, cs_pin)
        self.send_command(0xBC, cs_pin); self.send_data(0x00, cs_pin)
        self.send_command(0xFF, cs_pin); self.send_data([0x60, 0x01, 0x04], cs_pin)
        self.send_command(0xC3, cs_pin); self.send_data(0x13, cs_pin)
        self.send_command(0xC4, cs_pin); self.send_data(0x13, cs_pin)
        self.send_command(0xC9, cs_pin); self.send_data(0x22, cs_pin)
        self.send_command(0xBE, cs_pin); self.send_data(0x11, cs_pin)
        self.send_command(0xE1, cs_pin); self.send_data([0x10, 0x0E], cs_pin)
        self.send_command(0xDF, cs_pin); self.send_data([0x21, 0x0C, 0x02], cs_pin)
        self.send_command(0xF0, cs_pin); self.send_data([0x45, 0x09, 0x08, 0x08, 0x26, 0x2A], cs_pin)
        self.send_command(0xF1, cs_pin); self.send_data([0x43, 0x70, 0x72, 0x36, 0x37, 0x6F], cs_pin)
        self.send_command(0xF2, cs_pin); self.send_data([0x45, 0x09, 0x08, 0x08, 0x26, 0x2A], cs_pin)
        self.send_command(0xF3, cs_pin); self.send_data([0x43, 0x70, 0x72, 0x36, 0x37, 0x6F], cs_pin)
        self.send_command(0xED, cs_pin); self.send_data([0x1B, 0x0B], cs_pin)
        self.send_command(0xAE, cs_pin); self.send_data(0x77, cs_pin)
        self.send_command(0xCD, cs_pin); self.send_data(0x63, cs_pin)
        self.send_command(0x70, cs_pin); self.send_data([0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03], cs_pin)
        self.send_command(0xE8, cs_pin); self.send_data(0x34, cs_pin)
        self.send_command(0x62, cs_pin); self.send_data([0x18, 0x0D, 0x71, 0xED, 0x70, 0x70, 0x18, 0x0D, 0x71, 0xED, 0x70, 0x70], cs_pin)
        self.send_command(0x63, cs_pin); self.send_data([0x18, 0x11, 0x71, 0xF1, 0x70, 0x70, 0x18, 0x11, 0x71, 0xF1, 0x70, 0x70], cs_pin)
        self.send_command(0x64, cs_pin); self.send_data([0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07], cs_pin)
        self.send_command(0x66, cs_pin); self.send_data([0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00], cs_pin)
        self.send_command(0x67, cs_pin); self.send_data([0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98], cs_pin)
        self.send_command(0x74, cs_pin); self.send_data([0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00], cs_pin)
        self.send_command(0x98, cs_pin); self.send_data([0x3E, 0x07], cs_pin)
        self.send_command(0x35, cs_pin)
        self.send_command(0x21, cs_pin)  
        self.send_command(0x11, cs_pin)
        time.sleep(0.12)
        self.send_command(0x29, cs_pin)
        time.sleep(0.02)

    def display_image(self, img, cs_pin):
        self.send_command(0x2A, cs_pin)
        self.send_data([0x00, 0x00, 0x00, 0xEF], cs_pin)
        self.send_command(0x2B, cs_pin)
        self.send_data([0x00, 0x00, 0x00, 0xEF], cs_pin)
        self.send_command(0x2C, cs_pin)

        img_np = np.array(img.convert("RGB"), dtype=np.uint8)
        r = img_np[:, :, 0].astype(np.uint16)
        g = img_np[:, :, 1].astype(np.uint16)
        b = img_np[:, :, 2].astype(np.uint16)

        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buffer = rgb565.astype(">u2").tobytes()

        self.send_data(list(buffer), cs_pin)

    def draw_cross_at_center(self, draw, cx, cy, size=50, color=(255, 50, 50), width=12):
        half = size // 2
        draw.line([(cx - half, cy - half), (cx + half, cy + half)], fill=color, width=width)
        draw.line([(cx - half, cy + half), (cx + half, cy - half)], fill=color, width=width)

    def create_grogu_eye(self, eye_side, pupil_x_offset=0, pupil_y_offset=0, is_blink=False, is_cross=False,
                        top_lid_ratio=0.0, bottom_lid_ratio=0.0, slant_angle=0):
        img = Image.new("RGB", (WIDTH, HEIGHT), HOLLY_GREEN)
        draw = ImageDraw.Draw(img)

        draw.chord([ -20, -20, WIDTH + 20, HEIGHT + 20 ], start=200, end=340, fill=HOLLY_GREEN_LIGHT)
        draw.chord([ 10, 10, WIDTH - 10, HEIGHT - 10 ], start=20, end=160, fill=HOLLY_GREEN_DARK)

        if eye_side == 'left':
            base_x = 160
            base_y = 90
        else:
            base_x = 100
            base_y = 120

        sclera_r = 110
        pupil_r = 85

        if is_blink:
            draw.ellipse([base_x - sclera_r, base_y - sclera_r, base_x + sclera_r, base_y + sclera_r], fill=TOP_LID_COLOR)
            draw.line([(base_x - 100, base_y + 2), (base_x + 100, base_y + 2)], fill=LINE_SHADOW, width=6)
            draw.line([(base_x - 100, base_y), (base_x + 100, base_y)], fill=LINE_COLOR, width=4)
        else:
            draw.ellipse([base_x - sclera_r, base_y - sclera_r, base_x + sclera_r, base_y + sclera_r], fill=(240, 240, 240))
            
            cx = base_x + pupil_x_offset
            cy = base_y + pupil_y_offset

            if is_cross:
                self.draw_cross_at_center(draw, cx, cy, size=70, color=(255, 30, 30), width=14)
            else:
                draw.ellipse([cx - pupil_r, cy - pupil_r, cx + pupil_r, cy + pupil_r], fill=(0, 0, 0))
                draw.ellipse([cx - 40, cy - 40, cx - 12, cy - 12], fill=(255, 255, 255))
                draw.ellipse([cx + 15, cy + 20, cx + 28, cy + 32], fill=(200, 200, 200))

            eye_height = sclera_r * 2

            if top_lid_ratio > 0.0:
                lid_h = int(eye_height * top_lid_ratio)
                top_y1 = base_y - sclera_r - 20
                top_y2 = base_y - sclera_r + lid_h
                slant = slant_angle if eye_side == 'left' else -slant_angle
                
                polygon_coords = [
                    (base_x - sclera_r - 20, top_y1),
                    (base_x + sclera_r + 20, top_y1),
                    (base_x + sclera_r + 20, top_y2 + slant),
                    (base_x - sclera_r - 20, top_y2 - slant)
                ]
                draw.polygon(polygon_coords, fill=TOP_LID_COLOR)
                draw.line([(base_x - sclera_r - 20, top_y2 - slant), (base_x + sclera_r + 20, top_y2 + slant)], fill=LINE_COLOR, width=3)

            if bottom_lid_ratio > 0.0:
                lid_h = int(eye_height * bottom_lid_ratio)
                bottom_y1 = base_y + sclera_r - lid_h
                bottom_y2 = base_y + sclera_r + 20
                slant = slant_angle if eye_side == 'left' else -slant_angle

                polygon_coords = [
                    (base_x - sclera_r - 20, bottom_y1 + slant),
                    (base_x + sclera_r + 20, bottom_y1 - slant),
                    (base_x + sclera_r + 20, bottom_y2),
                    (base_x - sclera_r - 20, bottom_y2)
                ]
                draw.polygon(polygon_coords, fill=BOTTOM_LID_COLOR)
                draw.line([(base_x - sclera_r - 20, bottom_y1 + slant), (base_x + sclera_r + 20, bottom_y1 - slant)], fill=LINE_COLOR, width=3)

        return img

    def update_eyes(self, left_x=0, left_y=0, left_blink=False, 
                    right_x=0, right_y=0, right_blink=False, 
                    left_cross=False, right_cross=False,
                    top_lid=0.0, bottom_lid=0.0, slant=0):
        with self.lock:
            img_left = self.create_grogu_eye('left', pupil_x_offset=left_x, pupil_y_offset=left_y, 
                                            is_blink=left_blink, is_cross=left_cross,
                                            top_lid_ratio=top_lid, bottom_lid_ratio=bottom_lid, slant_angle=slant)
            self.display_image(img_left, self.cs_left)
            
            img_right = self.create_grogu_eye('right', pupil_x_offset=right_x, pupil_y_offset=right_y, 
                                             is_blink=right_blink, is_cross=right_cross,
                                             top_lid_ratio=top_lid, bottom_lid_ratio=bottom_lid, slant_angle=slant)
            self.display_image(img_right, self.cs_right)

    def set_emotion(self, emotion_name, pupil_x=0, pupil_y=0):
        emotions = {
            "happy": {"top_lid": 0.1, "bottom_lid": 0.4, "slant": -5, "left_blink": False, "right_blink": False, "cross": False},
            "laugh": {"top_lid": 0.3, "bottom_lid": 0.5, "slant": 0, "left_blink": False, "right_blink": False, "cross": False},
            "sad": {"top_lid": 0.35, "bottom_lid": 0.1, "slant": -20, "left_blink": False, "right_blink": False, "cross": False},
            "angry": {"top_lid": 0.4, "bottom_lid": 0.1, "slant": 25, "left_blink": False, "right_blink": False, "cross": False},
            "surprised": {"top_lid": 0.0, "bottom_lid": 0.0, "slant": 0, "left_blink": False, "right_blink": False, "cross": False},
            "sleepy": {"top_lid": 0.6, "bottom_lid": 0.2, "slant": -5, "left_blink": False, "right_blink": False, "cross": False},
            "wink": {"top_lid": 0.0, "bottom_lid": 0.0, "slant": 0, "left_blink": True, "right_blink": False, "cross": False},
            "dame": {"top_lid": 0.0, "bottom_lid": 0.0, "slant": 0, "left_blink": False, "right_blink": False, "cross": True}
        }

        emo = emotions.get(emotion_name.lower(), {"top_lid": 0.0, "bottom_lid": 0.0, "slant": 0, "left_blink": False, "right_blink": False, "cross": False})
        
        self.update_eyes(
            left_x=pupil_x, left_y=pupil_y,
            right_x=pupil_x, right_y=pupil_y,
            left_blink=emo["left_blink"],
            right_blink=emo["right_blink"],
            left_cross=emo["cross"],
            right_cross=emo["cross"],
            top_lid=emo["top_lid"],
            bottom_lid=emo["bottom_lid"],
            slant=emo["slant"]
        )

    def close(self):
        with self.lock:
            GPIO.output(self.cs_left, GPIO.HIGH)
            GPIO.output(self.cs_right, GPIO.HIGH)
            self.spi.close()


def run_emotion_sequence_test(eyes, duration=2.5):
    emotions = ["happy", "laugh", "sad", "angry", "surprised", "sleepy", "wink", "dame"]
    print("\n--- 全感情シーケンステスト開始 ---")
    for emo in emotions:
        print(f"[表示中]: 感情 -> '{emo}'")
        eyes.set_emotion(emo)
        time.sleep(duration)
    print("--- テスト完了 ---\n")

def run_interactive_emotion_test(eyes):
    available_emotions = ["happy", "laugh", "sad", "angry", "surprised", "sleepy", "wink", "dame"]
    print("\n=== 対話型感情テストモード ===")
    print(f"利用可能な感情: {', '.join(available_emotions)}")
    print("終了するには 'exit' または 'q' と入力してください。\n")

    while True:
        try:
            user_input = input("感情名を入力 > ").strip().lower()
            if user_input in ['exit', 'q']:
                break
            if user_input in available_emotions:
                eyes.set_emotion(user_input)
                print(f" -> 表情を '{user_input}' に変更しました。")
            else:
                print(f" [エラー] 不明な感情です。次のいずれかを指定してください: {available_emotions}")
        except EOFError:
            break


if __name__ == "__main__":
    try:
        eyes = GroguEyeController()
        print("目を初期化しました。")

        if "--emotion" in sys.argv:
            while True:
                run_emotion_sequence_test(eyes, duration=2.0)

        elif "--interactive" in sys.argv:
            run_interactive_emotion_test(eyes)

        elif "--blink" in sys.argv:
            print("瞬きテストを実行します...")
            eyes.update_eyes(left_blink=False, right_blink=False)
            time.sleep(1.0)
            while True:
                eyes.update_eyes(left_blink=True, right_blink=True)
                time.sleep(0.3)
                eyes.update_eyes(left_blink=False, right_blink=False)
                time.sleep(2.0)

        else:
            run_emotion_sequence_test(eyes, duration=2.0)

            print("通常の視線移動アニメーションを開始します...")
            current_x = 0
            step = -2
            while True:
                current_y = int(current_x * 0.4)
                is_blink = (random.randint(1, 35) == 1)
                
                eyes.update_eyes(left_x=current_x, left_y=current_y, 
                                 right_x=current_x, right_y=current_y, 
                                 left_blink=is_blink, right_blink=is_blink)
                
                if current_x <= -40:
                    step = 2
                elif current_x >= 0:
                    step = -2
                current_x += step
                time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n停止しました")
    finally:
        eyes.close()
        GPIO.cleanup()