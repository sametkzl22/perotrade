# ─────────────────────────────────────────────
# PeroTrade Pro — Konfigürasyon v23
# 7/24 Bileşik Faiz Sistemi + Binance Futures
# ─────────────────────────────────────────────

# ───── Binance API (Gerçek İşlem İçin) ─────
# Paper trading modunda boş bırakabilirsiniz.
API_KEY = ""
SECRET_KEY = ""

# ───── Exchange ─────
EXCHANGE_NAME = "binance"
USE_REAL_API = False  # True = gerçek Binance Futures, False = paper trade

# ───── Futures (v11) ─────
FUTURES_TYPE = "future"                # ccxt defaultType: spot → future
FUTURES_SYMBOL_SUFFIX = ":USDT"        # Perpetual sembol uzantısı (BTC/USDT:USDT)

# ───── Kaynak Yönetimi (v11) ─────
COOLING_SLEEP_SECONDS = 10             # Her analiz döngüsü sonunda zorunlu dinlenme (sn)
GC_COLLECT_INTERVAL = 100              # Her N döngüde gc.collect() çağır

# ───── Paper-Trade Bakiye ─────
INITIAL_BALANCE = 100000.0
TARGET_BALANCE = 1000000.0

# ───── Bileşik Faiz Hedefi ─────
DAILY_TARGET_PCT = 10.0   # Günlük %10 kâr hedefi
COMPOUNDING = True         # Her günün kârı ertesi günün bakiyesine eklenir

# ───── Risk Limitleri ─────
DAILY_PROFIT_LOCK = 500.0  # %500 kâra ulaşınca -> Güvenli Mod (eski)
PROFIT_LOCK_RATIO = 0.8    # Kazanılan %10 hedefin %80'i kilitlenir
DAILY_LOSS_STOP = -7.5     # %7.5 kayıpta -> Recovery Mode (Kurtarma Modu)
RECOVERY_CONFIDENCE_THRESHOLD = 90
MAX_CONCURRENT_TRADES = 99 # Aynı anda açılabilecek maksimum işlem sayısı

# V19 Gelişmiş Risk Yönetimi Mimarisi
MAX_WALLET_RISK_PCT = 100.0
TRADE_RISK_PCT = 10.0

# ───── Haber & Makro Filtre ─────
ENABLE_NEWS_VETO = True    # False = haberleri yoksay, sadece teknik skor ile işlem yap

# ───── Analiz Ayarları ─────
SHORT_MA = 7
LONG_MA = 25
TIMEFRAME = "1h"

# ───── Persistent State ─────
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

# ───── Evolutionary Trainer ─────
EVO_REWARD_POINTS = 10          # Kârlı işlem ödülü
EVO_PENALTY_POINTS = -15        # Zararlı işlem cezası
EVO_WAIT_MULTIPLIER = 0.30      # Bekleme süresi çarpanı (%70 azalma)
EVO_LOSS_STOP_OVERRIDE = -50.0  # Gevşetilmiş zarar durdurma (sadece EVO mod)
EVO_MIN_SCORE_THRESHOLD = 10    # Sinyal eşiği (düşük = daha fazla işlem)

# ───── Telegram Entegrasyonu (V23) — .env'den okunur ─────
import os as _os
from dotenv import load_dotenv as _load_dotenv

_load_dotenv(dotenv_path=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env"))

TELEGRAM_TOKEN = _os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _os.getenv("TELEGRAM_CHAT_ID", "")
