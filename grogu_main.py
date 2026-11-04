import argparse  # ← 追加
import time
import RPi.GPIO as GPIO
from grogu_controller import CatRobotController

if __name__ == "__main__":
    # ── 1. 起動時に引数（--remote-only）を受け取れるように設定 ──
    parser = argparse.ArgumentParser(description="Grogu Robot Controller Main")
    parser.add_argument("--remote-only", action="store_true", help="AIやBLE待機をスキップし、リモコン・腰制御のみを動かす")
    args = parser.parse_args()

    robot = None
    try:
        print("\n--- 1. ロボットコントローラーの初期化 ---")
        API_KEY = "AIzaSyA594srtfXCbUOUejm4YEInVEm-8YwWgVg"
        
        # ── 2. 引数の有無（True/False）をコントローラーに渡す ──
        robot = CatRobotController(gemini_api_key=API_KEY, use_mic=False, remote_only_mode=args.remote_only, enable_proactive=True)
        
        if args.remote_only:
            print("\n--- ⚡ リモコン専用モードで稼働中 ---")
            print("※ AI対話、自動追従、BLE帰宅待機はスキップされました。WebUIからの操作に特化します。")
        else:
            print("\n--- 2. ご主人の帰宅を待機中 ---")
            # 通常モードのときだけBLE帰宅待機を行う
            if robot.wait_for_owner_return(target_name="M5_TAG_01", rssi_threshold=-85):
                print("\n--- 3. 監視・WebUI操作モード稼働中 ---")
                print("※ WebUIからの操作、およびErikoさんの自動検知による発話が有効です。")

        # プログラムが終了しないように常時待機
        while True:
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n\n⚠️ Ctrl+C を検出しました。安全終了を開始します...")
    except Exception as e:
        print(f"\n予期しないエラーが発生しました: {e}")
    finally:
        if robot:
            print("\n--- 4. 終了処理 ---")
            robot.close()
        GPIO.cleanup()
        print("すべての安全終了処理が完了しました。")