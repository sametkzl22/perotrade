"""
Train Model — Otonom ML Eğitim Pipeline
========================================
trade_logs.db'deki tarama_log ve islem_log tablolarını
birleştirerek XGBoost modeli eğitir ve kaydeder.
"""

import os
import sys
import sqlite3
import numpy as np
from datetime import datetime, timezone

try:
    import joblib
except ImportError:
    joblib = None

try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None

try:
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, classification_report
except ImportError:
    train_test_split = None
    accuracy_score = None
    classification_report = None


def _get_db_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "trade_logs.db")


def _get_model_dir() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    model_dir = os.path.join(base, "models")
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


FEATURE_NAMES = [
    "rsi", "volatilite", "duyarlilik", "hacim_trend",
    "sma_sinyal", "btc_trendi", "fonlama_orani", "breakout",
    "atr", "atr_spike_oran", "mtf_guc", "fg_index"
]


def prepare_training_data(min_samples: int = 30) -> dict:
    """
    islem_log + tarama_log JOIN ile eğitim verisi hazırlar.
    
    Label:
        - pnl > 0 ve tip == 'LONG'  → 1 (LONG)
        - pnl > 0 ve tip == 'SHORT' → 2 (SHORT)
        - pnl <= 0                  → 0 (BEKLE — zararlı kararları öğrenme)
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"basarili": False, "neden": "trade_logs.db bulunamadı", "X": None, "y": None}

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")

        # İşlemleri çek
        islemler = conn.execute("""
            SELECT i.sembol, i.zaman, i.tip, i.pnl, i.pnl_pct, i.kaldirac, i.margin
            FROM islem_log i
            ORDER BY i.id DESC
            LIMIT 1000
        """).fetchall()

        if len(islemler) < min_samples:
            conn.close()
            return {
                "basarili": False,
                "neden": f"Yetersiz veri: {len(islemler)} işlem (min: {min_samples})",
                "X": None, "y": None
            }

        X_list = []
        y_list = []

        for sembol, zaman, tip, pnl, pnl_pct, kaldirac, margin in islemler:
            # İşlem zamanının yakınındaki tarama kaydını bul
            tarama = conn.execute("""
                SELECT skor, atr, volatilite, hacim_artis, breakout, mtf_konsensus
                FROM tarama_log
                WHERE sembol = ? AND zaman <= ?
                ORDER BY id DESC LIMIT 1
            """, (sembol, zaman)).fetchone()

            if tarama is None:
                continue

            skor, atr, volatilite, hacim_artis, breakout, mtf_konsensus = tarama

            # Feature vektörü oluştur (basitleştirilmiş — tarama verisinden)
            mtf_guc_val = 0.0
            if mtf_konsensus:
                mtf_map = {"GÜÇLÜ AL": 2, "ZAYIF AL": 1, "KARARSIZ": 0, "ZAYIF SAT": -1, "GÜÇLÜ SAT": -2}
                mtf_guc_val = float(mtf_map.get(mtf_konsensus, 0))

            features = np.array([
                50.0,                           # RSI (tarama'dan yok, ortalama)
                volatilite or 0.0,              # Volatilite
                0.0,                            # Duyarlılık (tarama'dan yok)
                1.0 if hacim_artis and hacim_artis > 100 else 0.0,  # Hacim trendi
                1.0 if skor and skor > 0 else (-1.0 if skor and skor < 0 else 0.0),  # SMA sinyali (skor proxy)
                0.0,                            # BTC trendi (tarama'dan yok)
                0.0,                            # Fonlama oranı (tarama'dan yok)
                float(breakout or 0),           # Breakout
                atr or 0.0,                     # ATR
                0.0,                            # ATR Spike oranı (tarama'dan yok)
                mtf_guc_val,                    # MTF gücü
                50.0                            # Fear & Greed (tarama'dan yok)
            ], dtype=np.float64)

            # Label oluştur
            if pnl is not None and pnl > 0:
                if tip == "LONG":
                    label = 1
                elif tip == "SHORT":
                    label = 2
                else:
                    label = 0
            else:
                label = 0  # Zararlı → BEKLE olarak öğret

            X_list.append(features)
            y_list.append(label)

        conn.close()

        if len(X_list) < min_samples:
            return {
                "basarili": False,
                "neden": f"Eşleşen veri yetersiz: {len(X_list)} örnek (min: {min_samples})",
                "X": None, "y": None
            }

        X = np.array(X_list)
        y = np.array(y_list)

        return {
            "basarili": True,
            "neden": f"{len(X)} örnek hazırlandı",
            "X": X,
            "y": y,
            "label_dagilim": {
                "BEKLE(0)": int(np.sum(y == 0)),
                "LONG(1)": int(np.sum(y == 1)),
                "SHORT(2)": int(np.sum(y == 2))
            }
        }
    except Exception as e:
        return {"basarili": False, "neden": f"Veri hazırlama hatası: {e}", "X": None, "y": None}


def train_xgboost_model(X: np.ndarray, y: np.ndarray) -> dict:
    """XGBClassifier eğitir ve model + metrikleri döner."""
    if XGBClassifier is None:
        return {"basarili": False, "neden": "xgboost kurulu değil (pip install xgboost)"}
    if train_test_split is None:
        return {"basarili": False, "neden": "scikit-learn kurulu değil (pip install scikit-learn)"}

    try:
        # Sınıf ağırlıkları hesapla (dengesiz veri için)
        siniflar, sayilar = np.unique(y, return_counts=True)
        toplam = len(y)
        agirliklar = {int(s): toplam / (len(siniflar) * c) for s, c in zip(siniflar, sayilar)}
        sample_weights = np.array([agirliklar[int(label)] for label in y])

        # Train/Test ayır
        X_train, X_test, y_train, y_test, w_train, _ = train_test_split(
            X, y, sample_weights, test_size=0.2, random_state=42, stratify=y
        )

        model = XGBClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            use_label_encoder=False,
            eval_metric="mlogloss",
            random_state=42,
            verbosity=0
        )
        model.fit(X_train, y_train, sample_weight=w_train)

        # Değerlendirme
        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)

        # Feature importance
        importances = model.feature_importances_
        top_features = sorted(
            zip(FEATURE_NAMES, importances),
            key=lambda x: x[1], reverse=True
        )[:5]

        return {
            "basarili": True,
            "model": model,
            "accuracy": round(acc * 100, 1),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "top_features": top_features
        }
    except Exception as e:
        return {"basarili": False, "neden": f"Eğitim hatası: {e}"}


def save_model(model, path: str = None) -> bool:
    """Modeli joblib ile diske kaydeder."""
    if joblib is None:
        print("⚠️ joblib kurulu değil (pip install joblib)")
        return False
    try:
        if path is None:
            path = os.path.join(_get_model_dir(), "xgb_model.joblib")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(model, path)
        return True
    except Exception as e:
        print(f"⚠️ Model kaydetme hatası: {e}")
        return False


def run_training(min_samples: int = None) -> dict:
    """
    Tam eğitim pipeline'ını çalıştırır:
    1. Veri hazırla (tarama_log + islem_log JOIN)
    2. XGBoost eğit
    3. Model kaydet
    """
    if min_samples is None:
        try:
            import config as cfg
            min_samples = getattr(cfg, "ML_MIN_TRAINING_SAMPLES", 30)
        except ImportError:
            min_samples = 30

    result = {"basarili": False, "neden": "", "detay": {}}

    # 1. Veri hazırla
    data = prepare_training_data(min_samples)
    if not data["basarili"]:
        result["neden"] = data["neden"]
        return result

    result["detay"]["veri"] = {
        "orneklem": len(data["X"]),
        "label_dagilim": data.get("label_dagilim", {})
    }

    # 2. Eğit
    train_result = train_xgboost_model(data["X"], data["y"])
    if not train_result["basarili"]:
        result["neden"] = train_result["neden"]
        return result

    result["detay"]["egitim"] = {
        "accuracy": train_result["accuracy"],
        "train_size": train_result["train_size"],
        "test_size": train_result["test_size"],
        "top_features": [(f, round(float(s), 3)) for f, s in train_result["top_features"]]
    }

    # 3. Kaydet
    model_path = os.path.join(_get_model_dir(), "xgb_model.joblib")
    saved = save_model(train_result["model"], model_path)
    if not saved:
        result["neden"] = "Model dosyası kaydedilemedi"
        return result

    result["basarili"] = True
    result["neden"] = f"✅ Model eğitildi: Accuracy %{train_result['accuracy']:.1f}, {len(data['X'])} örnek"
    result["model_path"] = model_path

    return result


if __name__ == "__main__":
    print("🧠 PeroTrade ML Training Pipeline")
    print("=" * 40)
    try:
        sonuc = run_training()
        if sonuc["basarili"]:
            print(f"✅ {sonuc['neden']}")
            print(f"   Model: {sonuc.get('model_path', '?')}")
            detay = sonuc.get("detay", {})
            if "egitim" in detay:
                print(f"   Top Features: {detay['egitim'].get('top_features', [])}")
        else:
            neden = sonuc.get("neden", "Bilinmeyen hata")
            if "Yetersiz" in neden or "bulunamadı" in neden or "Eşleşen" in neden:
                print(f"⚠️ Yeterli eğitim verisi henüz toplanmadı.")
                print(f"   Detay: {neden}")
                print(f"   Bot işlem yaptıkça trade_logs.db'ye veri birikecek ve model eğitilebilir hale gelecektir.")
            else:
                print(f"❌ Eğitim başarısız: {neden}")
    except Exception as e:
        print(f"⚠️ Yeterli eğitim verisi henüz toplanmadı veya beklenmeyen bir hata oluştu.")
        print(f"   Hata: {e}")
