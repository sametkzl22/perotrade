"""
AI Karar Motoru (AI Decision Engine) v4
========================================
Breakout Tarayıcı, Hacim Anormallikleri, Trend/Web Simülasyonu,
Twitter (X) Duyarlılık Analizi, Güven Skoru & Beklenen Artış,
Vadeli İşlemler (Long/Short) stratejisi ve *Live Test Optimizasyonları*
(BTC Korelasyonu, Fonlama Oranı, 2s Breakout Taraması).
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
    try:
        if exchange is None or not symbol:
            return pd.DataFrame()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not ohlcv or not isinstance(ohlcv, list):
            return pd.DataFrame()
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return pd.DataFrame()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        # NaN/None temizliği
        df = df.dropna(subset=["close", "volume"])
        return df
    except Exception:
        return pd.DataFrame()

def sma_hesapla(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def sinyal_uret(df: pd.DataFrame, sma_kisa: int, sma_uzun: int) -> str:
    if df is None or df.empty or len(df) < max(sma_kisa, sma_uzun) + 1:
        return "BEKLE"
    try:
        df = df.copy()
        df["sma_k"] = sma_hesapla(df["close"], sma_kisa)
        df["sma_u"] = sma_hesapla(df["close"], sma_uzun)

        if df["sma_k"].isna().iloc[-1] or df["sma_u"].isna().iloc[-1]: return "BEKLE"

        onceki, son = df.iloc[-2], df.iloc[-1]
        if onceki["sma_k"] <= onceki["sma_u"] and son["sma_k"] > son["sma_u"]: return "AL"
        if onceki["sma_k"] >= onceki["sma_u"] and son["sma_k"] < son["sma_u"]: return "SAT"
        return "BEKLE"
    except Exception:
        return "BEKLE"

def rsi_hesapla(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or df.empty or len(df) < period + 1:
        return 50.0
    try:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rs = rs.replace([np.inf, -np.inf], 100)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if not np.isnan(val) else 50.0
    except Exception:
        return 50.0

def volatilite_hesapla(df: pd.DataFrame) -> float:
    if df is None or df.empty or len(df) < 14:
        return 0.0
    try:
        returns = df['close'].pct_change().dropna()
        if returns.empty:
            return 0.0
        volatility = returns.std() * 100
        return float(volatility) if not np.isnan(volatility) else 0.0
    except Exception:
        return 0.0

def dinamik_analiz_araligi(volatilite: float, is_breakout: bool = False) -> int:
    """ Breakout varsa ultra-hızlı 2 saniye reaksiyon süresi. """
    if is_breakout: return 2
    if volatilite > 5.0: return 30
    elif volatilite > 2.0: return 120
    elif volatilite > 0.5: return 300
    else: return 900


# ─────────────────────────────────────────────
# 2) Piyasa Lideri (BTC) ve Fonlama Oranı (PRO)
# ─────────────────────────────────────────────
def btc_trendi_analiz_et(exchange) -> str:
    """ Genel piyasa sağlığını (BTC yönünü) analiz eder. """
    try:
        if exchange is None:
            return "BİLİNMİYOR"
        df = mum_verisi_cek(exchange, "BTC/USDT", "1h", limit=15)
        if df is None or df.empty or len(df) < 14:
            return "BİLİNMİYOR"
        sma_k = sma_hesapla(df['close'], 7).iloc[-1]
        sma_u = sma_hesapla(df['close'], 14).iloc[-1]
        if pd.isna(sma_k) or pd.isna(sma_u):
            return "YATAY"
        rsi = rsi_hesapla(df, 7)
        
        if sma_k > sma_u and rsi > 55: return "YUKARI"
        elif sma_k < sma_u and rsi < 45: return "AŞAĞI"
        else: return "YATAY"
    except Exception:
        return "BİLİNMİYOR"

def fonlama_orani_getir(exchange, symbol: str) -> dict:
    """ Fonlama oranını simüle ederek / çekerek aşırı riskli yönü bulur. """
    try:
        if exchange is not None and hasattr(exchange, 'has') and isinstance(exchange.has, dict):
            if exchange.has.get('fetchFundingRate'):
                res = exchange.fetch_funding_rate(symbol)
                if isinstance(res, dict):
                    oran = float(res.get('fundingRate', 0.0) or 0.0) * 100
                    risk = "Yok"
                    if oran > 0.05: risk = "Uzun(Long) Riskli"
                    elif oran < -0.05: risk = "Kısa(Short) Riskli"
                    return {"oran": oran, "risk": risk}
    except Exception:
        pass
        
    s_oran = random.uniform(-0.06, 0.08)
    risk = "Yok"
    if s_oran > 0.05: risk = "Uzun(Long) Riskli"
    elif s_oran < -0.05: risk = "Kısa(Short) Riskli"
    return {"oran": s_oran, "risk": risk}


# ─────────────────────────────────────────────
# 3) Sosyal & Web Duyarlılık (News + Twitter)
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
        "Whale wallets accumulating large amounts of BTC",
        "Global tension rises as new border conflict emerges",
        "Fed signals unexpected rate hike amid inflation fears",
        "Severe economic sanctions heavily impacting markets",
        "Major exchange announces new listing for trending coin",
        "Elon Musk tweets about crypto causing market volatility"
    ]
    haberler.extend(random.sample(mock_trends, 3))
    return haberler

def haber_anahtar_kelime_puanla(haberler: list) -> dict:
    """Haberlerdeki anahtar kelimelere ağırlıklı puan verir."""
    if not haberler or not isinstance(haberler, list):
        return {"toplam_puan": 0.0, "tetiklenen": [], "risk_seviyesi": "Stabil"}
    kelime_agirliklari = {
        "war": -0.8, "conflict": -0.7, "sanctions": -0.6, "missile": -0.9, "strike": -0.5, "tension": -0.4,
        "savaş": -0.8, "çatışma": -0.7, "yaptırım": -0.6,
        "fed rate": -0.5, "rate hike": -0.6, "inflation": -0.4, "recession": -0.7,
        "rate cut": 0.5, "stimulus": 0.6,
        "listing": 0.7, "elon": 0.4, "adoption": 0.5, "etf": 0.6, "approval": 0.5,
        "surge": 0.4, "bull": 0.3, "breakout": 0.5, "accumulate": 0.3,
        "sec": -0.3, "hack": -0.8, "exploit": -0.7, "ban": -0.5, "crash": -0.6,
        "liquidation": -0.4, "delisting": -0.6
    }
    
    toplam_puan = 0.0
    tetiklenen_kelimeler = []
    
    for haber in haberler:
        if not isinstance(haber, str):
            continue
        hl = haber.lower()
        for kelime, agirlik in kelime_agirliklari.items():
            if kelime in hl:
                toplam_puan += agirlik
                if kelime not in [k for k, _ in tetiklenen_kelimeler]:
                    tetiklenen_kelimeler.append((kelime, agirlik))
    
    tetiklenen_kelimeler.sort(key=lambda x: abs(x[1]), reverse=True)
    top_3 = tetiklenen_kelimeler[:3]
    
    return {
        "toplam_puan": max(-1.0, min(1.0, toplam_puan)),
        "tetiklenen": top_3,
        "risk_seviyesi": "Yüksek Risk" if toplam_puan < -0.5 else "Orta Risk" if toplam_puan < 0 else "Stabil" if toplam_puan < 0.3 else "Fırsat"
    }

def makro_analiz_yap(haberler: list) -> dict:
    if not haberler or not isinstance(haberler, list):
        return {"durum": "Normal", "neden": "Küresel piyasalar stabil"}
    risk_off_kelimeler = ["war", "conflict", "sanctions", "strike", "missile", "tension", "fed", "inflation", "crash", "emergency", "savaş", "çatışma", "yaptırım"]
    
    risk_off = False
    tetikleyen = ""
    for h in haberler:
        if not isinstance(h, str):
            continue
        hl = h.lower()
        for rk in risk_off_kelimeler:
            if rk in hl:
                risk_off = True
                tetikleyen = rk
                break
        if risk_off: break
        
    if risk_off: return {"durum": "Risk-Off", "neden": f"Makro Risk Algılandı ({tetikleyen.upper()})"}
    return {"durum": "Normal", "neden": "Küresel piyasalar stabil"}

def twitter_etkisi_puanla(sembol: str) -> dict:
    coin_adi = sembol.split('/')[0]
    influencer_tweettleri = [
        {"yazar": "Elon Musk", "tweet": f"Thinking about buying more {coin_adi} 🚀. To the moon!", "skor": 0.5},
        {"yazar": "Elon Musk", "tweet": f"{coin_adi} seems overvalued right now. Be careful. 📉", "skor": -0.5},
        {"yazar": "Michael Saylor", "tweet": f"{coin_adi} is digital energy. HODL forever.", "skor": 0.4},
        {"yazar": "Whale Alert", "tweet": f"🚨 10,000,000 {coin_adi} transferred to unknown wallet.", "skor": 0.3},
        {"yazar": "Whale Alert", "tweet": f"🚨 50,000,000 {coin_adi} transferred to Binance.", "skor": -0.4},
    ]
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
    skor += twitter_skoru  
    return max(-1.0, min(1.0, skor))

def fear_and_greed_simulasyonu() -> dict:
    fg_value = random.randint(10, 90)
    if fg_value < 25: durum = "Extreme Fear"
    elif fg_value < 45: durum = "Fear"
    elif fg_value < 55: durum = "Neutral"
    elif fg_value < 75: durum = "Greed"
    else: durum = "Extreme Greed"
    return {"deger": fg_value, "durum": durum}

# ─────────────────────────────────────────────
# 4) Dinamik Coin Seçimi (Top 20 + Breakout Scanner)
# ─────────────────────────────────────────────
def top_coinleri_tara(exchange, limit=100) -> list:
    standart_liste = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
    yasakli_kelimeler = ["USDC", "FDUSD", "TUSD", "DAI", "EUR", "GBP", "BUSD", "USDP", "TRUE", "PAXG", "USDD", "PYUSD"]
    try:
        if exchange is None:
            return standart_liste
        if not isinstance(getattr(exchange, 'has', None), dict) or not exchange.has.get('fetchTickers'):
            return standart_liste
            
        tickers = exchange.fetch_tickers()
        if not tickers or not isinstance(tickers, dict):
            return standart_liste
            
        usdt_tickers = {}
        for k, v in tickers.items():
            if v is None or not isinstance(v, dict):
                continue
            vol = v.get('quoteVolume', 0)
            if vol is None:
                vol = 0
            if '/USDT' in k and vol > 0:
                is_yasakli = any(yasak in k for yasak in yasakli_kelimeler)
                if not is_yasakli:
                    usdt_tickers[k] = v
        # Hacme göre sırala
        sirali = sorted(usdt_tickers.items(), key=lambda x: (x[1].get('quoteVolume', 0) or 0), reverse=True)
        if not sirali:
            return standart_liste
        return [k for k, v in sirali[:limit]]
    except Exception:
        return standart_liste

def anormallik_tara_ve_sec(exchange, top_coinler, sma_kisa, sma_uzun) -> dict:
    secilen_sembol = None
    en_baskın_mutlak_skor = -1
    secilen_pazar = None
    secilen_sma = None
    secilen_breakout = False
    secilen_rapor = ""
    
    taranan_liste = []
    haberler = trend_analizi_yap()
    haber_puanlari = haber_anahtar_kelime_puanla(haberler)

    for coin in top_coinler[:15]:
        try:
            df = mum_verisi_cek(exchange, coin, "1h", limit=sma_uzun+5)
            if df is None or df.empty or len(df) < 15: continue
            
            twitter_verisi = twitter_etkisi_puanla(coin)
            pazar = pazar_durumu_cikar(df, coin, pre_fetched_news=haberler, twitter_verisi=twitter_verisi)
            if not pazar: continue
            
            sma_sinyal = sinyal_uret(df, sma_kisa, sma_uzun)
            
            # Breakout Kontrolü (Hacim Patlaması & Konsolidasyon)
            is_breakout = False
            son_hacim = df['volume'].iloc[-1]
            ortalama_hacim = df['volume'].iloc[-14:-1].mean()
            son_mum = df.iloc[-1]
            fiyat_farki_pct = ((son_mum['high'] - son_mum['low']) / son_mum['low']) * 100
            hacim_artis_pct = ((son_hacim - ortalama_hacim) / ortalama_hacim * 100) if ortalama_hacim > 0 else 0
            
            # Son 1 saatteki hacim, 24 saatlik ortalamanın en az %200 üzerinde olmalı
            if hacim_artis_pct >= 200 and fiyat_farki_pct < 3.0:
                is_breakout = True
            elif son_hacim > (ortalama_hacim * 1.5) and fiyat_farki_pct < 3.0:
                is_breakout = True  # Daha düşük hacim artışı ama yine de dikkat çekici
            pazar['is_breakout'] = is_breakout
                
            skor = kompozit_skor_hesapla(pazar, sma_sinyal)
            
            taranan_liste.append({
                "Sembol": coin, 
                "Fiyat": pazar["fiyat"], 
                "Skor": round(skor,1),
                "Volatilite": round(pazar["volatilite"], 2),
                "Hacim Artışı": f"%{hacim_artis_pct:.0f}",
                "Breakout": "🔥 EVET" if is_breakout else "HAYIR"
            })
            
            # Volatilite * Breakout puanı (Hacimli ama ölü USDC gibi coinleri eler)
            mutlak_guc = abs(skor) + (pazar["volatilite"] * 2) 
            if is_breakout: mutlak_guc += 50
            if pazar["volatilite"] < 0.5: mutlak_guc -= 100 # Sabit coin cezası
            
            # Haber puanını da ekle
            mutlak_guc += abs(haber_puanlari["toplam_puan"]) * 10
            
            if mutlak_guc > en_baskın_mutlak_skor:
                en_baskın_mutlak_skor = mutlak_guc
                secilen_sembol = coin
                secilen_pazar = pazar
                secilen_sma = sma_sinyal
                secilen_breakout = is_breakout
                
                # Şeffaf Karar Raporu Oluştur
                tw_data = pazar.get('twitter', {}) or {}
                tw_durum = f"Twitter: {tw_data.get('yazar', '?')} ({tw_data.get('skor', 0):+.1f})" if tw_data.get('aktif') else "Twitter: Etkisiz"
                h_tetiklenen = haber_puanlari.get("tetiklenen", []) or []
                haber_ozet = ", ".join([f"{k.upper()}({a:+.1f})" for k, a in h_tetiklenen]) if h_tetiklenen else "Belirgin gündem yok"
                makro_data = pazar.get('makro', {}) or {}
                secilen_rapor = (
                    f"SEÇİLEN COİN: {coin}\n"
                    f"TEKNİK: RSI: {pazar.get('rsi', 50):.1f}, Hacim Artışı: %{hacim_artis_pct:.0f}, Breakout: {'Evet' if is_breakout else 'Hayır'}\n"
                    f"GÜNDEM: {haber_ozet}. {tw_durum}. Duyarlılık: {pazar.get('duyarlilik', 0):+.2f}\n"
                    f"MAKRO: {makro_data.get('durum', 'Normal')} - {makro_data.get('neden', '')}"
                )
                
        except Exception:
            continue

    return {
        "secilen_sembol": secilen_sembol,
        "secilen_pazar": secilen_pazar,
        "secilen_sma": secilen_sma,
        "secilen_breakout": secilen_breakout,
        "taranan_liste": taranan_liste,
        "karar_raporu": secilen_rapor,
        "haber_puanlari": haber_puanlari
    }


# ─────────────────────────────────────────────
# 5) Komplike Karar Motoru & Yapay Zeka
# ─────────────────────────────────────────────
def pazar_durumu_cikar(df: pd.DataFrame, sembol: str, pre_fetched_news=None, twitter_verisi=None) -> dict:
    if df is None or df.empty or len(df) < 3:
        return None
    try:
        rsi = rsi_hesapla(df)
        vol_pct = volatilite_hesapla(df)
        kisa_hacim = df['volume'].iloc[-3:].mean()
        uzun_hacim = df['volume'].iloc[-14:].mean() if len(df) >= 14 else kisa_hacim
        hacim_artiyor = kisa_hacim > uzun_hacim
        
        haberler = pre_fetched_news if pre_fetched_news else trend_analizi_yap()
        tw_veri = twitter_verisi if twitter_verisi else twitter_etkisi_puanla(sembol)
        if not isinstance(tw_veri, dict):
            tw_veri = {"aktif": False, "skor": 0.0}
        duyarlilik = duyarlilik_puanla(haberler, sembol, tw_veri.get("skor", 0.0))
        makro = makro_analiz_yap(haberler)
        
        son_fiyat = df['close'].iloc[-1]
        if pd.isna(son_fiyat) or son_fiyat is None:
            return None
        
        return {
            "rsi": rsi,
            "volatilite": vol_pct,
            "hacim_trend": "Artıyor" if hacim_artiyor else "Düşüyor",
            "duyarlilik": duyarlilik,
            "twitter": tw_veri,
            "fiyat": float(son_fiyat),
            "is_breakout": False,
            "fg_index": fear_and_greed_simulasyonu(),
            "makro": makro
        }
    except Exception:
        return None

def kompozit_skor_hesapla(pazar: dict, sma_sinyal: str) -> float:
    if not isinstance(pazar, dict):
        return 0.0
    skor = 0.0
    if sma_sinyal == "AL": skor += 30
    elif sma_sinyal == "SAT": skor -= 30
    
    rsi = pazar.get("rsi", 50.0) or 50.0
    if rsi < 30: skor += 25
    elif rsi > 70: skor -= 25
    elif rsi > 50: skor += 5
    else: skor -= 5
        
    skor += (pazar.get("duyarlilik", 0) or 0) * 20
    if pazar.get("hacim_trend") == "Artıyor": skor += 15 if skor > 0 else -15
        
    vol = pazar.get("volatilite", 0) or 0
    vol_etki = min(vol, 5.0) * 2
    skor += vol_etki if skor > 0 else -vol_etki
    
    if pazar.get("is_breakout"): skor += 20 if skor > 0 else -20
        
    return max(-100.0, min(100.0, skor))

def ai_metrikler(pazar: dict, kompozit_skor: float, zaman_baski_carpani: float = 1.0) -> tuple:
    if not isinstance(pazar, dict):
        return 0.0, 0.0, 10, 0.10
    vol = pazar.get("volatilite", 0) or 0
    guven = min(100.0, abs(kompozit_skor) * 0.8 + vol * 2)
    if pazar.get("is_breakout"): guven = min(100.0, guven + 15)
    
    beklenen = vol * 1.5
    if pazar.get("is_breakout"): beklenen *= 2.5
    if kompozit_skor < 0: beklenen = -beklenen
    
    tavsiye_kaldirac = 10
    tavsiye_oran = 0.10
    
    if guven > 90:
        tavsiye_kaldirac = random.randint(30, 50)
        tavsiye_oran = 0.40
    elif guven > 75:
        tavsiye_kaldirac = random.randint(20, 30)
        tavsiye_oran = 0.20
    elif guven > 60:
        tavsiye_kaldirac = random.randint(10, 20)
        tavsiye_oran = 0.10
    else:
        tavsiye_kaldirac = random.randint(2, 10)
        tavsiye_oran = 0.05
        
    if zaman_baski_carpani > 1.0:
        tavsiye_kaldirac = int(tavsiye_kaldirac * zaman_baski_carpani)
        tavsiye_oran = tavsiye_oran * zaman_baski_carpani
        
    tavsiye_kaldirac = min(tavsiye_kaldirac, 50)
    tavsiye_oran = min(tavsiye_oran, 0.50 if zaman_baski_carpani >= 4.0 else 0.40)

    if vol > 10.0 and zaman_baski_carpani <= 1.2: 
        tavsiye_kaldirac = min(tavsiye_kaldirac, 10)
    
    return guven, beklenen, tavsiye_kaldirac, tavsiye_oran

def mock_ai_karar(sembol: str, pazar: dict, kompozit_skor: float, acik_pozisyon: str, btc_trendi: str, fonlama: dict, zaman_baski_carpani: float = 1.0) -> dict:
    if not isinstance(pazar, dict):
        return {"sembol": sembol, "karar": "BEKLE", "skor": 0, "dusunce": "Pazar verisi yok", "aralik_sn": 30, "guven_skoru": 0, "expected_growth": 0, "tavsiye_kaldirac": 10, "tavsiye_oran": 0.10, "ozet": "Veri yok"}
    if not isinstance(fonlama, dict):
        fonlama = {"oran": 0.0, "risk": "Yok"}
    guven, beklenen_artis, kaldirac, oran = ai_metrikler(pazar, kompozit_skor, zaman_baski_carpani)
    
    karar = "BEKLE"
    neden = f"Piyasa kararsız (Skor: {kompozit_skor:.1f}). Kesin kırılım yok."
    tw_data = pazar.get('twitter', {}) or {}
    twitter_msg = f" 🐦 [{tw_data.get('yazar', '?')}: {tw_data.get('skor', 0):+.1f} Etki]" if tw_data.get('aktif') else ""
    fg = pazar.get("fg_index") or {"deger": 50, "durum": "Neutral"}
    fg_korku_var_mi = fg.get("durum", "Neutral") in ["Fear", "Extreme Fear"]
    makro = pazar.get("makro") or {"durum": "Normal", "neden": ""}

    if makro.get("durum") == "Risk-Off":
        if acik_pozisyon == "LONG":
            karar = "KAPAT"
            neden = f"🚨 ACİL (Risk-Off): {makro.get('neden', '')}. Güvenli limana geçiş, LONG pozisyon hemen kapatılıyor."
        elif acik_pozisyon == "YOK" and kompozit_skor < -10:
            karar = "SHORT"
            neden = f"🚨 MAKRO FIRSAT: {makro.get('neden', '')} tespit edildi + zayıf trend. Küresel panik kaynaklı güçlü SHORT!"
        elif acik_pozisyon == "SHORT":
            neden = f"🚨 Makro gerginlik ({makro.get('neden', '')}) SHORT pozisyonumuz için lehimize. Tutmaya devam ediyoruz."
    elif pazar.get("is_breakout") and fg_korku_var_mi and acik_pozisyon != "LONG" and not (btc_trendi == "AŞAĞI"):
        karar = "LONG"
        neden = f"🚀 KORKUYU SATIN AL: Piyasada Aşırı Korku ({fg.get('deger', 50)} - {fg.get('durum', 'N/A')}) varken hacim patlaması (Breakout) yakalandı! Güçlü AL sinyali."
    elif kompozit_skor > 40:
        if acik_pozisyon == "SHORT": 
            karar = "KAPAT"
            neden = f"Trend YUKARI döndü! SHORT pozisyon riske girdi, acil kapatılıyor (Skor: {kompozit_skor:.1f})."
        else:
            if btc_trendi == "AŞAĞI":
                neden = f"LONG fırsatı vardı fakt BTC Trendi AŞAĞI olduğu için İPTAL edildi. Güvenlik öncelikli."
            elif "Uzun" in fonlama.get("risk", ""):
                neden = f"LONG fırsatı vardı fakat Fonlama Oranı aşırı yüksek ({fonlama.get('oran', 0):.2f}%). Likidasyon/Maliyet riski nedeniyle işlem askıda."
            else:
                karar = "LONG"
                neden = f"Güçlü YÜKSELİŞ Beklentisi! {sembol} kompozit skoru {kompozit_skor:.1f}. RSI ({pazar.get('rsi', 50):.1f}).{twitter_msg}"
                if pazar.get("is_breakout"): neden = "🚀 ACİL LONG (BREAKOUT)! Hacim patlaması tespit edildi. " + neden
            
    elif kompozit_skor < -40:
        if acik_pozisyon == "LONG":
            karar = "KAPAT"
            neden = f"Trend AŞAĞI döndü! LONG pozisyon terse düştü, acil kapatılıyor (Skor: {kompozit_skor:.1f})."
        else:
            if btc_trendi == "YUKARI":
                neden = f"SHORT fırsatı vardı fakat BTC Trendi YUKARI olduğu için İPTAL edildi. Güvenlik öncelikli."
            elif "Kısa" in fonlama.get("risk", ""):
                neden = f"SHORT fırsatı vardı fakat negatif Fonlama Oranı aşırı yüksek ({fonlama.get('oran', 0):.2f}%). Pozisyon açılmadı."
            else:
                karar = "SHORT"
                neden = f"Güçlü DÜŞÜŞ Beklentisi! {sembol} zayıflık gösteriyor (Skor: {kompozit_skor:.1f}).{twitter_msg}"
                if pazar.get("is_breakout"): neden = "📉 ACİL SHORT (CRASH)! Aşağı yönlü hacim patlaması tespit edildi. " + neden

    vol = pazar.get("volatilite", 0) or 0
    sonraki_sn = dinamik_analiz_araligi(vol, pazar.get("is_breakout", False))
    
    return {
        "sembol": sembol,
        "karar": karar,
        "skor": kompozit_skor,
        "dusunce": neden,
        "aralik_sn": sonraki_sn,
        "guven_skoru": guven,
        "expected_growth": beklenen_artis,
        "tavsiye_kaldirac": kaldirac,
        "tavsiye_oran": oran,
        "ozet": f"BTC: {btc_trendi} | Fonlama: {fonlama.get('oran', 0):.3f}% | Time-Pr: {zaman_baski_carpani:.2f}"
    }

def llm_karar(sembol: str, pazar: dict, sma_sinyal: str, api_key: str, acik_pozisyon: str, btc_trendi: str, fonlama: dict, zaman_baski_carpani: float = 1.0) -> dict:
    import openai
    client = openai.OpenAI(api_key=api_key)
    
    komp_skor = kompozit_skor_hesapla(pazar, sma_sinyal)
    guven, beklenen_artis, kaldirac, oran = ai_metrikler(pazar, komp_skor, zaman_baski_carpani)
    breakout_str = "EVET" if pazar.get("is_breakout") else "HAYIR"
    tw_str = pazar.get('twitter', {}).get('tweet', 'Yok')
    
    makro = pazar.get("makro", {"durum": "Normal", "neden": ""})
    
    prompt = f"""
    Sen usta bir kripto Furtures (Vadeli İşlem) botusun.
    Mevcut Açık Pozisyon: {acik_pozisyon} ("YOK", "LONG", veya "SHORT" olabilir).
    BTC Genel Trendi: {btc_trendi} (Ana piyasa yönü, buna göre risk al).
    Fonlama Oranı Risk Durumu: {fonlama['risk']} (Oran: {fonlama['oran']:.3f}%)
    Fear & Greed Index: {pazar.get('fg_index', {}).get('durum', 'Neutral')} ({pazar.get('fg_index', {}).get('deger', 50)})
    Makro Risk Durumu: {makro['durum']} (Sebep: {makro['neden']})
    Zaman Baskisi Çarpanı: {zaman_baski_carpani:.2f} (>1.0 ise agresifliği artır, hedefe az zaman kaldı!)
    
    {sembol} coini için 'LONG', 'SHORT', 'KAPAT' veya 'BEKLE' kararı ver.
    Veriler: Fiyat: {pazar['fiyat']}, SMA Sinyali: {sma_sinyal}, RSI: {pazar['rsi']:.2f}, Vol: %{pazar['volatilite']:.2f}, Trend: {pazar['hacim_trend']}, Breakout: {breakout_str}
    Sosyal Trend Skoru: {pazar['duyarlilik']:.2f}
    Eğer piyasa aşırı korkuda ('Fear' veya 'Extreme Fear') ve Breakout 'EVET' ise, bunu çok güçlü bir 'LONG' sinyali olarak değerlendir.
    Eğer Makro Risk Durumu 'Risk-Off' ise ('War', 'Sanctions' vb. sebeplerle), güvenli limana kaçış vardır, LONG kesinlikle kapatılmalı ve gerekirse SHORT açılmalıdır.
    
    YANIT FORMATI:
    Karar: [LONG/SHORT/KAPAT/BEKLE]
    Kaldirac: [Beklenen kaldirac 1-50]
    Oran: [Beklenen risk orani 0.05-0.50]
    Neden: [1 cümle net açıklırma - Makro ve teknik gerekçeleri birleştirerek yaz]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3
        )
        cevap = response.choices[0].message.content
        lines = cevap.strip().split('\n')
        karar = "BEKLE"
        neden = cevap
        llm_kaldirac = kaldirac
        llm_oran = oran
        
        for l in lines:
            if l.startswith("Karar:"):
                k = l.split("Karar:")[1].strip().upper()
                if "LONG" in k: karar = "LONG"
                elif "SHORT" in k: karar = "SHORT"
                elif "KAPAT" in k: karar = "KAPAT"
            if l.startswith("Kaldirac:"):
                try: llm_kaldirac = int(l.split("Kaldirac:")[1].strip())
                except: pass
            if l.startswith("Oran:"):
                try: llm_oran = float(l.split("Oran:")[1].strip())
                except: pass
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
            "tavsiye_kaldirac": llm_kaldirac,
            "tavsiye_oran": llm_oran,
            "ozet": f"LLM | BTC: {btc_trendi} | Fonlama: {fonlama['risk']}"
        }
    except Exception as e:
        print(f"LLM hatası: {e}. Mock AI'ye dönülüyor.")
        return mock_ai_karar(sembol, pazar, komp_skor, acik_pozisyon, btc_trendi, fonlama)


