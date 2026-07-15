#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_data.py
================
由 GitHub Actions 每日排程執行的資料更新腳本。

流程：
  1. 向台灣證交所（TWSE）OpenAPI 取得上市證券每日行情，篩選出代號屬於 ETF 編碼規則
     （00 開頭）的標的，取得「即時 ETF 清單」。若連線失敗，退回內建的備援清單，
     確保 data.json 一定會被產生（避免前端因缺檔而整頁失敗）。
  2. 透過 yfinance 逐檔下載每一檔 ETF 過去約 2 年的日收盤價，並下載台灣加權指數
     （^TWII）作為大盤基準。
  3. 將所有序列對齊到同一組交易日期（以 ^TWII 的交易日曆為主），缺值以前值填補，
     產生單一靜態檔案 data.json，提交回儲存庫供 GitHub Pages 的純前端頁面讀取。

此腳本刻意避免依賴任何伺服器端執行環境（不需要資料庫、不需要密鑰），
只要 GitHub Actions 的 runner 能連上網路即可運作。
"""

import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------
TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
ETF_CODE_PATTERN = re.compile(r"^00\d{2,4}[A-Z]?$")  # 與前端 JS 的篩選規則一致

# TWSE OpenAPI 若連線失敗時的備援清單（與前端 index.html 中的 FALLBACK_ETFS 對齊）
FALLBACK_ETFS = [
    ("0050.TW", "元大台灣50"), ("0056.TW", "元大高股息"), ("00878.TW", "國泰永續高股息"),
    ("00692.TW", "富邦公司治理"), ("006208.TW", "富邦台50"), ("00713.TW", "元大台灣高息低波"),
    ("00919.TW", "群益台灣精選高息"), ("00929.TW", "復華台灣科技優息"), ("00646.TW", "元大S&P500"),
    ("00830.TW", "國泰費城半導體"), ("00940.TW", "元大台灣價值高息"), ("00895.TW", "富邦特選高股息30"),
    ("00888.TW", "永豐台灣ESG"), ("00757.TW", "統一FANG+"), ("00733.TW", "富邦臺灣半導體"),
    ("00893.TW", "國泰智能電動車"), ("00631L.TW", "元大台灣50正2"), ("00881.TW", "國泰台灣5G+"),
    ("00735.TW", "國泰網路資安"),
]

HISTORY_PERIOD = "2y"      # yfinance 下載區間
REQUEST_TIMEOUT = 15       # 單一 TWSE API 請求逾時秒數
DOWNLOAD_RETRIES = 3       # 單一 ticker 下載重試次數
RETRY_SLEEP_SEC = 2        # 重試間隔秒數
BETWEEN_TICKER_SLEEP_SEC = 0.3  # 每檔之間的節流間隔，降低被 Yahoo 限流的機率
MAX_TICKERS = 250          # 安全上限，避免 ETF 清單異常暴增時執行時間失控（目前台灣 00 開頭 ETF 約 200 餘檔）

OUTPUT_PATH = "data.json"


def log(msg: str) -> None:
    print(f"[update_data] {msg}", flush=True)


# ------------------------------------------------------------------
# 步驟 1：取得 ETF 清單
# ------------------------------------------------------------------
def fetch_twse_etf_list():
    """回傳 [(ticker, name), ...]；ticker 格式為 '0050.TW'。失敗時回傳備援清單。"""
    try:
        resp = requests.get(TWSE_STOCK_DAY_ALL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            raise ValueError("TWSE STOCK_DAY_ALL 回傳空白或非預期格式")

        etfs = []
        seen = set()
        for item in data:
            code = str(item.get("Code", "")).strip()
            name = str(item.get("Name", code)).strip()
            if not code or not ETF_CODE_PATTERN.match(code):
                continue
            ticker = f"{code}.TW"
            if ticker in seen:
                continue
            seen.add(ticker)
            etfs.append((ticker, name))

        if not etfs:
            raise ValueError("篩選後沒有任何符合 ETF 代號規則的標的")

        etfs.sort(key=lambda x: x[0])
        log(f"TWSE ETF 清單擷取成功，共 {len(etfs)} 檔")
        return etfs
    except Exception as e:  # noqa: BLE001 - 對外層而言任何失敗都應該優雅退回備援清單
        log(f"⚠️ TWSE ETF 清單擷取失敗（{e}），改用內建備援清單（{len(FALLBACK_ETFS)} 檔）")
        return list(FALLBACK_ETFS)


# ------------------------------------------------------------------
# 步驟 2：以 yfinance 下載歷史收盤價
# ------------------------------------------------------------------
def download_close_series(ticker: str):
    """下載單一 ticker 的收盤價序列（pandas.Series，index 為日期），失敗回傳 None。"""
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD, interval="1d", auto_adjust=True)
            if hist is None or hist.empty or "Close" not in hist.columns:
                raise ValueError("yfinance 回傳空資料")
            series = hist["Close"].dropna()
            if series.empty:
                raise ValueError("收盤價序列全為缺值")
            # 只保留日期（去除時區與時間資訊），避免與交易日曆比對時因時區不一致而錯位
            series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
            return series
        except Exception as e:  # noqa: BLE001
            if attempt < DOWNLOAD_RETRIES:
                log(f"  重試 {ticker}（第 {attempt} 次失敗：{e}）")
                time.sleep(RETRY_SLEEP_SEC)
            else:
                log(f"  ⚠️ 放棄 {ticker}：{e}")
                return None
    return None


def build_dataset(etf_list):
    if len(etf_list) > MAX_TICKERS:
        log(f"清單長度 {len(etf_list)} 超過上限 {MAX_TICKERS}，僅取前 {MAX_TICKERS} 檔")
        etf_list = etf_list[:MAX_TICKERS]

    log("下載台灣加權指數 ^TWII 作為大盤基準…")
    twii_series = download_close_series("^TWII")
    if twii_series is None:
        log("⚠️ ^TWII 下載失敗，將以所有成功下載之 ETF 的聯集交易日作為日期基準")

    name_map = dict(etf_list)
    price_series = {}
    failed = []

    for i, (ticker, _name) in enumerate(etf_list, start=1):
        log(f"[{i}/{len(etf_list)}] 下載 {ticker} ({name_map.get(ticker, ticker)}) …")
        s = download_close_series(ticker)
        if s is not None:
            price_series[ticker] = s
        else:
            failed.append(ticker)
        time.sleep(BETWEEN_TICKER_SLEEP_SEC)

    if not price_series:
        raise RuntimeError("所有 ETF 皆下載失敗，無法產生 data.json")

    # 決定共同的交易日期索引：優先採用 ^TWII 的交易日曆，否則採用所有序列的聯集後再取交集範圍
    if twii_series is not None:
        date_index = twii_series.index
    else:
        date_index = sorted(set().union(*[set(s.index) for s in price_series.values()]))
        date_index = pd.DatetimeIndex(date_index)

    date_index = date_index.sort_values()

    aligned_prices = {}
    for ticker, s in price_series.items():
        # 對齊到共同日期索引：先 reindex，缺值以前一筆有效值遞補（ffill），
        # 序列開頭若仍缺值（表示該 ETF 上市時間晚於基準起始日）則以該檔最早的有效值回補（bfill），
        # 避免影響其餘已對齊資料，同時確保輸出陣列不含 null。
        reindexed = s.reindex(date_index).ffill().bfill()
        if reindexed.isna().any():
            log(f"  ⚠️ {ticker} 對齊後仍有缺值，予以剔除")
            continue
        aligned_prices[ticker] = [round(float(v), 4) for v in reindexed.tolist()]

    if not aligned_prices:
        raise RuntimeError("對齊交易日期後沒有任何可用的 ETF 序列")

    twii_out = None
    if twii_series is not None:
        twii_aligned = twii_series.reindex(date_index).ffill().bfill()
        if not twii_aligned.isna().any():
            twii_out = [round(float(v), 4) for v in twii_aligned.tolist()]

    metadata = [
        {"ticker": t, "name": name_map.get(t, t)}
        for t in aligned_prices.keys()
    ]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dates": [d.strftime("%Y-%m-%d") for d in date_index],
        "prices": aligned_prices,
        "twii": twii_out,
        "metadata": metadata,
        "failed_tickers": failed,
    }

    log(f"完成：{len(aligned_prices)} 檔成功、{len(failed)} 檔失敗、共 {len(date_index)} 個交易日")
    if failed:
        log(f"失敗清單：{', '.join(failed)}")

    return output


def main():
    log("=== 開始更新台灣 ETF 資料 ===")
    etf_list = fetch_twse_etf_list()
    dataset = build_dataset(etf_list)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, separators=(",", ":"))

    log(f"已寫入 {OUTPUT_PATH}")
    log("=== 更新完成 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - 讓 GitHub Actions 明確顯示失敗原因並以非 0 結束
        traceback.print_exc()
        sys.exit(1)
