import datetime
from binance_historical_data import BinanceDataDumper

# Parametreleri MacBook Air M2 performansına ve kütüphane kurallarına göre güncelledik
dumper = BinanceDataDumper(
    path_dir_where_to_dump="data/history", # Klasör yolu parametresi düzeltildi
    asset_class="um",                      # 'um' = USD(T) Margined Futures (Senin botun için kritik)
    data_type="klines",                    # Mum verisi
    data_frequency="15m"                   # 15 dakikalık periyot
)

# Eğitim için seçtiğimiz genişletilmiş coin listesi
tickers_list = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", 
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT", 
    "FETUSDT", "ARBUSDT", "PEPEUSDT", "DOGEUSDT"
]

print("🚀 Veri indirme işlemi başlıyor, bu işlem internet hızına bağlı olarak birkaç dakika sürebilir...")

# Verileri 1 Ocak 2025'ten itibaren indiriyoruz
dumper.dump_data(
    tickers=tickers_list,
    date_start=datetime.date(2025, 1, 1),
    is_to_update_existing=False
)

print("✅ Tüm veriler 'data/history' klasörüne indirildi.")