# ─────────────────────────────────────────────
# 7) Grid Trading Modülü (Destek/Direnç Grid)
# ─────────────────────────────────────────────
def grid_destek_direnc(df: pd.DataFrame) -> dict:
    """Son 24 mumdan destek/direnç seviyelerini hesaplar."""
    bos_grid = {"destek": 0, "direnc": 0, "aralik_pct": 0, "yatay_mi": False, "grid_seviyeleri": [], "grid_uygun": False}
    if df is None or df.empty or len(df) < 2:
        return bos_grid
    try:
        son_24 = df.iloc[-24:] if len(df) >= 24 else df
        destek = float(son_24['low'].min())
        direnc = float(son_24['high'].max())
        fiyat = float(df['close'].iloc[-1])
        if pd.isna(fiyat) or fiyat <= 0:
            return bos_grid
        aralik = direnc - destek
        
        yatay_mi = (aralik / fiyat * 100) < 5.0 if fiyat > 0 else False
        
        grid_seviyeleri = []
        if aralik > 0:
            adim = aralik / 6
            for i in range(1, 6):
                seviye = destek + (adim * i)
                tip = "AL" if seviye < fiyat else "SAT"
                grid_seviyeleri.append({"fiyat": round(seviye, 4), "tip": tip})
        
        return {
            "destek": round(destek, 4),
            "direnc": round(direnc, 4),
            "aralik_pct": round(aralik / fiyat * 100, 2) if fiyat > 0 else 0,
            "yatay_mi": yatay_mi,
            "grid_seviyeleri": grid_seviyeleri,
            "grid_uygun": yatay_mi and aralik > 0
        }
    except Exception:
        return bos_grid


