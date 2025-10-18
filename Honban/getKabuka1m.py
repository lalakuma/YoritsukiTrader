import yfinance as yf
import pandas as pd
import sys
from datetime import date, timedelta
import time
import sqlite3
from pathlib import Path
import configparser
import os

# --- 設定ファイルの読み込み ---
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_path, encoding='utf-8')

# --- 定数定義 ---
# 銘柄コード (config.iniから取得)
TICKER_CODE = config['TRADE_SETTINGS']['TICKER']
TICKER = f"{TICKER_CODE}.T" # yfinance用に.Tを付加
# データベースのパス
DB_PATH = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")
# テーブル名
TABLE_NAME = f"tbl_{TICKER_CODE}_min"

# 取得したい合計日数 (API制限により最大30日程度)
TOTAL_DAYS_TO_FETCH = 8
# 1回のリクエストで取得する日数 (API制限のため7日以下を推奨)
CHUNK_DAYS = 8
# データ取得間隔 (1分足)
INTERVAL = "1m"

def fetch_stock_data_by_range(ticker: str, start_date: str, end_date: str, interval: str) -> pd.DataFrame | None:
    """指定された期間の株価データをyfinanceから取得し、列名を整形します。"""
    print(f"銘柄 {ticker} の株価データを取得します (期間: {start_date} to {end_date}, 間隔: {interval})...")
    try:
        df = yf.download(ticker, start=start_date, end=end_date, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            print(f"警告: 銘柄 {ticker} の期間 {start_date} to {end_date} のデータは見つかりませんでした。")
            return None
        
        # MultiIndexの列名をシンプルな名前に修正
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        print("データの取得と列名の整形が完了しました。")
        return df
    except Exception as e:
        print(f"エラー: データの取得中に問題が発生しました: {e}", file=sys.stderr)
        return None

def save_data_to_sqlite(df_new_jst: pd.DataFrame, db_path: Path, table_name: str):
    """
    整形済みのデータ(JST)をSQLiteデータベースに保存します。
    既存のデータも読み込んで整形し直し、マージして重複を除去することで、DBをクリーンな状態に更新します。
    """
    print(f"\nデータベース '{db_path.name}' のテーブル '{table_name}' をクリーンアップ・更新します...")
    
    if df_new_jst.empty:
        print("保存する新しいデータがありません。")
        return

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)

        merged_df = df_new_jst

        # 既存のデータを読み込んでクリーンアップする試み
        try:
            df_old = pd.read_sql(f"SELECT * FROM {table_name}", conn, index_col='Datetime', parse_dates=['Datetime'])
            print(f"既存データ{len(df_old)}件を読み込みました。クリーンアップ処理を開始します。")

            # 1. 列名のクリーンアップ
            if isinstance(df_old.columns[0], str) and df_old.columns[0].startswith('('):
                df_old.columns = [eval(c)[0] for c in df_old.columns]

            # 2. タイムゾーンのクリーンアップ（UTCとして解釈し、JSTに変換）
            if df_old.index.tz is None:
                df_old.index = df_old.index.tz_localize('UTC').tz_convert('Asia/Tokyo')
            else:
                df_old.index = df_old.index.tz_convert('Asia/Tokyo')
            
            # 新旧データを結合
            merged_df = pd.concat([df_old, df_new_jst])
            print("既存データと新データを結合しました。")

        except Exception as e:
            print(f"テーブル '{table_name}' は存在しないか、読み込みに失敗しました。新しいデータのみでテーブルを作成します。(エラー: {e})")
            merged_df = df_new_jst

        # 重複を除去（JST基準）
        final_df = merged_df[~merged_df.index.duplicated(keep='last')].copy()
        final_df.sort_index(inplace=True)

        # 最終的なデータをテーブルに書き込む（既存のテーブルは置換）
        final_df.to_sql(table_name, conn, if_exists='replace', index=True, index_label='Datetime')

        print("データベースの更新が完了しました。")
        print(f"テーブル '{table_name}' には現在 {len(final_df)} 件のレコードがあります。")

    except Exception as e:
        print(f"エラー: データベースへの保存中に問題が発生しました: {e}", file=sys.stderr)
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def main():
    """メイン処理"""
    today = date.today()
    # yfinanceの1分足データは直近30日という制限がある。
    # 実行時刻によっては30日前のデータが取得できない場合があるため、安全マージンをとり29日とする
    api_limit_date = today - timedelta(days=29)
    num_chunks = (TOTAL_DAYS_TO_FETCH + CHUNK_DAYS - 1) // CHUNK_DAYS

    print(f"合計{TOTAL_DAYS_TO_FETCH}日分のデータを最大{num_chunks}回に分けて取得します。")
    print(f"(API制限により、{api_limit_date.strftime('%Y-%m-%d')} より前のデータは取得できません)")
    print("-" * 40)

    all_dataframes = []
    for i in range(num_chunks):
        end_date = today - timedelta(days=i * CHUNK_DAYS)
        start_date = end_date - timedelta(days=CHUNK_DAYS)
        
        if end_date < api_limit_date:
            # 取得期間の終わりがAPI制限よりも前になったら、それ以降の取得は不要
            break
        start_date = max(start_date, api_limit_date)
        
        stock_df = fetch_stock_data_by_range(TICKER, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), INTERVAL)
        if stock_df is not None and not stock_df.empty:
            all_dataframes.append(stock_df)
        
        # 次のループで取得対象の期間が残っている場合のみ待機
        if i < num_chunks - 1:
            next_end_date = today - timedelta(days=(i + 1) * CHUNK_DAYS)
            if next_end_date >= api_limit_date:
                print("次のリクエストまで1秒待機します...\n")
                time.sleep(1)

    print("-" * 40)

    if not all_dataframes:
        print("取得できたデータがありませんでした。")
        return

    print("取得したデータを一つに結合します...")
    combined_df = pd.concat(reversed(all_dataframes))
    # yfinanceから取得したデータはUTCなので、まずJSTに変換
    combined_df.index = combined_df.index.tz_convert('Asia/Tokyo')
    print("タイムゾーンをJSTに変換しました。")

    # データベースに保存（この関数内で既存データもクリーンアップされる）
    save_data_to_sqlite(combined_df, DB_PATH, TABLE_NAME)

    print("\n--- 今回取得したデータ ---")
    print(f"合計 {len(combined_df)} 件のデータを取得・処理しました。")
    print(combined_df.head())

if __name__ == "__main__":
    main()
