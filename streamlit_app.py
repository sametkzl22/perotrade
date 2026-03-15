"""
Kripto Paper-Trading Bot — AI Dashboard v4
==========================================
Pro Live Test Optimizasyonu: Trailing Stop-Loss, BTC Korelasyonu, 
ve Fonlama Oranı filtresi entegre edildi. 
"""

import threading
import time
import csv
import asyncio
from datetime import datetime, timezone

import ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

import ai_engine

st.set_page_config(
    page_title="PeroTrade Pro AI v4",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #0b0c10 0%, #1f2833 100%); color: #c5c6c7; font-family: 'Inter', sans-serif; }
[data-testid="stMetric"] { background: rgba(31, 40, 51, 0.8); border: 1px solid #45a29e; border-radius: 12px; padding: 16px; box-shadow: 0 4px 15px rgba(0,0,0,0.4); transition: transform 0.2s; }
[data-testid="stMetric"]:hover { transform: translateY(-3px); }
[data-testid="stMetricValue"] { color: #66fcf1 !important; font-weight: 700; font-size: 1.8rem !important; }
[data-testid="stSidebar"] { background: #0b0c10; border-right: 1px solid #1f2833; }
.dashboard-header { background: linear-gradient(90deg, rgba(69,162,158,0.2), transparent); border-left: 3px solid #66fcf1; padding: 8px 16px; margin: 16px 0; border-radius: 0 8px 8px 0; color: #fff;}
.status-badge { display: inline-block; padding: 6px 18px; border-radius: 20px; font-weight: bold; }
.status-running { background: linear-gradient(135deg, #00b09b, #96c93d); color: #fff; animation: pulse 2s infinite; }
.status-stopped { background: #434343; color: #ccc; }
.status-breakout { background: linear-gradient(135deg, #FF416C, #FF4B2B); color: #fff; animation: shake 0.5s infinite; }
.status-target { background: #f7971e; color: #fff; }
.ai-log-box { background: rgba(31,40,51,0.5); border-left: 4px solid #66fcf1; padding: 12px; margin-bottom: 8px; border-radius: 4px; font-family: monospace; font-size: 0.9rem;}
.ai-log-breakout { border-left: 4px solid #FF4B2B; background: rgba(255, 75, 43, 0.1); }
.metric-card { background: rgba(31, 40, 51, 0.8); border-radius: 12px; padding: 20px; text-align: center; border: 1px solid #45a29e; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37); backdrop-filter: blur(4px); }
.metric-card h3 { color: #c5c6c7; font-size: 1rem; margin-bottom: 5px; }
.metric-card h1 { color: #66fcf1; font-size: 2.2rem; margin: 0; }
.metric-card p { margin-top: 10px; font-size: 0.85rem; color: #a4a5a6; }
@keyframes pulse { 0%, 100% {opacity: 1;} 50% {opacity: 0.7;} }
@keyframes shake { 0% { transform: translateX(0); } 25% { transform: translateX(-2px); } 50% { transform: translateX(2px); } 75% { transform: translateX(-2px); } 100% { transform: translateX(0); } }
</style>
""", unsafe_allow_html=True)

MOD_PRESETLERI = {
    "⚡ Agresif Mod": {"risk": 1.0, "sma_kisa": 7, "sma_uzun": 25, "aralik_carpan": 0.5},
    "🌱 Soft Kar Modu": {"risk": 0.30, "sma_kisa": 14, "sma_uzun": 50, "aralik_carpan": 1.5},
}

def session_state_baslat():
    default_state = {
        "bot_calisiyor": False,
        "bot_durumu": "Duraklatıldı",
        "bakiye": 10.0,
        "baslangic_bakiye": 10.0,
        "hedef_bakiye": 100.0,
        "pozisyon": "YOK",
        "coin_miktar": 0.0, 
        "giris_fiyati": 0.0, 
        "likidasyon_fiyati": 0.0,
        "islem_margin": 0.0,
        "islem_kaldirac": 1,
        "kademeli_tp_yapildi": False,
        "pik_bakiye": 10.0,
        "max_drawdown": 0.0,
        "ts_aktif": False,              # Trailing Stop
        "trailing_stop_fiyat": 0.0,     # Trailing Stop Level
        "aktif_sembol": "Bekleniyor...",
        "is_breakout": False,
        "islem_gecmisi": [],
        "ai_dusunce_gunlugu": [],
        "taranan_coinler": [],
        "sonraki_analiz_sn": 0,
        
        "fiyat": 0.0,
        "degisim_24s": 0.0,
        "hacim_24s": 0.0,
        "ai_guven_skoru": 0.0,
        "ai_beklenen_artis": 0.0,
        "ai_analiz_ozeti": "Piyasa taranıyor...",
        "btc_trendi": "Taranıyor",     
        "fonlama_orani": 0.0,          
        "fonlama_riski": "Yok",   
        
        "exchange_adi": "binance",
        "mod": "⚡ Agresif Mod",
        "ai_modu": "Mock AI",
        "openai_key": "",
        "kaldirac": 1,
        
        "lock": threading.Lock(),
        "dur_sinyali": threading.Event(),
    }
    for k, v in default_state.items():
        if k not in st.session_state: st.session_state[k] = v

session_state_baslat()

def islem_gecmisi_kaydet(gecmis: list, dosya="trade_history.csv"):
    if not gecmis: return
    headers = ["zaman", "sembol", "sinyal", "fiyat", "kaldirac", "poz_buyukluk", "bakiye_usdt", "kar_zarar", "ai_notu"]
    with open(dosya, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(gecmis)

def log_ekle(mesaj: str, state, is_breakout=False, is_liq=False):
    zaman = datetime.now(timezone.utc).strftime("%H:%M:%S")
    state["ai_dusunce_gunlugu"].insert(0, {"time": zaman, "msg": mesaj, "breakout": is_breakout, "liq": is_liq})
    if len(state["ai_dusunce_gunlugu"]) > 60: state["ai_dusunce_gunlugu"].pop()

def pnl_hesapla(pozisyon, giris, anlik, miktar, kaldirac) -> float:
    if pozisyon == "YOK" or giris == 0: return 0.0
    margin = miktar / kaldirac
    if pozisyon == "LONG": pnl_pct = ((anlik - giris) / giris)
    else: pnl_pct = ((giris - anlik) / giris)
    return margin * pnl_pct * kaldirac

def likidasyon_hesapla(pozisyon, giris, kaldirac) -> float:
    if pozisyon == "YOK" or giris == 0: return 0.0
    if pozisyon == "LONG": return giris * (1 - (1 / kaldirac))
    elif pozisyon == "SHORT": return giris * (1 + (1 / kaldirac))
    return 0.0

# ─────────────────────────────────────────────
# AI Bot Engine & WebSocket Dinleyici
# ─────────────────────────────────────────────
def islem_kapat(state, fiyat, neden, is_breakout=False, is_liq=False):
    eski_poz = state["pozisyon"]
    if eski_poz == "YOK": return

    margin = state["islem_margin"]
    kaldirac = state["islem_kaldirac"]
    aktif_pnl = pnl_hesapla(eski_poz, state["giris_fiyati"], fiyat, margin * kaldirac, kaldirac)

    reel_getiri = margin + aktif_pnl
    state["bakiye"] += reel_getiri
    
    state["pozisyon"] = "YOK"
    state["coin_miktar"] = state["giris_fiyati"] = state["likidasyon_fiyati"] = state["ts_aktif"] = state["trailing_stop_fiyat"] = state["islem_margin"] = 0.0
    state["kademeli_tp_yapildi"] = False
    
    kz_str = f"{aktif_pnl:+.2f} USDT"
    icon = "☠️" if is_liq else "🛡️" if "TS" in neden else "🔴"
    
    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    state["islem_gecmisi"].append({
        "zaman": zaman, "sembol": state["aktif_sembol"], "sinyal": f"{icon} KAPAT: {eski_poz}", 
        "fiyat": round(fiyat, 4), "kaldirac": f"{kaldirac}x", "poz_buyukluk": 0, 
        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": kz_str, "ai_notu": neden
    })
    log_ekle(f"{icon} POZİSYON KAPATILDI: {state['aktif_sembol']} {eski_poz}. PNL: {kz_str}", state, is_breakout, is_liq)

def ws_fiyat_dinleyici(state, lock, dur_sinyali):
    async def dinle():
        try: exchange = getattr(ccxtpro, state["exchange_adi"])({"enableRateLimit": True})
        except: return
            
        while not dur_sinyali.is_set():
            try:
                sembol = state["aktif_sembol"]
                fiyat_eski = state["fiyat"]
                if sembol and sembol != "Bekleniyor...":
                    ticker = await asyncio.wait_for(exchange.watch_ticker(sembol), timeout=5.0)
                    with lock:
                        fiyat = ticker.get("last", state["fiyat"])
                        degisim = ticker.get("percentage", state["degisim_24s"])
                        hacim = ticker.get("quoteVolume", state["hacim_24s"])
                        state["fiyat"] = fiyat
                        if degisim: state["degisim_24s"] = degisim
                        if hacim: state["hacim_24s"] = hacim
                        
                        # --- ANLIK RİSK & POZİSYON KONTROLÜ (WS TICK) ---
                        if state["pozisyon"] != "YOK" and state["islem_margin"] > 0:
                            is_long = state["pozisyon"] == "LONG"
                            is_short = state["pozisyon"] == "SHORT"
                            liq_price = state["likidasyon_fiyati"]
                            
                            aktif_pnl = pnl_hesapla(state["pozisyon"], state["giris_fiyati"], fiyat, state["islem_margin"] * state["islem_kaldirac"], state["islem_kaldirac"])
                            pnl_pct = (aktif_pnl / state["islem_margin"]) * 100
                            
                            # 1. Likidasyon Kontrolü
                            if (is_long and fiyat <= liq_price) or (is_short and fiyat >= liq_price):
                                islem_kapat(state, fiyat, "Liquidation", is_liq=True)
                                if state["bakiye"] <= 0:
                                    state["bot_durumu"] = "💀 İflas"
                                    state["bot_calisiyor"] = False
                                    dur_sinyali.set()
                            
                            elif state["pozisyon"] != "YOK": # Liq olmadıysa
                                # 2. %10 Kademeli TP %50 Kapat ve TS Breakeven
                                if pnl_pct >= 10.0 and not state.get("kademeli_tp_yapildi", False):
                                    state["kademeli_tp_yapildi"] = True
                                    real_pnl = aktif_pnl / 2
                                    ret_margin = state["islem_margin"] / 2
                                    state["bakiye"] += (ret_margin + real_pnl)
                                    state["islem_margin"] /= 2
                                    state["coin_miktar"] /= 2
                                    
                                    state["ts_aktif"] = True
                                    state["trailing_stop_fiyat"] = state["giris_fiyati"]
                                    
                                    z = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                                    state["islem_gecmisi"].append({
                                        "zaman": z, "sembol": sembol, "sinyal": "💰 %50 TP", 
                                        "fiyat": round(fiyat, 4), "kaldirac": f"{state['islem_kaldirac']}x", 
                                        "poz_buyukluk": round(state["coin_miktar"], 2), 
                                        "bakiye_usdt": round(state["bakiye"], 2), 
                                        "kar_zarar": f"{real_pnl:+.2f} USDT", "ai_notu": "%10 ROE: %50 Kâr Alındı, TS Başabaş."
                                    })
                                    log_ekle(f"💰 %10 ROE Tespiti: {sembol} pozisyonun yarısı kapatıldı. TS giriş fiyatına çekildi.", state, is_breakout=True)
                                    
                                # 3. Normal TS Update
                                if state["ts_aktif"]:
                                    ts_hit = False
                                    if is_long:
                                        if pnl_pct >= 5.0 and not state.get("kademeli_tp_yapildi", False):
                                            yeni_ts = fiyat * 0.98
                                            if yeni_ts > state["trailing_stop_fiyat"]: state["trailing_stop_fiyat"] = yeni_ts
                                        if fiyat <= state["trailing_stop_fiyat"]: ts_hit = True
                                    else:
                                        if pnl_pct >= 5.0 and not state.get("kademeli_tp_yapildi", False):
                                            yeni_ts = fiyat * 1.02
                                            if yeni_ts < state["trailing_stop_fiyat"]: state["trailing_stop_fiyat"] = yeni_ts
                                        if fiyat >= state["trailing_stop_fiyat"]: ts_hit = True
                                        
                                    if ts_hit:
                                        islem_kapat(state, fiyat, "🛡️ TS KAPAT - İz Süren Stop")
                        
                        # --- DRAWDOWN TRACKER ---
                        aktif = pnl_hesapla(state["pozisyon"], state["giris_fiyati"], fiyat, state["islem_margin"] * state["islem_kaldirac"] if state["pozisyon"] != "YOK" else 0, state["islem_kaldirac"])
                        toplam = state["bakiye"] + (state["islem_margin"] if state["pozisyon"] != "YOK" else 0) + aktif
                        
                        if toplam > state["pik_bakiye"]: state["pik_bakiye"] = toplam
                        elif state["pik_bakiye"] > 0:
                            dd = (state["pik_bakiye"] - toplam) / state["pik_bakiye"] * 100
                            if dd > state["max_drawdown"]: state["max_drawdown"] = dd

                else:
                    await asyncio.sleep(0.5)
            except asyncio.TimeoutError: pass
            except Exception as e:
                await asyncio.sleep(1)
        try: await exchange.close()
        except: pass
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dinle())

def bot_engine(state, lock: threading.Lock, dur_sinyali: threading.Event):
    exchange = getattr(ccxt, state["exchange_adi"])({"enableRateLimit": True})
    
    while not dur_sinyali.is_set():
        try:
            preset = MOD_PRESETLERI[state["mod"]]
            kaldirac = state["kaldirac"]
            
            # --- 1. AŞAMA: TARA VE CANLI VERİ ENTEGRASYONU ---
            with lock:
                poz = state["pozisyon"]
                if poz == "YOK": log_ekle("🔍 Live Test: Breakout, BTC Trendi ve Fonlama verileri sentezleniyor...", state)
            
            btc_trend = ai_engine.btc_trendi_analiz_et(exchange)
            
            if poz == "YOK":
                top_coinler = ai_engine.top_coinleri_tara(exchange, limit=30)
                tarama_sonucu = ai_engine.anormallik_tara_ve_sec(exchange, top_coinler, preset["sma_kisa"], preset["sma_uzun"])
                
                secilen_sembol = tarama_sonucu["secilen_sembol"] or "BTC/USDT"
                secilen_pazar = tarama_sonucu.get("secilen_pazar", {})
                secilen_sma = tarama_sonucu.get("secilen_sma", "BEKLE")
                is_breakout = tarama_sonucu.get("secilen_breakout", False)
                
                with lock:
                    state["taranan_coinler"] = tarama_sonucu.get("taranan_liste", [])
                    state["aktif_sembol"] = secilen_sembol
                    state["is_breakout"] = is_breakout
                    if is_breakout:
                        state["bot_durumu"] = "🚨 Breakout Modu!"
                        log_ekle(f"🔥 HACİM PATLAMASI: {secilen_sembol} (Hız 5s->2s)", state, is_breakout=True)
                    else: state["bot_durumu"] = "Çalışıyor"
            else:
                secilen_sembol = state["aktif_sembol"]
                df = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "1h", limit=preset["sma_uzun"]+5)
                secilen_pazar = ai_engine.pazar_durumu_cikar(df, secilen_sembol)
                secilen_sma = ai_engine.sinyal_uret(df, preset["sma_kisa"], preset["sma_uzun"])
                is_breakout = False 
            
            try:
                ticker = exchange.fetch_ticker(secilen_sembol)
                fiyat, degisim, hacim = ticker.get("last", secilen_pazar.get("fiyat", 0)), ticker.get("percentage", 0), ticker.get("quoteVolume", 0)
            except Exception:
                fiyat, degisim, hacim = secilen_pazar.get("fiyat", 0), 0, 0
                
            fonlama = ai_engine.fonlama_orani_getir(exchange, secilen_sembol)
                
            # --- 2. AŞAMA: DURUM KONTROLÜ (RİSK WS TARAFINDAN YÖNETİLİYOR) ---
            pozisyonu_kapat = False
            kapat_sinyali_nedeni = ""
            
            with lock:
                state["btc_trendi"] = btc_trend
                state["fonlama_orani"] = fonlama["oran"]
                state["fonlama_riski"] = fonlama["risk"]

            if dur_sinyali.is_set(): break

            # --- 3. AŞAMA: YAPAY ZEKA TAHMİNİ ---
            # Eger trailing stop vurduysa LLM beklemeden direk kapatsin
            karar_paketi = {"karar": "BEKLE", "dusunce": kapat_sinyali_nedeni, "aralik_sn": 5}
            if not pozisyonu_kapat:
                if state["ai_modu"] == "OpenAI LLM" and state["openai_key"]:
                    karar_paketi = ai_engine.llm_karar(secilen_sembol, secilen_pazar, secilen_sma, state["openai_key"], state["pozisyon"], btc_trend, fonlama)
                else:
                    skor = ai_engine.kompozit_skor_hesapla(secilen_pazar, secilen_sma)
                    karar_paketi = ai_engine.mock_ai_karar(secilen_sembol, secilen_pazar, skor, state["pozisyon"], btc_trend, fonlama)
            else:
                karar_paketi["karar"] = "KAPAT"
                
            # --- 4. AŞAMA: UI & TRADE PROCESS ---
            with lock:
                state["fiyat"] = fiyat
                state["degisim_24s"] = degisim
                state["hacim_24s"] = hacim
                state["ai_guven_skoru"] = karar_paketi.get("guven_skoru", 0.0)
                state["ai_beklenen_artis"] = karar_paketi.get("expected_growth", 0.0)
                state["ai_analiz_ozeti"] = karar_paketi.get("ozet", kapat_sinyali_nedeni)
                
                if not pozisyonu_kapat:
                    log_ekle(f"🎯 {secilen_sembol} Analizi: {karar_paketi['dusunce']}", state, is_breakout=is_breakout)
                
                sinyal = karar_paketi["karar"]
                zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                
                if (sinyal == "LONG" or sinyal == "SHORT") and state["pozisyon"] == "YOK":
                    margin = state["bakiye"] * preset["risk"]
                    buyukluk_usdt = margin * kaldirac
                    
                    state["islem_margin"] = margin
                    state["islem_kaldirac"] = kaldirac
                    state["kademeli_tp_yapildi"] = False
                    
                    state["coin_miktar"] = buyukluk_usdt
                    state["bakiye"] -= margin
                    state["pozisyon"] = sinyal
                    state["giris_fiyati"] = fiyat
                    state["likidasyon_fiyati"] = likidasyon_hesapla(sinyal, fiyat, kaldirac)
                    
                    state["islem_gecmisi"].append({
                        "zaman": zaman, "sembol": secilen_sembol, "sinyal": f"🟢 AÇ: {sinyal}", 
                        "fiyat": round(fiyat, 4), "kaldirac": f"{kaldirac}x", "poz_buyukluk": round(buyukluk_usdt, 2), 
                        "bakiye_usdt": round(state["bakiye"] + margin, 2), "kar_zarar": "—", "ai_notu": karar_paketi["dusunce"]
                    })
                    log_ekle(f"💰 {kaldirac}x {sinyal} POZİSYON AÇILDI: {secilen_sembol}. Giriş: {fiyat:.4f}, Liq: {state['likidasyon_fiyati']:.4f}", state, is_breakout)
                    
                elif sinyal == "KAPAT" and state["pozisyon"] != "YOK":
                    islem_kapat(state, fiyat, karar_paketi["dusunce"])
                
                if state["pozisyon"] != "YOK":
                    aktif_pnl = pnl_hesapla(state["pozisyon"], state["giris_fiyati"], fiyat, state["islem_margin"] * state["islem_kaldirac"], state["islem_kaldirac"])
                    toplam_varlik = state["bakiye"] + state["islem_margin"] + aktif_pnl
                else: toplam_varlik = state["bakiye"]
                
                if toplam_varlik >= state["hedef_bakiye"]:
                    state["bot_durumu"] = "🎯 Hedefi Ulaştı!"
                    state["bot_calisiyor"] = False
                    log_ekle("🏆 HEDEF ULAŞILDI! Bot durduruluyor.", state)
                    islem_gecmisi_kaydet(state["islem_gecmisi"])
                    dur_sinyali.set()
                    break

            # --- 5. AŞAMA: BEKLEME ---
            bekleme_suresi = int(karar_paketi["aralik_sn"])
            if not is_breakout: bekleme_suresi = int(bekleme_suresi * preset["aralik_carpan"])
            with lock: state["sonraki_analiz_sn"] = bekleme_suresi
            
            for _ in range(bekleme_suresi):
                if dur_sinyali.is_set(): return
                time.sleep(1)
                with lock: state["sonraki_analiz_sn"] -= 1

        except Exception as e:
            with lock: log_ekle(f"❌ Hata: {str(e)}", state)
            time.sleep(5)


# ─────────────────────────────────────────────
# Uygulama Arayüzü (UI)
# ─────────────────────────────────────────────
def baslat():
    if not st.session_state.bot_calisiyor:
        st.session_state.dur_sinyali.clear()
        st.session_state.bot_calisiyor = True
        st.session_state.bot_durumu = "Çalışıyor"
        
        t1 = threading.Thread(target=ws_fiyat_dinleyici, args=(st.session_state, st.session_state.lock, st.session_state.dur_sinyali), daemon=True)
        add_script_run_ctx(t1)
        t1.start()
        
        t2 = threading.Thread(target=bot_engine, args=(st.session_state, st.session_state.lock, st.session_state.dur_sinyali), daemon=True)
        add_script_run_ctx(t2)
        t2.start()

def durdur():
    st.session_state.dur_sinyali.set()
    st.session_state.bot_calisiyor = False
    if "Hedef" not in st.session_state.bot_durumu and "İflas" not in st.session_state.bot_durumu:
        st.session_state.bot_durumu = "Duraklatıldı"
    islem_gecmisi_kaydet(st.session_state.islem_gecmisi)


with st.sidebar:
    st.title("🎛️ AI v4 (Pro Live)")
    st.session_state.exchange_adi = st.selectbox("🏦 Borsa", ["binance", "gateio"], disabled=st.session_state.bot_calisiyor)
    ai_mod = st.radio("🧠 Zeka Modeli", ["Mock AI", "OpenAI LLM"], disabled=st.session_state.bot_calisiyor)
    st.session_state.ai_modu = ai_mod
    if ai_mod == "OpenAI LLM": st.session_state.openai_key = st.text_input("OpenAI API Key", type="password", disabled=st.session_state.bot_calisiyor)
        
    st.markdown("---")
    st.session_state.baslangic_bakiye = st.number_input("Başlangıç (USDT)", min_value=1.0, value=st.session_state.baslangic_bakiye, disabled=st.session_state.bot_calisiyor)
    st.session_state.hedef_bakiye = st.number_input("Hedef (USDT)", min_value=2.0, value=st.session_state.hedef_bakiye, disabled=st.session_state.bot_calisiyor)
    st.session_state.kaldirac = st.slider("Kaldıraç (Leverage)", min_value=1, max_value=50, value=st.session_state.kaldirac, step=1, disabled=st.session_state.bot_calisiyor)
    
    if not st.session_state.bot_calisiyor: st.session_state.bakiye = st.session_state.baslangic_bakiye

    st.markdown("---")
    mod_sec = st.radio("Alım Satım Agresifliği", list(MOD_PRESETLERI.keys()), disabled=st.session_state.bot_calisiyor)
    st.session_state.mod = mod_sec
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Başlat", use_container_width=True, type="primary", disabled=st.session_state.bot_calisiyor):
            baslat()
            st.rerun()
    with c2:
        if st.button("⏹️ Durdur", use_container_width=True, disabled=not st.session_state.bot_calisiyor):
            durdur()
            st.rerun()

# ─ Ana Ekran ─
col_baslik, col_durum = st.columns([3, 1])
col_baslik.markdown("<h1 style='color: #66fcf1; font-weight: 800; margin-bottom: 0;'>🚀 PeroTrade Pro AI (Live)</h1>", unsafe_allow_html=True)

status_class = "status-stopped"
if st.session_state.bot_calisiyor: status_class = "status-breakout" if st.session_state.is_breakout else "status-running"
elif "Hedef" in st.session_state.bot_durumu: status_class = "status-target"
    
col_durum.markdown(f"<div style='text-align:right; margin-top:20px;'><span class='status-badge {status_class}'>Durum: {st.session_state.bot_durumu}</span></div>", unsafe_allow_html=True)
st.markdown(f"<div class='dashboard-header'><b>🎯 Odaklanılan Coin: {st.session_state.aktif_sembol}</b> — Sonraki Analiz: {st.session_state.sonraki_analiz_sn}s</div>", unsafe_allow_html=True)

# ─ AI Strateji Paneli ─
st.markdown("### 📊 Vadeli İşlemler ve AI Stratejisi")
m1, m2, m3, m4 = st.columns(4)

with m1:
    poz = st.session_state.pozisyon
    if poz == "LONG": renk, gorsel = "#96c93d", "🟢 LONG"
    elif poz == "SHORT": renk, gorsel = "#FF416C", "🔴 SHORT"
    else: renk, gorsel = "#c5c6c7", "⚪ YOK"
    st.markdown(f"""
    <div class="metric-card">
        <h3>Açık Pozisyon</h3><h1 style="color: {renk}; font-size:1.8rem;">{gorsel}</h1>
        <p>{st.session_state.kaldirac}x Kaldıraçlı</p>
    </div>""", unsafe_allow_html=True)

with m2:
    liq = st.session_state.likidasyon_fiyati
    liq_str = f"${liq:,.4f}" if liq > 0 else "—"
    ts_durum = f"(<span style='color:#66fcf1;'>TS Aktif: ${st.session_state.trailing_stop_fiyat:.4f}</span>)" if st.session_state.ts_aktif else ""
    st.markdown(f"""
    <div class="metric-card">
        <h3>Likidasyon (Liq)</h3><h1 style="color: #f7971e; font-size:1.8rem;">{liq_str}</h1>
        <p>Giriş: {"$"+str(round(st.session_state.giris_fiyati,4)) if st.session_state.giris_fiyati else "—"} <br> {ts_durum}</p>
    </div>""", unsafe_allow_html=True)

with m3:
    if st.session_state.pozisyon != "YOK" and st.session_state.islem_margin > 0:
        aktif_pnl = pnl_hesapla(st.session_state.pozisyon, st.session_state.giris_fiyati, st.session_state.fiyat, st.session_state.islem_margin * st.session_state.islem_kaldirac, st.session_state.islem_kaldirac)
        pnl_pct = (aktif_pnl / st.session_state.islem_margin) * 100
        pnl_renk = "#96c93d" if aktif_pnl >= 0 else "#FF416C"
        pnl_isaret = "+" if aktif_pnl >= 0 else ""
        st.markdown(f"""
        <div class="metric-card">
            <h3>Canlı PNL</h3><h1 style="color: {pnl_renk}; font-size:1.8rem;">{pnl_isaret}{aktif_pnl:.2f} $</h1>
            <p style="color: {pnl_renk}; margin-bottom: 0;">{pnl_isaret}%{pnl_pct:.2f} ROE</p>
            <p style="color: #a4a5a6; font-size: 0.75rem; margin-top:2px;">Maks Drawdown: -%{st.session_state.max_drawdown:.2f}</p>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div class="metric-card"><h3>Canlı PNL</h3><h1 style="color: #66fcf1; font-size:1.8rem;">$0.00</h1><p style="margin-bottom: 0;">İşlem Bekleniyor</p><p style="color: #a4a5a6; font-size: 0.75rem; margin-top:2px;">Maks Drawdown: -%{st.session_state.max_drawdown:.2f}</p></div>""", unsafe_allow_html=True)

with m4:
    btc_tr = st.session_state.btc_trendi
    btc_c = "#96c93d" if btc_tr == "YUKARI" else "#FF416C" if btc_tr == "AŞAĞI" else "#f7971e"
    fon_riski = st.session_state.fonlama_riski
    fon_c = "#FF416C" if "Riskli" in fon_riski else "#66fcf1"
    
    st.markdown(f"""
    <div class="metric-card" style="padding-top: 15px; padding-bottom: 5px;">
        <h3 style="margin-bottom: 5px; color:#c5c6c7;">📋 Live Analiz Raporu</h3>
        <p style="margin:2px;"><b>BTC Trend:</b> <span style="color:{btc_c}">{btc_tr}</span></p>
        <p style="margin:2px;"><b>Fonlama Oranı:</b> <span style="color:{fon_c}">{st.session_state.fonlama_orani:.3f}% ({fon_riski})</span></p>
        <span style="font-size: 0.8rem; color: #a4a5a6;">{st.session_state.ai_analiz_ozeti}</span>
    </div>""", unsafe_allow_html=True)
    
st.markdown("---")

# ─ Finansal Metrikler ─
k1, k2, k3, k4 = st.columns(4)
with k1: st.metric("Anlık Fiyat", f"${st.session_state.fiyat:,.4f}" if st.session_state.fiyat else "—", f"%{st.session_state.degisim_24s:+.2f}")
with k2: 
    hacim = st.session_state.hacim_24s
    hacim_str = f"${hacim/1e6:,.1f}M" if hacim > 1e6 else f"${hacim:,.0f}" if hacim else "—"
    st.metric("24s Hacim", hacim_str)
    
bakiye = st.session_state.bakiye
if st.session_state.pozisyon != "YOK":
    aktif_pnl = pnl_hesapla(st.session_state.pozisyon, st.session_state.giris_fiyati, st.session_state.fiyat, st.session_state.islem_margin * st.session_state.islem_kaldirac, st.session_state.islem_kaldirac)
    toplam = bakiye + st.session_state.islem_margin + aktif_pnl
else: toplam = bakiye

kar_yuzde = ((toplam - st.session_state.baslangic_bakiye) / st.session_state.baslangic_bakiye * 100) if st.session_state.baslangic_bakiye else 0

with k3: st.metric("Toplam Varlık", f"${toplam:,.2f}", f"%{kar_yuzde:+.2f}")
with k4: st.metric("Boşta USDT (Teta)", f"${bakiye:,.2f}")

st.progress(min(toplam / st.session_state.hedef_bakiye, 1.0) if st.session_state.hedef_bakiye else 0.0)
st.markdown("---")

col_sol, col_sag = st.columns([2, 1])
with col_sol:
    st.markdown("<div class='dashboard-header'><b>📋 Vadeli İşlem Geçmişi</b></div>", unsafe_allow_html=True)
    if st.session_state.islem_gecmisi:
        df_log = pd.DataFrame(st.session_state.islem_gecmisi).iloc[::-1].reset_index(drop=True)
        st.dataframe(df_log, use_container_width=True, hide_index=True, height=250)
    else: st.info("Henüz işlem yok.")
        
    st.markdown("<div class='dashboard-header'><b>🔥 Breakout Radarı (Anlık Tarama)</b></div>", unsafe_allow_html=True)
    if st.session_state.taranan_coinler:
        df_scan = pd.DataFrame(st.session_state.taranan_coinler)
        st.dataframe(df_scan, use_container_width=True, hide_index=True)
    else: st.info("Piyasa taraması bekleniyor...")

with col_sag:
    st.markdown("<div class='dashboard-header'><b>🧠 Pro Live Düşünce Günlüğü</b></div>", unsafe_allow_html=True)
    log_kutusu = st.container(height=500, border=True)
    for log in st.session_state.ai_dusunce_gunlugu:
        cls_name = 'ai-log-breakout' if log.get('liq') or log.get('breakout') else 'ai-log-box'
        if '🛡️' in log['msg']: cls_name = 'ai-log-breakout' # Trailing stop vurgusu
        log_kutusu.markdown(f"<div class='{cls_name}'>[{log['time']}] {log['msg']}</div>", unsafe_allow_html=True)

if st.session_state.bot_calisiyor:
    time.sleep(0.3)
    st.rerun()