# ─────────────────────────────────────────────
# 8) Multi-Timeframe Analiz (5dk + 15dk + 1s)
# ─────────────────────────────────────────────
def multi_timeframe_analiz(exchange, sembol: str) -> dict:
    """3 zaman dilimini sentezleyerek güçlü sinyal üretir."""
    varsayilan = {"rsi": 50.0, "volatilite": 0.0, "sinyal": "BEKLE"}
    sonuclar = {}
    for tf, label in [("5m", "5dk"), ("15m", "15dk"), ("1h", "1s")]:
        try:
            df = mum_verisi_cek(exchange, sembol, tf, limit=30)
            if df is None or df.empty or len(df) < 15:
                sonuclar[label] = varsayilan.copy()
                continue
            rsi = rsi_hesapla(df)
            vol = volatilite_hesapla(df)
            sinyal = sinyal_uret(df, 7, 14)
            sonuclar[label] = {"rsi": round(rsi, 1), "volatilite": round(vol, 2), "sinyal": sinyal}
        except Exception:
            sonuclar[label] = varsayilan.copy()
    
    # Konsensüs hesapla
    sinyaller = [v.get("sinyal", "BEKLE") for v in sonuclar.values()]
    al_sayisi = sinyaller.count("AL")
    sat_sayisi = sinyaller.count("SAT")
    
    if al_sayisi >= 2: konsensus = "GÜÇLÜ AL"
    elif sat_sayisi >= 2: konsensus = "GÜÇLÜ SAT"
    elif al_sayisi == 1 and sat_sayisi == 0: konsensus = "ZAYIF AL"
    elif sat_sayisi == 1 and al_sayisi == 0: konsensus = "ZAYIF SAT"
    else: konsensus = "KARARSIZ"
    
    # Ortalama RSI
    rsi_listesi = [v.get("rsi", 50.0) for v in sonuclar.values()]
    ort_rsi = sum(rsi_listesi) / max(len(rsi_listesi), 1)
    
    return {
        "detay": sonuclar,
        "konsensus": konsensus,
        "ortalama_rsi": round(ort_rsi, 1),
        "guc": al_sayisi - sat_sayisi
    }


