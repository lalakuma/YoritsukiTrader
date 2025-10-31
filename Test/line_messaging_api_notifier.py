import requests
from datetime import datetime

import configparser
import os

config = configparser.ConfigParser()
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')
config.read(config_path, encoding='utf-8')

# LINE Messaging API の設定をconfig.iniから読み込む
CHANNEL_ACCESS_TOKEN = config['LINE_MESSAGING_API']['CHANNEL_ACCESS_TOKEN']
USER_IDS = config['LINE_MESSAGING_API']['USER_IDS'].split(',')


def line_notify(lst_codes, stance, logger=None):
    # 設定ファイルから通知の有効/無効を読み込む
    notifications_enabled = config.getboolean('LINE_MESSAGING_API', 'NOTIFICATIONS_ENABLED', fallback=True)
    if not notifications_enabled:
        print("LINE通知は無効です。")
        return

    # 土曜日（7）と日曜日（6）は通知を送らない
    iWeek = datetime.today().isoweekday()
    if iWeek in [6, 7]:  
        print("今日は通知を送りません")
        return  

    # 送信するメッセージ作成

    message = "\n".join(lst_codes) if lst_codes else "not found"
    full_message = f"{stance} {message}"

    # LINE Messaging API のエンドポイント
    url = "https://api.line.me/v2/bot/message/multicast"

    # ヘッダー情報
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 送信するデータ
    data = {
        "to": USER_IDS,
        "messages": [{"type": "text", "text": full_message}]
    }

    # APIリクエストを送信
    response = requests.post(url, headers=headers, json=data)

    # 結果を確認
    if response.status_code == 200:
        print("メッセージ送信成功！")
    else:
        print(f"エラー発生: {response.status_code} - {response.text}")



