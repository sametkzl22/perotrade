import pandas as pd
import glob
import os
import sqlite3
import sys
from tqdm import tqdm
from datetime import datetime, timezone
import numpy as np

# Kendi yazdığın motorları içe aktar
import ai_engine
import config as cfg

# --- YOL AYARLARI ---
# train_model.py ile aynı mantığı kullanıyoruz
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trade_logs.db")

def init_db():
    """Veritabanını ve tabloları sıfırdan oluşturur."""
    print(f"📁 Veritabanı oluşturuluyor: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS tarama_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zaman TEXT NOT NULL, sembol TEXT NOT NULL, fiyat REAL, skor REAL,
            atr REAL, volatilite REAL, hacim_artis REAL, breakout INTEGER DEFAULT 0,
            mtf_konsensus TEXT, karar TEXT
        );
        CREATE TABLE IF NOT EXISTS islem_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zaman TEXT NOT NULL, sembol TEXT NOT NULL, tip TEXT,
            giris_fiyati REAL, cikis_fiyati REAL, pnl REAL, pnl_pct REAL,
            kaldirac INTEGER, margin REAL, neden TEXT, etiket TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    print("✅ Tablolar hazır.")

def run_simulation():
    print(f"🚀 Script başlatıldı. Çalışma dizini: {BASE_DIR}")
    init_db()
    
    # CSV dosyalarını ara
    search_pattern = os.path.join(BASE_DIR, "data", "history", "**", "*.csv")
    files = glob.glob(search_pattern, recursive=True)
    
    print(f"🔍 Aranan konum: {search_pattern}")
    print(f"📂 Bulunan CSV dosya sayısı: {len(files)}")
    
    if not files:
        print("❌ HATA: İşlenecek veri bulunamadı! 'data/history' klasörünün dolu olduğundan emin ol.")
        return

    conn = sqlite3.connect(DB_PATH)
    # İnternet kesintilerine karşı botu koruyan offline haber verisi
    mock_haberler = ["Offline Backtest Mode", "Market is neutral"]

    for file_path in files:
        file_name = os.path.basename(file_path)
        raw_symbol = file_name.split("-")[0]
        symbol = f"{raw_symbol[:-4]}/{raw_symbol[-4:]}" # USDT formatı
        
        print(f"\n📊 {symbol} verisi yükleniyor...")
        
        try:
            # Header temizliği ve okuma
            df_temp = pd.read_csv(file_path, nrows=5)
            has_header = "open_time" in df_temp.columns or "timestamp" in df_temp.columns
            df = pd.read_csv(file_path) if has_header else pd.read_csv(file_path, header=None)
            
            df = df.iloc[:, :6]
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df = df[pd.to_numeric(df['timestamp'], errors='coerce').notnull()].copy()
            df["timestamp"] = pd.to_numeric(df["timestamp"])
            df["time_dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            
            # Backtest Döngüsü
            for i in tqdm(range(100, len(df) - 10), desc=f"İşleniyor: {symbol}"):
                window = df.iloc[i-100:i].copy()
                window["timestamp"] = window["time_dt"]
                
                # ai_engine ile analiz
                pazar = ai_engine.pazar_durumu_cikar(window, symbol, pre_fetched_news=mock_haberler)
                if not pazar: continue
                
                sinyal = ai_engine.sinyal_uret(window, 7, 25)
                skor = ai_engine.kompozit_skor_hesapla(pazar, sinyal)
                zaman_str = window["time_dt"].iloc[-1].isoformat()
                
                # Veritabanına kaydet
                conn.execute("""
                    INSERT INTO tarama_log (zaman, sembol, fiyat, skor, volatilite, karar)
                    VALUES (?,?,?,?,?,?)
                """, (zaman_str, symbol, pazar["fiyat"], skor, pazar["volatilite"], sinyal))

                if sinyal in ["AL", "SAT"]:
                    entry = float(df.iloc[i]["open"])
                    exit = float(df.iloc[i+4]["close"]) # 1 saat sonra çıkış simülasyonu
                    pnl_pct = ((exit - entry) / entry) * 100 if sinyal == "AL" else ((entry - exit) / entry) * 100
                    
                    conn.execute("""
                        INSERT INTO islem_log (zaman, sembol, tip, giris_fiyati, cikis_fiyati, pnl, pnl_pct, etiket)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (zaman_str, symbol, "LONG" if sinyal=="AL" else "SHORT", entry, exit, pnl_pct*10, pnl_pct, "BACKTEST"))
            
            conn.commit() # Her coin bittiğinde mühürle
        except Exception as e:
            print(f"⚠️ {file_name} işlenirken hata oluştu: {e}")
            continue

    conn.close()
    print("\n✅ Tamamlandı! Artık 'python3 train_model.py' komutuna geçebilirsin.")

if __name__ == "__main__":
    run_simulation()