# ─────────────────────────────────────────────
# 9) Dinamik DCA (Dollar Cost Averaging)
# ─────────────────────────────────────────────
def dca_hesapla(pozisyon: dict, guncel_fiyat: float, bakiye: float) -> dict:
    """Martingale DCA: Pozisyon terse düştüğünde kademeli artan miktarlarla ekleme."""
    if not isinstance(pozisyon, dict) or not guncel_fiyat or guncel_fiyat <= 0:
        return {"uygun": False, "ekleme_margin": 0, "yeni_ortalama": 0, "neden": "Geçersiz pozisyon verisi", "dca_sayisi": 0}
    giris = pozisyon.get("giris_fiyati", 0)
    margin = pozisyon.get("islem_margin", 0)
    kaldirac = pozisyon.get("islem_kaldirac", 1)
    liq = pozisyon.get("likidasyon_fiyati", 0)
    tip = pozisyon.get("pozisyon", "YOK")
    dca_sayisi = pozisyon.get("dca_sayisi", 0)
    
    if not giris or giris <= 0 or not margin or kaldirac <= 0:
        return {"uygun": False, "ekleme_margin": 0, "yeni_ortalama": giris, "neden": "Eksik pozisyon verisi", "dca_sayisi": dca_sayisi}
    
    if tip == "LONG":
        pnl_pct = ((guncel_fiyat - giris) / giris) * 100 * kaldirac
        liq_uzaklik = ((guncel_fiyat - liq) / guncel_fiyat) * 100 if guncel_fiyat > 0 else 0
    else:
        pnl_pct = ((giris - guncel_fiyat) / giris) * 100 * kaldirac
        liq_uzaklik = ((liq - guncel_fiyat) / guncel_fiyat) * 100 if guncel_fiyat > 0 else 0
    
    dca_onerisi = {"uygun": False, "ekleme_margin": 0, "yeni_ortalama": giris, "neden": "", "dca_sayisi": dca_sayisi}
    
    if -20 < pnl_pct < -3 and liq_uzaklik > 5.0 and dca_sayisi < 3:
        martingale_carpan = 2 ** dca_sayisi
        temel_ekleme = min(bakiye * 0.08, margin * 0.3)
        ekleme = temel_ekleme * martingale_carpan
        ekleme = min(ekleme, bakiye * 0.30)
        
        if ekleme > 0.3:
            yeni_toplam_margin = margin + ekleme
            yeni_ortalama = (giris * margin + guncel_fiyat * ekleme) / yeni_toplam_margin
            hedef_kar_fiyat = yeni_ortalama * (1.10 if tip == "LONG" else 0.90)
            dca_onerisi = {
                "uygun": True,
                "ekleme_margin": round(ekleme, 2),
                "yeni_ortalama": round(yeni_ortalama, 4),
                "dca_sayisi": dca_sayisi + 1,
                "neden": f"DCA Yapıldı: Fiyat düşüşü %{abs(pnl_pct):.1f}, Giriş Fiyatı Revize: ${yeni_ortalama:.4f}, Hedef Kâr: ${hedef_kar_fiyat:.4f} (Martingale {martingale_carpan}x, DCA #{dca_sayisi+1})"
            }
    elif pnl_pct <= -20:
        dca_onerisi["neden"] = f"Zarar çok derin (%{pnl_pct:.1f}). DCA riskli, pozisyon kapatılmalı."
    elif dca_sayisi >= 3:
        dca_onerisi["neden"] = f"Maksimum DCA sayısına ({dca_sayisi}) ulaşıldı."
    else:
        dca_onerisi["neden"] = "Pozisyon henüz DCA gerektirmiyor."
    
    return dca_onerisi


