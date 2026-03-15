"""
Kripto Paper-Trading Bot
========================
Binance veya Gate.io üzerinde SMA crossover stratejisiyle
sanal alım-satım yapan bot iskeleti.

Kullanım:
    python bot.py
"""

import time
import csv
from datetime import datetime, timezone

import ccxt
import pandas as pd
import numpy as np

import config as cfg


# ─────────────────────────────────────────────
# 1) Exchange Oluşturma
# ─────────────────────────────────────────────
def exchange_olustur(exchange_name: str) -> ccxt.Exchange:
    """
    Verilen isme göre ccxt exchange nesnesi oluşturur.
    API key gerektirmez — yalnızca public endpoint kullanılır.
    """
    exchange_sinifi = getattr(ccxt, exchange_name, None)
    if exchange_sinifi is None:
        raise ValueError(f"Desteklenmeyen exchange: {exchange_name}")

    exchange = exchange_sinifi({"enableRateLimit": True})
    return exchange


# ─────────────────────────────────────────────
# 2) OHLCV (Mum) Verisi Çekme
# ─────────────────────────────────────────────
def mum_verisi_cek(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int = 50,
) -> pd.DataFrame:
    """
    Belirtilen çift için son `limit` adet mum verisini çeker.
    Dönen DataFrame sütunları: timestamp, open, high, low, close, volume
    """
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    df = pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


# ─────────────────────────────────────────────
# 3) SMA (Basit Hareketli Ortalama) Hesaplama
# ─────────────────────────────────────────────
def sma_hesapla(df: pd.DataFrame, period: int) -> pd.Series:
    """Kapanış fiyatı üzerinden SMA hesaplar."""
    return df["close"].rolling(window=period).mean()


# ─────────────────────────────────────────────
# 4) Sinyal Üretme — MA Crossover
# ─────────────────────────────────────────────
def sinyal_uret(df: pd.DataFrame) -> str:
    """
    SMA(7) ve SMA(25) crossover mantığı:
      - SMA kısa > SMA uzun  → 'AL'   (golden cross)
      - SMA kısa < SMA uzun  → 'SAT'  (death cross)
      - Eşit veya veri eksik  → 'BEKLE'
    Son iki mumu karşılaştırarak kesişimi tespit eder.
    """
    df = df.copy()
    df["sma_kisa"] = sma_hesapla(df, cfg.SHORT_MA)
    df["sma_uzun"] = sma_hesapla(df, cfg.LONG_MA)

    # Yeterli veri yoksa bekle
    if df["sma_kisa"].isna().iloc[-1] or df["sma_uzun"].isna().iloc[-1]:
        return "BEKLE"

    # Son iki satırın farkına bakarak crossover tespit et
    onceki = df.iloc[-2]
    son = df.iloc[-1]

    # Kısa MA, uzun MA'yı aşağıdan yukarı kesiyor → AL
    if onceki["sma_kisa"] <= onceki["sma_uzun"] and son["sma_kisa"] > son["sma_uzun"]:
        return "AL"

    # Kısa MA, uzun MA'yı yukarıdan aşağı kesiyor → SAT
    if onceki["sma_kisa"] >= onceki["sma_uzun"] and son["sma_kisa"] < son["sma_uzun"]:
        return "SAT"

    return "BEKLE"


# ─────────────────────────────────────────────
# 5) Paper Trade — Sanal Alım / Satım
# ─────────────────────────────────────────────
def islem_yap(sinyal: str, fiyat: float, durum: dict) -> dict:
    """
    Sinyale göre sanal alım veya satım yapar.
    Tüm bakiyeyi kullanır (agresif mod — %100 risk).

    durum dict yapısı:
      bakiye     : float  — USDT cinsinden sanal bakiye
      coin_miktar: float  — Elde tutulan coin miktarı
      pozisyon   : str    — 'YOK' veya 'ACIK'
      gecmis     : list   — İşlem kayıtları
    """
    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if sinyal == "AL" and durum["pozisyon"] == "YOK":
        # Bakiyenin tamamıyla coin al
        miktar = (durum["bakiye"] * cfg.RISK_RATIO) / fiyat
        durum["coin_miktar"] = miktar
        durum["pozisyon"] = "ACIK"

        kayit = {
            "zaman": zaman,
            "sinyal": "AL",
            "fiyat": round(fiyat, 2),
            "miktar": round(miktar, 8),
            "bakiye_usdt": 0.0,
            "toplam_deger": round(miktar * fiyat, 2),
        }
        durum["bakiye"] = 0.0
        durum["gecmis"].append(kayit)
        print(f"✅ [{zaman}] Sanal Alım Yapıldı  | Fiyat: {fiyat:.2f} | Miktar: {miktar:.8f} | Toplam: {kayit['toplam_deger']:.2f} USDT")

    elif sinyal == "SAT" and durum["pozisyon"] == "ACIK":
        # Tüm coini sat
        gelir = durum["coin_miktar"] * fiyat
        kayit = {
            "zaman": zaman,
            "sinyal": "SAT",
            "fiyat": round(fiyat, 2),
            "miktar": round(durum["coin_miktar"], 8),
            "bakiye_usdt": round(gelir, 2),
            "toplam_deger": round(gelir, 2),
        }
        durum["bakiye"] = gelir
        durum["coin_miktar"] = 0.0
        durum["pozisyon"] = "YOK"
        durum["gecmis"].append(kayit)
        print(f"🔴 [{zaman}] Sanal Satım Yapıldı | Fiyat: {fiyat:.2f} | Bakiye: {gelir:.2f} USDT")

    else:
        print(f"⏳ [{zaman}] Beklemede           | Fiyat: {fiyat:.2f} | Pozisyon: {durum['pozisyon']}")

    return durum


