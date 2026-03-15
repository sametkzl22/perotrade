"""
AI Karar Motoru (AI Decision Engine) v2
========================================
Breakout Tarayıcı, Hacim Anormallikleri, Trend/Web Simülasyonu
ve Güven Skoru & Beklenen Artış hesaplayıcı özellikleri eklendi.
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
    rs = rs.replace([np.inf, -np.inf], 100)
    rsi = 100 - (100 / (1 + rs))
    
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def volatilite_hesapla(df: pd.DataFrame) -> float:
    """Son 14 mum üzerinden standart sapma bazlı volatilite yüzdesi."""
    if len(df) < 14:
        return 0.0
    
    returns = df['close'].pct_change().dropna()
    volatility = returns.std() * 100
    return float(volatility) if not np.isnan(volatility) else 0.0


def dinamik_analiz_araligi(volatilite: float, is_breakout: bool = False) -> int:
    """
    Volatiliteye göre analiz süresi.
    Eğer hacim patlaması (breakout) varsa süreyi sıfırla/kısalt.
    """
    if is_breakout:
        return 5  # Acil durum, anında müdahale

    if volatilite > 5.0:
        return 30   # Ekstrem hareketlilik -> 30 sn
    elif volatilite > 2.0:
        return 120  # Yüksek hareketlilik -> 2 dk
    elif volatilite > 0.5:
        return 300  # Normal -> 5 dk
    else:
        return 900  # Durgun -> 15 dk


# ─────────────────────────────────────────────
# 2) Haber, Trend (Web) ve Duyarlılık (Sentiment) Analizi
# ─────────────────────────────────────────────
def trend_analizi_yap() -> list:
    """
    Hem CoinDesk RSS hem de simüle edilmiş CryptoPanic (Trending/Web)
    verilerini birleştirerek piyasa anlatılarını yakalar.
    """
    haberler = []
    
    # 1. RSS
    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    try:
        feed = feedparser.parse(url)
        haberler = [entry.title for entry in feed.entries[:8]]
    except Exception:
        pass
        
    # 2. Mock Web Trending Simulation (CryptoPanic benzeri anlık trendler)
    # Gerçek sistemde burası Twitter API veya CryptoPanic public API olabilir
    mock_trends = [
        "AI coins are surging as new tech models released",
        "Meme coins experiencing massive liquidations",
        "DePIN sector gaining huge traction with recent funding",
        "SEC approves new exchange traded vehicle",
        "Whale wallets accumulating large amounts of BTC"
    ]
    
    # Günü/saati simüle etmek için rastgele 2 tanesini seç
    haberler.extend(random.sample(mock_trends, 2))
    
    return haberler


def duyarlilik_puanla(haberler: list, sembol: str) -> float:
    if not haberler:
        return 0.0

    pozitif_kelimeler = ["surge", "bull", "rally", "adopt", "buy", "up", "high", "breakout", "accumulating", "approved"]
    negatif_kelimeler = ["crash", "bear", "drop", "hack", "sell", "down", "low", "ban", "sec", "liquidation", "scam"]
    
    coin_adi = sembol.split('/')[0].lower()
    
    skor = 0.0
    for haber in haberler:
        h = haber.lower()
        
        # Genel trend
        for p in pozitif_kelimeler:
            if p in h: skor += 0.1
        for n in negatif_kelimeler:
            if n in h: skor -= 0.1
            
        # Coine özel trend varsa fırla
        if coin_adi in h:
            for p in pozitif_kelimeler:
                if p in h: skor += 0.3
            for n in negatif_kelimeler:
                if n in h: skor -= 0.3

    # Ayrıca AI token vs. trenddeyse pump simülasyonu
    if "ai" in coin_adi and any("ai" in h for h in haberler):
        skor += 0.2

    return max(-1.0, min(1.0, skor))


# ─────────────────────────────────────────────
# 3) Dinamik Coin Seçimi (Top 20 + Breakout Scanner)
# ─────────────────────────────────────────────
def top_coinleri_tara(exchange, limit=30) -> list:
    """
    Sadece hacim değil, anormallik taraması da yapar.
    30 USDT paritesini çeker, arayüz/motor filtrelemesi için döndürür.
    """
    standart_liste = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
    try:
        if exchange.has['fetchTickers']:
            tickers = exchange.fetch_tickers()
            usdt_tickers = {k: v for k, v in tickers.items() if '/USDT' in k and v.get('quoteVolume', 0) > 0}
            
            # Öncelikle salt hacme göre (gerçek bir pazar taramasını simüle etmek için) ilk 50'yi bul
            sirali = sorted(usdt_tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
            secilenler = [k for k, v in sirali[:limit]]
            return secilenler
        else:
            return standart_liste
    except Exception:
        return standart_liste


def anormallik_tara_ve_sec(exchange, top_coinler, sma_kisa, sma_uzun) -> dict:
    """
    Seçilen coinler arasında "Volume Spike" (Hacim Patlaması) ve
    "Consolidation" (Fiyat Sıkışması) arar. Bulursa önceliklendirir.
    """
    secilen_sembol = None
    en_iyi_skor = -999
    secilen_pazar = None
    secilen_sma = None
    secilen_breakout = False
    
    taranan_liste = []
    
    # Haber/Trend verisini bir kere çek
    haberler = trend_analizi_yap()

    # Rate limitleri korumak için listedeki ilk 5'i derinden incele (veya random sample yap)
    for coin in top_coinler[:5]:
        df = mum_verisi_cek(exchange, coin, "1h", limit=sma_uzun+5)
        
        # 1. Pazar Durumu & Sentinel
        pazar = pazar_durumu_cikar(df, coin, pre_fetched_news=haberler)
        sma_sinyal = sinyal_uret(df, sma_kisa, sma_uzun)
        
        # 2. Breakout Kontrolü (Volume Spike + Consolidation)
        is_breakout = False
        son_hacim = df['volume'].iloc[-1]
        ortalama_hacim = df['volume'].iloc[-14:-1].mean()
        
        # Fiyat sıkışması: Son 1 saatteki High - Low %2'den küçük mü?
        son_mum = df.iloc[-1]
        fiyat_farki_pct = ((son_mum['high'] - son_mum['low']) / son_mum['low']) * 100
        
        if son_hacim > (ortalama_hacim * 2) and fiyat_farki_pct < 2.0:
            is_breakout = True
            pazar['is_breakout'] = True
        else:
            pazar['is_breakout'] = False
            
        # 3. Skorlama
        skor = kompozit_skor_hesapla(pazar, sma_sinyal)
        
        taranan_liste.append({
            "Sembol": coin, 
            "Fiyat": pazar["fiyat"], 
            "Skor": round(skor,1), 
            "Breakout": "🔥 EVET" if is_breakout else "HAYIR"
        })
        
        # Breakout varsa skoru suni olarak zıplat ki bu coini "Odak" yapsın
        boosted_skor = skor + 50 if is_breakout else skor

        if boosted_skor > en_iyi_skor:
            en_iyi_skor = boosted_skor
            secilen_sembol = coin
            secilen_pazar = pazar
            secilen_sma = sma_sinyal
            secilen_breakout = is_breakout

    return {
        "secilen_sembol": secilen_sembol,
        "secilen_pazar": secilen_pazar,
        "secilen_sma": secilen_sma,
        "secilen_breakout": secilen_breakout,
        "taranan_liste": taranan_liste
    }


# ─────────────────────────────────────────────
# 4) Komplike Karar Motoru & Yapay Zeka
# ─────────────────────────────────────────────
def pazar_durumu_cikar(df: pd.DataFrame, sembol: str, pre_fetched_news=None) -> dict:
    """Bir coin için tüm teknik + duyarlılık göstergelerini toplar."""
    rsi = rsi_hesapla(df)
    vol_pct = volatilite_hesapla(df)
    
    kisa_hacim = df['volume'].iloc[-3:].mean()
    uzun_hacim = df['volume'].iloc[-14:].mean()
    hacim_artiyor = kisa_hacim > uzun_hacim
    
    haberler = pre_fetched_news if pre_fetched_news else trend_analizi_yap()
    duyarlilik = duyarlilik_puanla(haberler, sembol)
    
    return {
        "rsi": rsi,
        "volatilite": vol_pct,
        "hacim_trend": "Artıyor" if hacim_artiyor else "Düşüyor",
        "duyarlilik": duyarlilik,
        "fiyat": df['close'].iloc[-1],
        "is_breakout": False # Varsayılan, sonra güncellenir
    }


def kompozit_skor_hesapla(pazar: dict, sma_sinyal: str) -> float:
    skor = 0.0
    
    if sma_sinyal == "AL": skor += 30
    elif sma_sinyal == "SAT": skor -= 30
    
    if pazar["rsi"] < 30: skor += 25
    elif pazar["rsi"] > 70: skor -= 25
    elif pazar["rsi"] > 50: skor += 5
    else: skor -= 5
        
    skor += (pazar["duyarlilik"] * 20)
    
    if pazar["hacim_trend"] == "Artıyor":
        skor += 15 if skor > 0 else -15
        
    vol_etki = min(pazar["volatilite"], 5.0) * 2
    skor += vol_etki if skor > 0 else -vol_etki
    
    if pazar.get("is_breakout"):
        skor += 20 # Breakout ekstra ivme
        
    return max(-100.0, min(100.0, skor))


def ai_metrikler(pazar: dict, kompozit_skor: float) -> tuple:
    """Güven skoru ve Beklenen Artış'ı hesaplar"""
    # Güven Skoru (0-100)
    guven = min(100.0, abs(kompozit_skor) * 0.8 + pazar["volatilite"] * 2)
    if pazar.get("is_breakout"): guven = min(100.0, guven + 15)
    
    # Beklenen Artış (Expected Growth % Olarak)
    beklenen = pazar["volatilite"] * 1.5
    if pazar.get("is_breakout"): beklenen *= 2.5
    if kompozit_skor < 0: beklenen = -beklenen # Düşüş beklentisi
    
    return guven, beklenen


