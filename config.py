# ─────────────────────────────────────────────
# PeroTrade Pro — Konfigürasyon v5
# 7/24 Bileşik Faiz Sistemi + Binance API
# ─────────────────────────────────────────────

# ───── Binance API (Gerçek İşlem İçin) ─────
# Paper trading modunda boş bırakabilirsiniz.
API_KEY = ""
SECRET_KEY = ""

# ───── Exchange ─────
EXCHANGE_NAME = "binance"
USE_REAL_API = False  # True = gerçek Binance Futures, False = paper trade

# ───── Paper-Trade Bakiye ─────
INITIAL_BALANCE = 10.0
TARGET_BALANCE = 100.0

# ───── Bileşik Faiz Hedefi ─────
DAILY_TARGET_PCT = 10.0   # Günlük %10 kâr hedefi
COMPOUNDING = True         # Her günün kârı ertesi günün bakiyesine eklenir

# ───── Risk Limitleri ─────
DAILY_PROFIT_LOCK = 500.0  # %500 kâra ulaşınca -> Güvenli Mod (eski)
PROFIT_LOCK_RATIO = 0.8    # Kazanılan %10 hedefin %80'i kilitlenir
DAILY_LOSS_STOP = -7.5     # %7.5 kayıpta -> Recovery Mode (Kurtarma Modu)
RECOVERY_CONFIDENCE_THRESHOLD = 90
MAX_CONCURRENT_TRADES = 99 # Aynı anda açılabilecek maksimum işlem sayısı
MAX_RISK_PER_TRADE = 1.0   # Normal modda Max %100 bakiye kullanımı

# ───── Haber & Makro Filtre ─────
ENABLE_NEWS_VETO = True    # False = haberleri yoksay, sadece teknik skor ile işlem yap

# ───── Analiz Ayarları ─────
SHORT_MA = 7
LONG_MA = 25
TIMEFRAME = "1h"

# ───── Persistent State ─────
STATE_FILE = "persistent_state.json"
TRADE_LOG_FILE = "trade_history.csv"

# ───── Headless Bot (bot.py) ─────
HEADLESS_CHECK_INTERVAL = 60  # Saniye (eski PC için 60s yeterli)
HEADLESS_COIN_SCAN_LIMIT = 50  # CPU dostu: 50 coin tara (100 yerine)

# ───── ML Engine (v9) ─────
ML_MODEL_PATH = "models/xgb_model.joblib"
ML_RETRAIN_INTERVAL_HOURS = 24    # Her 24 saatte bir model yeniden eğitilir
ML_MIN_TRAINING_SAMPLES = 30     # Minimum eğitim örneği sayısı

# ───── 94-Day Challenge ─────
CHALLENGE_INITIAL_BALANCE = 10.0
CHALLENGE_TARGET_BALANCE = 100000.0
CHALLENGE_TOTAL_DAYS = 94
CHALLENGE_DAILY_TARGET_PCT = 10.0   # Günlük %10 hedef
CHALLENGE_MAX_LEVERAGE = 50         # Cross 20x-50x arası
CHALLENGE_MIN_LEVERAGE = 20
CHALLENGE_RISK_PER_TRADE = 0.20     # İşlem başına kasanın %20'si
CHALLENGE_TRAILING_STOP_ACTIVATE = 10.0   # %10'da trailing stop aktifleşir
CHALLENGE_TRAILING_STOP_STEP = 2.0        # Her %2 artışta stop %2 yukarı kayar
CHALLENGE_COMMISSION_RATE = 0.001         # %0.1 Binance standart komisyon (giriş + çıkış)