# ─────────────────────────────────────────────
# 6) İşlem Geçmişini CSV'ye Kaydetme
# ─────────────────────────────────────────────
def islem_kaydet(gecmis: list, dosya: str) -> None:
    """İşlem geçmişi listesini CSV dosyasına yazar."""
    if not gecmis:
        print("ℹ️  Kaydedilecek işlem geçmişi yok.")
        return

    basliklar = ["zaman", "sinyal", "fiyat", "miktar", "bakiye_usdt", "toplam_deger"]

    with open(dosya, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=basliklar)
        writer.writeheader()
        writer.writerows(gecmis)

    print(f"💾 İşlem geçmişi kaydedildi → {dosya} ({len(gecmis)} işlem)")


# ─────────────────────────────────────────────
# 7) Hedef Bakiye Kontrolü
# ─────────────────────────────────────────────
def hedef_kontrol(durum: dict, fiyat: float) -> bool:
    """
    Toplam portföy değeri (USDT + coin değeri) hedefe ulaştı mı?
    """
    toplam = durum["bakiye"] + (durum["coin_miktar"] * fiyat)
    return toplam >= cfg.TARGET_BALANCE


# ─────────────────────────────────────────────
# 8) Güncel Portföy Durumunu Göster
# ─────────────────────────────────────────────
def durum_goster(durum: dict, fiyat: float) -> None:
    """Portföy özetini konsola basar."""
    coin_deger = durum["coin_miktar"] * fiyat
    toplam = durum["bakiye"] + coin_deger
    ilerleme = (toplam / cfg.TARGET_BALANCE) * 100

    print(f"📊 Portföy | USDT: {durum['bakiye']:.2f} | Coin: {durum['coin_miktar']:.8f} ({coin_deger:.2f} USDT) | Toplam: {toplam:.2f} USDT | Hedef: %{ilerleme:.1f}")
    print("─" * 70)


# ─────────────────────────────────────────────
# 9) Ana Döngü
# ─────────────────────────────────────────────
def main():
    """Bot'un ana giriş noktası — sonsuz döngü ile çalışır."""

    print("=" * 70)
    print("  🤖 Kripto Paper-Trading Bot Başlatılıyor")
    print("=" * 70)
    print(f"  Exchange  : {cfg.EXCHANGE_NAME}")
    print(f"  Sembol    : {cfg.SYMBOL}")
    print(f"  Periyot   : {cfg.TIMEFRAME}")
    print(f"  Strateji  : SMA({cfg.SHORT_MA}) / SMA({cfg.LONG_MA}) Crossover")
    print(f"  Bakiye    : {cfg.INITIAL_BALANCE} USDT (paper)")
    print(f"  Hedef     : {cfg.TARGET_BALANCE} USDT")
    print(f"  Risk      : %{cfg.RISK_RATIO * 100:.0f}")
    print(f"  Aralık    : {cfg.CHECK_INTERVAL}s ({cfg.CHECK_INTERVAL // 60} dk)")
    print("=" * 70)

    # Exchange nesnesini oluştur
    exchange = exchange_olustur(cfg.EXCHANGE_NAME)
    print(f"✅ {cfg.EXCHANGE_NAME.capitalize()} bağlantısı kuruldu.\n")

    # Sanal portföy durumu
    durum = {
        "bakiye": cfg.INITIAL_BALANCE,
        "coin_miktar": 0.0,
        "pozisyon": "YOK",
        "gecmis": [],
    }

    try:
        while True:
            # Mum verisini çek
            df = mum_verisi_cek(exchange, cfg.SYMBOL, cfg.TIMEFRAME, limit=cfg.LONG_MA + 5)
            son_fiyat = float(df["close"].iloc[-1])

            # SMA crossover sinyali üret
            sinyal = sinyal_uret(df)

            # Sanal işlemi gerçekleştir
            durum = islem_yap(sinyal, son_fiyat, durum)

            # Portföy durumunu göster
            durum_goster(durum, son_fiyat)

            # Hedefe ulaşıldı mı?
            if hedef_kontrol(durum, son_fiyat):
                toplam = durum["bakiye"] + (durum["coin_miktar"] * son_fiyat)
                print(f"\n🎯 HEDEF ULAŞILDI! Toplam portföy değeri: {toplam:.2f} USDT")
                islem_kaydet(durum["gecmis"], cfg.TRADE_LOG_FILE)
                break

            # Bir sonraki analiz için bekle
            print(f"⏱️  Sonraki analiz {cfg.CHECK_INTERVAL // 60} dakika sonra...\n")
            time.sleep(cfg.CHECK_INTERVAL)

    except KeyboardInterrupt:
        # Ctrl+C ile durdurulduğunda geçmişi kaydet
        print("\n\n⛔ Bot kullanıcı tarafından durduruldu.")
        son_fiyat_kayit = float(df["close"].iloc[-1]) if "df" in dir() else 0.0
        toplam = durum["bakiye"] + (durum["coin_miktar"] * son_fiyat_kayit)
        print(f"📊 Son portföy değeri: {toplam:.2f} USDT")
        islem_kaydet(durum["gecmis"], cfg.TRADE_LOG_FILE)
        print("👋 Güle güle!")


if __name__ == "__main__":
    main()