# ─────────────────────────────────────────────
# 10) Derin NLP Haber Veto Sistemi
# ─────────────────────────────────────────────
def haber_vetosu(haber_puanlari: dict, teknik_karar: str) -> dict:
    """Haber analizi teknik AL sinyalini veto edebilir."""
    puan = haber_puanlari.get("toplam_puan", 0)
    risk = haber_puanlari.get("risk_seviyesi", "Stabil")
    tetiklenenler = haber_puanlari.get("tetiklenen", [])
    
    veto = False
    neden = ""
    
    # Haber çok negatifse ve teknik AL diyorsa → VETO
    if teknik_karar in ["LONG", "AL"] and puan < -0.4:
        veto = True
        kelimeler = ", ".join([k.upper() for k, _ in tetiklenenler[:3]])
        neden = f"🚫 HABER VETOSU: Teknik AL sinyali iptal edildi! Negatif gündem ({kelimeler}). Risk: {risk}."
    
    # Haber pozitifse ve teknik SAT diyorsa → Uyarı (ama veto etme)
    elif teknik_karar in ["SHORT", "SAT"] and puan > 0.5:
        neden = f"⚠️ Teknik SAT sinyali var ama haberler pozitif ({risk}). Dikkatli ol."
    
    return {"veto": veto, "neden": neden, "haber_skoru": puan, "risk_seviyesi": risk}
