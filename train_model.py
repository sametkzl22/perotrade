"""
PeroTrade ML Model Eğitim Modülü — V35
========================================
TimeSeriesSplit (n_splits=5) ile zaman-uyumlu cross-validation.
3 sınıflı etiketleme: LONG (1), SHORT (2), BEKLE (0).
StandardScaler ile özellik ölçekleme + scaler kaydı.
Sharpe Oranı ve MDD metrikleri.
"""

import os
import sys
import sqlite3
import numpy as np
import joblib
from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
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
        return None, None, None

    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT i.sembol, i.zaman, i.tip, i.pnl, i.pnl_pct, t.skor, t.volatilite, t.karar
        FROM islem_log i
        JOIN tarama_log t ON i.sembol = t.sembol AND i.zaman = t.zaman
        WHERE i.etiket = 'BACKTEST'
        LIMIT 100000
    """
    import pandas as pd
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print("❌ HATA: Veritabanında eğitim için uygun BACKTEST verisi bulunamadı!")
        return None, None, None

    X_list = []
    y_list = []
    pnl_pct_list = []

    for _, row in df.iterrows():
        features = np.array([
            50.0,                                       # RSI (Varsayılan)
            row['volatilite'] or 0.0,                   # Gerçek volatilite
            0.0,                                        # Duyarlılık
            1.0 if row['volatilite'] > 1.5 else 0.0,   # Hacim trendi tahmini
            1.0 if row['skor'] > 0 else -1.0,          # SMA Sinyali
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0        # Diğerleri (Sıfırlanmış)
        ])

        # V35: 3 Sınıflı Etiketleme
        pnl_pct = row['pnl_pct'] if row['pnl_pct'] is not None else 0.0
        if pnl_pct > 0.5:
            label = 1   # LONG
        elif pnl_pct < -0.5:
            label = 2   # SHORT
        else:
            label = 0   # BEKLE

        X_list.append(features)
        y_list.append(label)
        pnl_pct_list.append(pnl_pct)

    return np.array(X_list), np.array(y_list), np.array(pnl_pct_list)


def _sharpe_orani_hesapla(pnl_pct_array: np.ndarray) -> float:
    """Annualized Sharpe Ratio hesaplar (risk-free rate = 0)."""
    if len(pnl_pct_array) < 2:
        return 0.0
    returns = pnl_pct_array / 100.0
    mean_r = np.mean(returns)
    std_r = np.std(returns)
    if std_r == 0:
        return 0.0
    # Saatlik veri varsayımı → yıllık: sqrt(8760)
    return float((mean_r / std_r) * np.sqrt(8760))


def _max_drawdown_hesapla(pnl_pct_array: np.ndarray) -> float:
    """Yüzde cinsinden Maximum Drawdown hesaplar."""
    if len(pnl_pct_array) < 2:
        return 0.0
    cumulative = np.cumsum(pnl_pct_array)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    return float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0


def train():
    """Tam eğitim akışı: Veri hazırlama → Scaler → TimeSeriesSplit → XGBoost → Metrikler."""
    data = prepare_data()
    if data is None or data[0] is None:
        return

    X, y, pnl_pcts = data

    # V35: StandardScaler ile özellik ölçekleme
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Scaler'ı kaydet
    os.makedirs(MODEL_DIR, exist_ok=True)
    scaler_path = os.path.join(MODEL_DIR, "scaler.joblib")
    joblib.dump(scaler, scaler_path)
    print(f"📏 Scaler kaydedildi: {scaler_path}")

    # V35: TimeSeriesSplit (n_splits=5)
    tscv = TimeSeriesSplit(n_splits=5)
    fold_accuracies = []

    print(f"🧠 Model eğitiliyor... (Örnek sayısı: {len(X)}, Sınıflar: BEKLE=0, LONG=1, SHORT=2)")

    model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        use_label_encoder=False,
        eval_metric="mlogloss",
        objective="multi:softprob",
        num_class=3,
    )

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X_scaled)):
        X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        acc = accuracy_score(y_test, preds)
        fold_accuracies.append(acc)
        print(f"  📊 Fold {fold_idx + 1}/5 — Accuracy: %{acc * 100:.2f}")

    avg_acc = np.mean(fold_accuracies)
    print(f"\n✅ Cross-Validation Tamamlandı! Ortalama Accuracy: %{avg_acc * 100:.2f}")

    # Son fold modeli zaten eğitilmiş durumda — tüm veri ile son eğitim
    model.fit(X_scaled, y)
    print(f"🧠 Final model tüm veri üzerinde eğitildi ({len(X)} örnek)")

    # Classification Report
    final_preds = model.predict(X_scaled)
    print("\n📋 Classification Report (Tüm Veri):")
    target_names = ["BEKLE (0)", "LONG (1)", "SHORT (2)"]
    print(classification_report(y, final_preds, target_names=target_names, zero_division=0))

    # V35: Sharpe Oranı ve MDD Metrikleri
    sharpe = _sharpe_orani_hesapla(pnl_pcts)
    mdd = _max_drawdown_hesapla(pnl_pcts)
    print(f"📈 Sharpe Oranı (Yıllık): {sharpe:.4f}")
    print(f"📉 Maximum Drawdown: %{mdd:.2f}")

    # Modeli kaydet
    model_path = os.path.join(MODEL_DIR, "xgb_model.joblib")
    joblib.dump(model, model_path)
    print(f"💾 Yeni beyin kaydedildi: {model_path}")

    return {
        "accuracy": avg_acc * 100,
        "sharpe": sharpe,
        "mdd": mdd,
        "sinif_dagilimi": {
            "BEKLE": int(np.sum(y == 0)),
            "LONG": int(np.sum(y == 1)),
            "SHORT": int(np.sum(y == 2)),
        }
    }


def run_training() -> dict:
    """bot_worker.py korelasyon_rutini tarafından çağrılan sarmalayıcı.
    Dict döner: {"basarili": bool, "neden": str, "detay": {...}}
    """
    try:
        sonuc = train()
        if sonuc is None:
            return {"basarili": False, "neden": "Eğitim verisi bulunamadı", "detay": {}}
        return {
            "basarili": True,
            "neden": "OK",
            "detay": {
                "egitim": {
                    "accuracy": sonuc.get("accuracy", 0),
                    "sharpe": sonuc.get("sharpe", 0),
                    "mdd": sonuc.get("mdd", 0),
                }
            }
        }
    except Exception as e:
        return {"basarili": False, "neden": str(e)[:120], "detay": {}}


if __name__ == "__main__":
    import pandas as pd
    train()