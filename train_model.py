import os
import sys
import sqlite3
import numpy as np
import joblib
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

# Dosya yolları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trade_logs.db")
MODEL_DIR = os.path.join(BASE_DIR, "models")

FEATURE_NAMES = [
    "rsi", "volatilite", "duyarlilik", "hacim_trend",
    "sma_sinyal", "btc_trendi", "fonlama_orani", "breakout",
    "atr", "atr_spike_oran", "mtf_guc", "fg_index"
]

def prepare_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ HATA: {DB_PATH} bulunamadı!")
        return None, None

    conn = sqlite3.connect(DB_PATH)
    # Backtest verilerini çekiyoruz (LIMIT 100000 yaparak tüm yılı kapsıyoruz)
    #
    query = """
        SELECT i.sembol, i.zaman, i.tip, i.pnl, i.pnl_pct, t.skor, t.volatilite, t.karar
        FROM islem_log i
        JOIN tarama_log t ON i.sembol = t.sembol AND i.zaman = t.zaman
        WHERE i.etiket = 'BACKTEST'
        LIMIT 100000
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print("❌ HATA: Veritabanında eğitim için uygun BACKTEST verisi bulunamadı!")
        return None, None

    X_list = []
    y_list = []

    for _, row in df.iterrows():
        # Feature vektörü oluşturma (Backtest'te kaydedilen temel metrikler)
        #
        features = np.array([
            50.0,                       # RSI (Varsayılan)
            row['volatilite'] or 0.0,   # Gerçek volatilite
            0.0,                        # Duyarlılık
            1.0 if row['volatilite'] > 1.5 else 0.0, # Hacim trendi tahmini
            1.0 if row['skor'] > 0 else -1.0,        # SMA Sinyali
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0       # Diğerleri (Sıfırlanmış)
        ])

        # Etiketleme (Labeling): PNL %0.5 üzerindeyse 1 (Başarılı), değilse 0 (Başarısız)
        label = 1 if row['pnl_pct'] > 0.5 else 0
        
        X_list.append(features)
        y_list.append(label)

    return np.array(X_list), np.array(y_list)

def train():
    X, y = prepare_data()
    if X is None: return

    # Veriyi Eğitim ve Test olarak ayır (%80 - %20)
    #
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print(f"🧠 Model eğitiliyor... (Örnek sayısı: {len(X)})")
    model = XGBClassifier(
        n_estimators=200, # Backtest verisi çok olduğu için ağaç sayısını artırdık
        max_depth=6,
        learning_rate=0.05,
        use_label_encoder=False,
        eval_metric="logloss"
    )
    
    model.fit(X_train, y_train)

    # Başarı testi
    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)
    print(f"✅ Eğitim Tamamlandı! Doğruluk Oranı (Accuracy): %{acc*100:.2f}")

    # Modeli kaydet
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, os.path.join(MODEL_DIR, "xgb_model.joblib"))
    print(f"💾 Yeni beyin kaydedildi: models/xgb_model.joblib")

if __name__ == "__main__":
    import pandas as pd
    train()