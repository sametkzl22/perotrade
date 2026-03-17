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
DAILY_PROFIT_LOCK = 500.0  # %500 kâra ulaşınca -> Güvenli Mod (işlem durur)
DAILY_LOSS_STOP = -5.0     # %5 kayıpta -> Panik Koruması (işlem durur)
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
