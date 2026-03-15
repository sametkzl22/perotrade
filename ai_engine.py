"""
AI Karar Motoru (AI Decision Engine)
=====================================
Kripto paralar için teknik analiz (RSI, Volatilite, SMA Crossover)
ile haber duyarlılığını (Sentiment) birleştiren modül.

Ayrıca dinamik "Top 20" coin taraması ve değişken zamanlama içerir.
LLM (OpenAI) iskeleti entegredir, key yoksa Mock AI çalışır.
"""

import math
import random
import feedparser
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ─────────────────────────────────────────────
# 1) Teknik Analiz Göstergeleri & Veri Çekme
# ─────────────────────────────────────────────
def mum_verisi_cek(exchange, symbol, timeframe="1h", limit=55):
    """OHLCV mum verisini pandas DataFrame olarak döndürür."""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def sma_hesapla(series: pd.Series, period: int) -> pd.Series:
    """Basit Hareketli Ortalama hesaplar."""
    return series.rolling(window=period).mean()

def sinyal_uret(df: pd.DataFrame, sma_kisa: int, sma_uzun: int) -> str:
    """SMA crossover sinyali üretir."""
    df = df.copy()
    df["sma_k"] = sma_hesapla(df["close"], sma_kisa)
    df["sma_u"] = sma_hesapla(df["close"], sma_uzun)

    if df["sma_k"].isna().iloc[-1] or df["sma_u"].isna().iloc[-1]: return "BEKLE"

    onceki, son = df.iloc[-2], df.iloc[-1]
    if onceki["sma_k"] <= onceki["sma_u"] and son["sma_k"] > son["sma_u"]: return "AL"
    if onceki["sma_k"] >= onceki["sma_u"] and son["sma_k"] < son["sma_u"]: return "SAT"
    return "BEKLE"

def rsi_hesapla(df: pd.DataFrame, period: int = 14) -> float:
    """Relative Strength Index (RSI) hesaplar (0-100)."""
    if len(df) < period + 1:
        return 50.0

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    rs = rs.replace([np.inf, -np.inf], 100) # Sıfıra bölünme koruması
    rsi = 100 - (100 / (1 + rs))
    
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def volatilite_hesapla(df: pd.DataFrame) -> float:
    """Son 14 mum üzerinden standart sapma bazlı volatilite yüzdesi (Yüksek volatilite = Yüksek risk/fırsat)."""
    if len(df) < 14:
        return 0.0
    
    returns = df['close'].pct_change().dropna()
    volatility = returns.std() * 100  # Yüzdelik cinsinden
    return float(volatility) if not np.isnan(volatility) else 0.0


def dinamik_analiz_araligi(volatilite: float) -> int:
    """
    Volatiliteye göre bir sonraki analizin kaç saniye sonra yapılacağını belirler.
    Çok hareketli piyasa: 30-60s | Durgun piyasa: 900s (15 dk)
    """
    if volatilite > 5.0:
        return 30   # Ekstrem hareketlilik -> 30 sn
    elif volatilite > 2.0:
        return 120  # Yüksek hareketlilik -> 2 dk
    elif volatilite > 0.5:
        return 300  # Normal -> 5 dk
    else:
        return 900  # Durgun -> 15 dk


# ─────────────────────────────────────────────
# 2) Haber ve Duyarlılık (Sentiment) Analizi
# ─────────────────────────────────────────────
def haber_cek_rss() -> list:
    """CoinDesk RSS üzerinden en güncel haber başlıklarını çeker."""
    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    try:
        feed = feedparser.parse(url)
        haberler = [entry.title for entry in feed.entries[:10]]
        return haberler
    except Exception:
        return []

