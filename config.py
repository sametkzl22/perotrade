# ─────────────────────────────────────────────
# Kripto Paper-Trading Bot — Konfigürasyon
# ─────────────────────────────────────────────

# Exchange ayarları (binance / gateio)
EXCHANGE_NAME = "binance"

# İşlem çifti
SYMBOL = "BTC/USDT"

# Mum periyodu
TIMEFRAME = "1h"

# Hareketli ortalama periyotları
SHORT_MA = 7
LONG_MA = 25

# Paper-trade bakiye ayarları (USDT)
INITIAL_BALANCE = 10.0
TARGET_BALANCE = 100.0

# Risk oranı — bakiyenin yüzdesi (1.0 = %100)
RISK_RATIO = 1.0

# Ana döngü bekleme süresi (saniye)
CHECK_INTERVAL = 300  # 5 dakika

# İşlem geçmişi dosyası
TRADE_LOG_FILE = "trade_history.csv"