def mock_ai_karar(sembol: str, pazar: dict, kompozit_skor: float) -> dict:
    guven, beklenen_artis = ai_metrikler(pazar, kompozit_skor)
    
    if kompozit_skor > 35:
        karar = "AL"
        neden = f"Güçlü Alış! {sembol} kompozit skor {kompozit_skor:.1f}. RSI ({pazar['rsi']:.1f})."
        if pazar.get("is_breakout"): 
            neden = "🚀 ACİL ALIM (BREAKOUT)! Hacim patlaması ve fiyat sıkışması tespit edildi. " + neden
    elif kompozit_skor < -35:
        karar = "SAT"
        neden = f"Güçlü Satış! {sembol} zayıflık gösteriyor (Skor: {kompozit_skor:.1f}). Hacim trendi '{pazar['hacim_trend']}'."
    else:
        karar = "BEKLE"
        neden = f"Kararsız bölge (Skor: {kompozit_skor:.1f}). Kesin kırılım yok, volatilite %{pazar['volatilite']:.1f}."

    sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"], pazar.get("is_breakout", False))
    
    return {
        "sembol": sembol,
        "karar": karar,
        "skor": kompozit_skor,
        "dusunce": neden,
        "aralik_sn": sonraki_sn,
        "guven_skoru": guven,
        "expected_growth": beklenen_artis,
        "ozet": f"RSI: {pazar['rsi']:.1f} | Trend: {pazar['hacim_trend']} | Haber Etkisi: {pazar['duyarlilik']:+.1f}"
    }


