import sys
import os
import time
from datetime import datetime

# Honbanディレクトリをsys.pathに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Honban')))

from intraday_dip_buy_bot import IntradayDipBuyBot

def run_manual_entry_test():
    """
    IntradayDipBuyBotのエントリーロジックを手動でトリガーし、
    注文発注と約定確認のプロセスをテストする。
    """
    bot = None
    try:
        print("--- テスト開始: IntradayDipBuyBot 手動エントリーテスト ---")
        
        # 1. botをインスタンス化
        bot = IntradayDipBuyBot()
        print(f"Botを初期化しました。銘柄: {bot.ticker}, 数量: {bot.qty}")

        # 2. APIトークンを取得
        print("APIトークンを取得しています...")
        if not bot.api.get_token():
            print("[エラー] APIトークンの取得に失敗しました。テストを中止します。")
            return
        print("APIトークンの取得に成功しました。")

        # 3. エントリー注文を直接発注
        print("--- 寄成（前場）注文を発注します ---")
        print("警告: 3秒後に実際の寄成買い注文が発注されます。")
        time.sleep(3)

        success, order_info = bot.api.send_market_order(
            bot.ticker, bot.exchange, bot.qty, "2", front_order_type=13 # 2:BUY, 13:寄成(前場)
        )

        if not success:
            print(f"[エラー] 注文の発注に失敗しました: {order_info}")
            print("テストを中止します。")
            return

        # 4. Botの状態を手動で設定
        bot.entry_order_id = order_info['OrderId']
        bot.state = 'WAITING_FOR_ENTRY'
        print(f"注文が発注されました。OrderId: {bot.entry_order_id}")
        print(f"ボットの状態を {bot.state} に設定しました。")
        
        # 5. 約定確認 (市場が開くまで約定しないため、この部分は長時間待機になる)
        print("--- 約定監視 ---")
        print("寄成注文のため、次の前場の寄り付きまで約定しません。")
        print("このテストでは、注文が正しく受け付けられたこと（OrderIdが発行されたこと）を確認します。")
        print(f"発行されたOrderId: {bot.entry_order_id}")
        print("[成功] 寄成注文は正常に受け付けられました。")
        print("テストを終了します。kabuステーションで注文を確認してください。")

    except Exception as e:
        print(f"テスト中に予期せぬエラーが発生しました: {e}")
        if bot and hasattr(bot, 'logger'):
            bot.logger.error("手動エントリーテスト中にエラー", exc_info=True)
    finally:
        print("--- テスト終了 ---")

if __name__ == "__main__":
    run_manual_entry_test()