def duyarlilik_puanla(haberler: list, sembol: str) -> float:
    """
    Haberlerdeki genel ve coine özel kelimelere bakarak
    -1.0 (Aşırı Olumsuz) ile +1.0 (Aşırı Olumlu) arası skor üretir.
    """
    if not haberler:
        return 0.0

    pozitif_kelimeler = ["surge", "bull", "rally", "adopt", "buy", "up", "high", "breakout"]
    negatif_kelimeler = ["crash", "bear", "drop", "hack", "sell", "down", "low", "ban", "sec"]
    
    coin_adi = sembol.split('/')[0].lower() # Örn: BTC
    
    skor = 0.0
    for haber in haberler:
        h = haber.lower()
        
        # Genel piyasa duyarlılığı
        for p in pozitif_kelimeler:
            if p in h: skor += 0.1
        for n in negatif_kelimeler:
            if n in h: skor -= 0.1
            
        # Coine özel haber varsa ağırlığı artır
        if coin_adi in h:
            for p in pozitif_kelimeler:
                if p in h: skor += 0.3
            for n in negatif_kelimeler:
                if n in h: skor -= 0.3

    # Skoru -1.0 ile 1.0 arasına sıkıştır (clip)
    return max(-1.0, min(1.0, skor))


# ─────────────────────────────────────────────
# 3) Dinamik Coin Seçimi (Top 20)
# ─────────────────────────────────────────────
def top_coinleri_tara(exchange, limit=20) -> list:
    """
    Borsadaki hacmi en yüksek 20 coini USDT paritesinde bulur.
    Gerçek API'de tüm ticker'ları çekmek uzun sürebileceğinden,
    eğer fetch_tickers desteklenmiyorsa standart/popüler listeyi döndürür.
    """
    standart_liste = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", 
                      "AVAX/USDT", "DOGE/USDT", "DOT/USDT", "MATIC/USDT", "LINK/USDT"]
    try:
        if exchange.has['fetchTickers']:
            tickers = exchange.fetch_tickers()
            # Sadece USDT çiftlerini ve volume'u olanları al
            usdt_tickers = {k: v for k, v in tickers.items() if '/USDT' in k and v.get('quoteVolume', 0) > 0}
            
            # Hacme göre sırala (azalan)
            sirali = sorted(usdt_tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
            return [k for k, v in sirali[:limit]]
        else:
            return standart_liste
    except Exception:
        return standart_liste


# ─────────────────────────────────────────────
# 4) Komplike Karar Motoru & Yapay Zeka
# ─────────────────────────────────────────────
def pazar_durumu_cikar(df: pd.DataFrame, sembol: str) -> dict:
    """Bir coin için tüm teknik + duyarlılık göstergelerini toplar."""
    rsi = rsi_hesapla(df)
    vol_pct = volatilite_hesapla(df)
    
    # Trend (Hacim trendi)
    kisa_hacim = df['volume'].iloc[-3:].mean()
    uzun_hacim = df['volume'].iloc[-14:].mean()
    hacim_artiyor = kisa_hacim > uzun_hacim
    
    # Haber skoru
    haberler = haber_cek_rss()
    duyarlilik = duyarlilik_puanla(haberler, sembol)
    
    return {
        "rsi": rsi,
        "volatilite": vol_pct,
        "hacim_trend": "Artıyor" if hacim_artiyor else "Düşüyor",
        "duyarlilik": duyarlilik,
        "fiyat": df['close'].iloc[-1]
    }


def kompozit_skor_hesapla(pazar: dict, sma_sinyal: str) -> float:
    """
    Farklı ağırlıklarla birleştirilmiş alım gücü skoru hesaplar (-100 ile +100 arası).
    """
    skor = 0.0
    
    # %30 SMA (Al: +30, Sat: -30)
    if sma_sinyal == "AL": skor += 30
    elif sma_sinyal == "SAT": skor -= 30
    
    # %25 RSI (Aşırı satım > Al fırastı: RSI<30 -> +25, RSI>70 -> -25)
    if pazar["rsi"] < 30: skor += 25
    elif pazar["rsi"] > 70: skor -= 25
    elif pazar["rsi"] > 50: skor += 5   # Yükseliş trendi
    else: skor -= 5                     # Düşüş trendi
        
    # %20 Haber Duyarlılığı (Örn: +0.5 skor -> +10 puan)
    skor += (pazar["duyarlilik"] * 20)
    
    # %15 Hacim Trendi
    if pazar["hacim_trend"] == "Artıyor":
        skor += 15 if skor > 0 else -15 # Trend yönünü güçlendirir
        
    # %10 Volatilite (Yüksek volatilite AL yönünde ivmeyi artırır, SAT yönünde paniği artırır)
    vol_etki = min(pazar["volatilite"], 5.0) * 2 # Max 10 puan
    skor += vol_etki if skor > 0 else -vol_etki

    return max(-100.0, min(100.0, skor))


def mock_ai_karar(sembol: str, pazar: dict, kompozit_skor: float) -> dict:
    """
    LLM API key olmadığında çalışan Rule-Based (Kural Tabanlı) Karar Modülü.
    Yapay zeka gibi düşünce logu üretir.
    """
    if kompozit_skor > 35:
        karar = "AL"
        neden = f"Piyasa algısı güçlü. {sembol} için kompozit skor {kompozit_skor:.1f}. RSI ({pazar['rsi']:.1f}) alım bölgesinde ve haber duyarlılığı destekleyici."
    elif kompozit_skor < -35:
        karar = "SAT"
        neden = f"{sembol} zayıflık gösteriyor (Skor: {kompozit_skor:.1f}). Hacim trendi '{pazar['hacim_trend']}' ve teknik göstergeler satışı işaret ediyor."
    else:
        karar = "BEKLE"
        neden = f"Kararsız bölge (Skor: {kompozit_skor:.1f}). Kesin bir kırılım görünmüyor, volatilite %{pazar['volatilite']:.1f}. Beklemeyi tercih ediyorum."

    # Sonraki analiz zamanlamasını belirle
    sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"])
    
    return {
        "sembol": sembol,
        "karar": karar,
        "skor": kompozit_skor,
        "dusunce": neden,
        "aralik_sn": sonraki_sn
    }


def llm_karar(sembol: str, pazar: dict, sma_sinyal: str, api_key: str) -> dict:
    """
    OpenAI API ile LLM tabanlı karar üretir (Genişletilebilir iskelet).
    """
    import openai
    
    client = openai.OpenAI(api_key=api_key)
    
    prompt = f"""
    Sen usta bir kripto para ticaret botusun. Paper trading yapıyorsun.
    Aşağıdaki verilere göre {sembol} coini için 'AL', 'SAT' veya 'BEKLE' kararı ver kısaca nedenini açıkla.
    Veriler:
    - Fiyat: {pazar['fiyat']}
    - SMA Sinyali: {sma_sinyal}
    - RSI: {pazar['rsi']:.2f}
    - Volatilite: %{pazar['volatilite']:.2f}
    - Haber Duyarlılığı: {pazar['duyarlilik']:.2f} (-1.0 ile 1.0 arası)
    - Hacim Trendi: {pazar['hacim_trend']}
    
    YANIT FORMATI:
    Karar: [AL/SAT/BEKLE]
    Neden: [1-2 cümlelik açıklama]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3
        )
        cevap = response.choices[0].message.content
        
        # Basit parser (gerçek kullanımda JSON parser önerilir)
        lines = cevap.strip().split('\n')
        karar = "BEKLE"
        neden = cevap
        
        for l in lines:
            if l.startswith("Karar:"):
                k = l.split("Karar:")[1].strip().upper()
                if "AL" in k: karar = "AL"
                elif "SAT" in k: karar = "SAT"
            if l.startswith("Neden:"):
                neden = l.split("Neden:")[1].strip()
                
        # LLM Kararı da dinamik zamana tabi
        sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"])
                
        return {
            "sembol": sembol,
            "karar": karar,
            "skor": kompozit_skor_hesapla(pazar, sma_sinyal), # Görsel için hesapla
            "dusunce": neden,
            "aralik_sn": sonraki_sn
        }

    except Exception as e:
        # Hata durumunda (mesela kotaya takılındığında) Mock AI'ya fallback (geri düşüş) yap
        print(f"LLM hatası: {e}. Mock AI'ye dönülüyor.")
        skor = kompozit_skor_hesapla(pazar, sma_sinyal)
        return mock_ai_karar(sembol, pazar, skor)