def llm_karar(sembol: str, pazar: dict, sma_sinyal: str, api_key: str) -> dict:
    import openai
    client = openai.OpenAI(api_key=api_key)
    
    guven, beklenen_artis = ai_metrikler(pazar, kompozit_skor_hesapla(pazar, sma_sinyal))
    breakout_str = "EVET" if pazar.get("is_breakout") else "HAYIR"
    
    prompt = f"""
    Sen usta bir kripto trader'ısın.
    {sembol} coini için 'AL', 'SAT' veya 'BEKLE' kararı ver.
    Veriler: Fiyat: {pazar['fiyat']}, SMA Sinyali: {sma_sinyal}, RSI: {pazar['rsi']:.2f}, Vol: %{pazar['volatilite']:.2f}, Duyarlılık: {pazar['duyarlilik']:.2f}, Breakout(Patlama): {breakout_str}
    YANIT FORMATI:
    Karar: [AL/SAT/BEKLE]
    Neden: [1 cümle net açıklama]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3
        )
        cevap = response.choices[0].message.content
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
                
        sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"], pazar.get("is_breakout", False))
                
        return {
            "sembol": sembol,
            "karar": karar,
            "skor": kompozit_skor_hesapla(pazar, sma_sinyal),
            "dusunce": neden,
            "aralik_sn": sonraki_sn,
            "guven_skoru": guven,
            "expected_growth": beklenen_artis,
            "ozet": f"LLM Analizi (RSI: {pazar['rsi']:.1f}, Breakout: {breakout_str})"
        }

    except Exception as e:
        print(f"LLM hatası: {e}. Mock AI'ye dönülüyor.")
        skor = kompozit_skor_hesapla(pazar, sma_sinyal)
        return mock_ai_karar(sembol, pazar, skor)
