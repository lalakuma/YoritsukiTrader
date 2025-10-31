import os
import sys
import configparser
import logging

# Honbanディレクトリをパスに追加してKabuAPIをインポート
honban_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Honban'))
sys.path.append(honban_dir)

try:
    from kabu_api import KabuAPI
except ImportError:
    print(f"Error: kabu_api.pyが見つかりません。Honbanフォルダを確認してください。")
    sys.exit(1)

# --- ロガー設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TradeTestMenu")

def get_api_instance():
    """設定を読み込み、KabuAPIのインスタンスを返す"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Testフォルダ内にあるconfig.iniを優先して使用
    config_path = os.path.join(script_dir, 'config.ini')

    if not os.path.exists(config_path):
        logger.error(f"設定ファイルが見つかりません: {config_path}")
        return None

    api = KabuAPI(config_path=config_path, logger=logger)
    
    logger.info("APIトークンの取得を試みます...")
    if not api.get_token():
        logger.error("APIトークンの取得に失敗しました。")
        return None
    logger.info("APIトークンの取得に成功しました。")
    return api

def send_cash_buy_order(api: KabuAPI):
    """現物買い注文（寄成）を送信する"""
    try:
        ticker = "4751"
        exchange = 1  # 1: 東証
        qty = 100
        logger.info(f"銘柄: {ticker}, 取引所: {exchange}, 株数: {qty} で現物買い（寄成）注文を送信します。")

        success, order_info = api.send_market_order(
            ticker=ticker,
            exchange=exchange,
            qty=qty,
            side='2',  # 2: 買い
            front_order_type=13  # 13: 寄成（前場）
        )

        if success:
            logger.info("--- 買い注文 送信成功 ---")
            logger.info(f"注文レスポンス: {order_info}")
        else:
            logger.error("--- 買い注文 送信失敗 ---")
            logger.error(f"エラー情報: {order_info}")

    except Exception as e:
        logger.error(f"注文処理中に予期せぬエラーが発生しました: {e}")

def send_cash_sell_order(api: KabuAPI):
    """現物売り注文（寄成）を送信する"""
    try:
        ticker = "4751"
        exchange = 1  # 1: 東証
        qty = 100
        logger.info(f"銘柄: {ticker}, 取引所: {exchange}, 株数: {qty} で現物売り（寄成）注文を送信します。")

        success, order_info = api.send_market_order(
            ticker=ticker,
            exchange=exchange,
            qty=qty,
            side='1',  # 1: 売り
            front_order_type=13  # 13: 寄成（前場）
        )

        if success:
            logger.info("--- 売り注文 送信成功 ---")
            logger.info(f"注文レスポンス: {order_info}")
        else:
            logger.error("--- 売り注文 送信失敗 ---")
            logger.error(f"エラー情報: {order_info}")

    except Exception as e:
        logger.error(f"注文処理中に予期せぬエラーが発生しました: {e}")

def main():
    """メニューを表示し、ユーザーの選択に応じて処理を実行する"""
    api = get_api_instance()
    if not api:
        logger.error("APIの初期化に失敗しました。プログラムを終了します。")
        return

    while True:
        print("\n--- 取引テストメニュー ---")
        print("1: 現物買い (寄成)")
        print("2: 現物売り (寄成)")
        print("q: 終了")
        choice = input("選択してください: ").lower()

        if choice == '1':
            send_cash_buy_order(api)
        elif choice == '2':
            send_cash_sell_order(api)
        elif choice == 'q':
            logger.info("プログラムを終了します。")
            break
        else:
            print("無効な選択です。もう一度入力してください。")

if __name__ == "__main__":
    main()