"""
AI Karar Motoru (AI Decision Engine) v3
========================================
Breakout Tarayıcı, Hacim Anormallikleri, Trend/Web Simülasyonu,
Twitter (X) Duyarlılık Analizi, Güven Skoru & Beklenen Artış,
ve Vadeli İşlemler (Long/Short) stratejisi.
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
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def sma_hesapla(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def sinyal_uret(df: pd.DataFrame, sma_kisa: int, sma_uzun: int) -> str:
    df = df.copy()
    df["sma_k"] = sma_hesapla(df["close"], sma_kisa)
    df["sma_u"] = sma_hesapla(df["close"], sma_uzun)

    if df["sma_k"].isna().iloc[-1] or df["sma_u"].isna().iloc[-1]: return "BEKLE"

    onceki, son = df.iloc[-2], df.iloc[-1]
    if onceki["sma_k"] <= onceki["sma_u"] and son["sma_k"] > son["sma_u"]: return "AL"   # Potansiyel LONG sinyali
    if onceki["sma_k"] >= onceki["sma_u"] and son["sma_k"] < son["sma_u"]: return "SAT"  # Potansiyel SHORT sinyali
    return "BEKLE"

def rsi_hesapla(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1: return 50.0
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rs = rs.replace([np.inf, -np.inf], 100)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0

def volatilite_hesapla(df: pd.DataFrame) -> float:
    if len(df) < 14: return 0.0
    returns = df['close'].pct_change().dropna()
    volatility = returns.std() * 100
    return float(volatility) if not np.isnan(volatility) else 0.0

def dinamik_analiz_araligi(volatilite: float, is_breakout: bool = False) -> int:
    if is_breakout: return 5
    if volatilite > 5.0: return 30
    elif volatilite > 2.0: return 120
    elif volatilite > 0.5: return 300
    else: return 900


# ─────────────────────────────────────────────
# 2) Sosyal & Web Duyarlılık (News + Twitter)
# ─────────────────────────────────────────────
def trend_analizi_yap() -> list:
    haberler = []
    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    try:
        feed = feedparser.parse(url)
        haberler = [entry.title for entry in feed.entries[:8]]
    except Exception:
        pass
        
    mock_trends = [
        "AI coins are surging as new tech models released",
        "Meme coins experiencing massive liquidations",
        "DePIN sector gaining huge traction with recent funding",
        "SEC approves new exchange traded vehicle",
        "Whale wallets accumulating large amounts of BTC"
    ]
    haberler.extend(random.sample(mock_trends, 2))
    return haberler

def twitter_etkisi_puanla(sembol: str) -> dict:
    """Mock Twitter (X) Duyarlılık Simülasyonu"""
    coin_adi = sembol.split('/')[0]
    influencer_tweettleri = [
        {"yazar": "Elon Musk", "tweet": f"Thinking about buying more {coin_adi} 🚀. To the moon!", "skor": 0.5},
        {"yazar": "Elon Musk", "tweet": f"{coin_adi} seems overvalued right now. Be careful. 📉", "skor": -0.5},
        {"yazar": "Michael Saylor", "tweet": f"{coin_adi} is digital energy. HODL forever.", "skor": 0.4},
        {"yazar": "Michael Saylor", "tweet": "Stick to BTC, drop the altcoins.", "skor": -0.3 if coin_adi != "BTC" else 0.4},
        {"yazar": "Whale Alert", "tweet": f"🚨 10,000,000 {coin_adi} transferred to unknown wallet.", "skor": 0.3}, # Bullish accumulation
        {"yazar": "Whale Alert", "tweet": f"🚨 50,000,000 {coin_adi} transferred to Binance.", "skor": -0.4}, # Bearish dump
    ]
    
    # Sisteme %30 ihtimalle bir influencer tweet'i tetiklensin
    if random.random() < 0.30: 
        secilen_tweet = random.choice(influencer_tweettleri)
        return {"aktif": True, "yazar": secilen_tweet["yazar"], "skor": secilen_tweet["skor"]}
    return {"aktif": False, "skor": 0.0}

def duyarlilik_puanla(haberler: list, sembol: str, twitter_skoru: float) -> float:
    if not haberler and twitter_skoru == 0: return 0.0

    pozitif_kelimeler = ["surge", "bull", "rally", "adopt", "buy", "up", "high", "breakout"]
    negatif_kelimeler = ["crash", "bear", "drop", "hack", "sell", "down", "low", "ban", "sec", "liquidation"]
    
    coin_adi = sembol.split('/')[0].lower()
    skor = 0.0
    for haber in haberler:
        h = haber.lower()
        for p in pozitif_kelimeler:
            if p in h: skor += 0.1
        for n in negatif_kelimeler:
            if n in h: skor -= 0.1
        if coin_adi in h:
            for p in pozitif_kelimeler:
                if p in h: skor += 0.3
            for n in negatif_kelimeler:
                if n in h: skor -= 0.3

    if "ai" in coin_adi and any("ai" in h for h in haberler): skor += 0.2
    
    # Twitter skorunu doğrudan ekle (max +-0.5 etkisi var)
    skor += twitter_skoru  

    return max(-1.0, min(1.0, skor))


# ─────────────────────────────────────────────
# 3) Dinamik Coin Seçimi (Top 20 + Breakout Scanner)
# ─────────────────────────────────────────────
def top_coinleri_tara(exchange, limit=30) -> list:
    standart_liste = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
    try:
        if exchange.has['fetchTickers']:
            tickers = exchange.fetch_tickers()
            usdt_tickers = {k: v for k, v in tickers.items() if '/USDT' in k and v.get('quoteVolume', 0) > 0}
            sirali = sorted(usdt_tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
            return [k for k, v in sirali[:limit]]
        else:
            return standart_liste
    except Exception:
        return standart_liste

def anormallik_tara_ve_sec(exchange, top_coinler, sma_kisa, sma_uzun) -> dict:
    secilen_sembol = None
    en_baskın_mutlak_skor = -1 # Yön farketmeksizin en güçlü sinyali bul (Long veya Short)
    secilen_pazar = None
    secilen_sma = None
    secilen_breakout = False
    
    taranan_liste = []
    haberler = trend_analizi_yap()

    for coin in top_coinler[:8]:
        try:
            df = mum_verisi_cek(exchange, coin, "1h", limit=sma_uzun+5)
            twitter_verisi = twitter_etkisi_puanla(coin)
            pazar = pazar_durumu_cikar(df, coin, pre_fetched_news=haberler, twitter_verisi=twitter_verisi)
            sma_sinyal = sinyal_uret(df, sma_kisa, sma_uzun)
            
            # Breakout Kontrolü (Hacim Patlaması & Konsolidasyon)
            is_breakout = False
            son_hacim = df['volume'].iloc[-1]
            ortalama_hacim = df['volume'].iloc[-14:-1].mean()
            son_mum = df.iloc[-1]
            fiyat_farki_pct = ((son_mum['high'] - son_mum['low']) / son_mum['low']) * 100
            
            if son_hacim > (ortalama_hacim * 2) and fiyat_farki_pct < 2.0:
                is_breakout = True
            pazar['is_breakout'] = is_breakout
                
            skor = kompozit_skor_hesapla(pazar, sma_sinyal)
            
            taranan_liste.append({
                "Sembol": coin, 
                "Fiyat": pazar["fiyat"], 
                "Skor": round(skor,1), 
                "Breakout": "🔥 EVET" if is_breakout else "HAYIR"
            })
            
            # Futures botu hem aşağı hem yukarıyı arar. Modeline (mutlak değerine) bakıyoruz.
            mutlak_guc = abs(skor)
            if is_breakout: mutlak_guc += 50
            
            if mutlak_guc > en_baskın_mutlak_skor:
                en_baskın_mutlak_skor = mutlak_guc
                secilen_sembol = coin
                secilen_pazar = pazar
                secilen_sma = sma_sinyal
                secilen_breakout = is_breakout
                
        except Exception:
            continue

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
def pazar_durumu_cikar(df: pd.DataFrame, sembol: str, pre_fetched_news=None, twitter_verisi=None) -> dict:
    rsi = rsi_hesapla(df)
    vol_pct = volatilite_hesapla(df)
    kisa_hacim = df['volume'].iloc[-3:].mean()
    uzun_hacim = df['volume'].iloc[-14:].mean()
    hacim_artiyor = kisa_hacim > uzun_hacim
    
    haberler = pre_fetched_news if pre_fetched_news else trend_analizi_yap()
    tw_veri = twitter_verisi if twitter_verisi else twitter_etkisi_puanla(sembol)
    
    duyarlilik = duyarlilik_puanla(haberler, sembol, tw_veri["skor"])
    
    return {
        "rsi": rsi,
        "volatilite": vol_pct,
        "hacim_trend": "Artıyor" if hacim_artiyor else "Düşüyor",
        "duyarlilik": duyarlilik,
        "twitter": tw_veri,
        "fiyat": df['close'].iloc[-1],
        "is_breakout": False 
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
        skor += 20 if skor > 0 else -20 # Breakout trend yönünde fırlatır
        
    return max(-100.0, min(100.0, skor))

def ai_metrikler(pazar: dict, kompozit_skor: float) -> tuple:
    guven = min(100.0, abs(kompozit_skor) * 0.8 + pazar["volatilite"] * 2)
    if pazar.get("is_breakout"): guven = min(100.0, guven + 15)
    
    beklenen = pazar["volatilite"] * 1.5
    if pazar.get("is_breakout"): beklenen *= 2.5
    if kompozit_skor < 0: beklenen = -beklenen # Düşüş beklentisi (-)
    
    return guven, beklenen

def mock_ai_karar(sembol: str, pazar: dict, kompozit_skor: float, acik_pozisyon: str) -> dict:
    guven, beklenen_artis = ai_metrikler(pazar, kompozit_skor)
    
    # Futures Karar Ağacı
    karar = "BEKLE"
    neden = f"Piyasa kararsız (Skor: {kompozit_skor:.1f}). Kesin kırılım yok."
    
    twitter_msg = f" 🐦 [{pazar['twitter']['yazar']}: {pazar['twitter']['skor']:+.1f} Etki]" if pazar.get("twitter", {}).get("aktif") else ""

    if kompozit_skor > 40:
        if acik_pozisyon == "SHORT": 
            karar = "KAPAT"
            neden = f"Trend YUKARI döndü! SHORT pozisyon riske girdi, acil kapatılıyor (Skor: {kompozit_skor:.1f})."
        else:
            karar = "LONG"
            neden = f"Güçlü YÜKSELİŞ Beklentisi! {sembol} kompozit skoru {kompozit_skor:.1f}. RSI ({pazar['rsi']:.1f}).{twitter_msg}"
            if pazar.get("is_breakout"): neden = "🚀 ACİL LONG (BREAKOUT)! Hacim patlaması tespit edildi. " + neden
            
    elif kompozit_skor < -40:
        if acik_pozisyon == "LONG":
            karar = "KAPAT"
            neden = f"Trend AŞAĞI döndü! LONG pozisyon terse düştü, acil kapatılıyor (Skor: {kompozit_skor:.1f})."
        else:
            karar = "SHORT"
            neden = f"Güçlü DÜŞÜŞ Beklentisi! {sembol} zayıflık gösteriyor (Skor: {kompozit_skor:.1f}). Hacim '{pazar['hacim_trend']}'.{twitter_msg}"
            if pazar.get("is_breakout"): neden = "📉 ACİL SHORT (CRASH)! Aşağı yönlü hacim patlaması tespit edildi. " + neden

    sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"], pazar.get("is_breakout", False))
    
    # Ters pozisyonda veya pozisyon kararsızsa güven skorunu ayarla
    return {
        "sembol": sembol,
        "karar": karar,
        "skor": kompozit_skor,
        "dusunce": neden,
        "aralik_sn": sonraki_sn,
        "guven_skoru": guven,
        "expected_growth": beklenen_artis,
        "ozet": f"RSI: {pazar['rsi']:.1f} | Trend: {pazar['hacim_trend']} | Sosyal Puan: {pazar['duyarlilik']:+.1f}"
    }

def llm_karar(sembol: str, pazar: dict, sma_sinyal: str, api_key: str, acik_pozisyon: str) -> dict:
    import openai
    client = openai.OpenAI(api_key=api_key)
    
    komp_skor = kompozit_skor_hesapla(pazar, sma_sinyal)
    guven, beklenen_artis = ai_metrikler(pazar, komp_skor)
    breakout_str = "EVET" if pazar.get("is_breakout") else "HAYIR"
    tw_str = pazar.get('twitter', {}).get('tweet', 'Yok')
    
    prompt = f"""
    Sen usta bir kripto Furtures (Vadeli İşlem) botusun.
    Mevcut Açık Pozisyon: {acik_pozisyon} ("YOK", "LONG", veya "SHORT" olabilir).
    {sembol} coini için 'LONG', 'SHORT', 'KAPAT' veya 'BEKLE' kararı ver.
    Eğer pozisyonun tersine güçlü bir trend varsa 'KAPAT' demelisin.
    
    Veriler: Fiyat: {pazar['fiyat']}, SMA Sinyali: {sma_sinyal}, RSI: {pazar['rsi']:.2f}, Vol: %{pazar['volatilite']:.2f}, Trend: {pazar['hacim_trend']}, Breakout: {breakout_str}
    Sosyal Trend Skoru: {pazar['duyarlilik']:.2f}
    Güncel Influencer Tweeti: {tw_str}
    
    YANIT FORMATI:
    Karar: [LONG/SHORT/KAPAT/BEKLE]
    Neden: [1 cümle net açıklırma]
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
                if "LONG" in k: karar = "LONG"
                elif "SHORT" in k: karar = "SHORT"
                elif "KAPAT" in k: karar = "KAPAT"
            if l.startswith("Neden:"):
                neden = l.split("Neden:")[1].strip()
                
        sonraki_sn = dinamik_analiz_araligi(pazar["volatilite"], pazar.get("is_breakout", False))
                
        return {
            "sembol": sembol,
            "karar": karar,
            "skor": komp_skor,
            "dusunce": neden,
            "aralik_sn": sonraki_sn,
            "guven_skoru": guven,
            "expected_growth": beklenen_artis,
            "ozet": f"LLM Futures: RSI {pazar['rsi']:.1f} | Soc {pazar['duyarlilik']:+.1f}"
        }
    except Exception as e:
        print(f"LLM hatası: {e}. Mock AI'ye dönülüyor.")
        return mock_ai_karar(sembol, pazar, komp_skor, acik_pozisyon)
