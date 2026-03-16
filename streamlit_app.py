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
import os
import sys
from datetime import datetime, timezone

import ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit.runtime.scriptrunner import add_script_run_ctx

import ai_engine
import config as cfg
import persistent_state as ps


def safe_state_kaydet(state):
    """threading.Lock, Event gibi serialize edilemeyen nesneleri temizleyerek kaydet."""
    try:
        temiz = {}
        for k, v in (state.items() if isinstance(state, dict) else []):
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                temiz[k] = v
        ps.state_kaydet(temiz)
    except Exception as e:
        print(f"⚠️ safe_state_kaydet hata: {e}")

def get_app_path():
    """PyInstaller EXE uyumluluğu: Çalışma dizinini bulur."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

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
        "aktif_pozisyonlar": {}, # { sembol: { 'pozisyon': 'LONG/SHORT', 'coin_miktar': ..., 'giris_fiyati': ..., 'likidasyon_fiyati': ..., 'islem_margin': ..., 'islem_kaldirac': ..., 'kademeli_tp_yapildi': bool, 'ts_aktif': bool, 'trailing_stop_fiyat': ... } }
        "pik_bakiye": 10.0,
        "max_drawdown": 0.0,
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
        "global_risk_seviyesi": "Normal",
        "kaldirac": 10, # Sadece eski kodun çökmemesi için geçici/yedek tutulur, pratikte tavsiye_kaldirac kullanılır.
        "baslangic_zamani": 0.0,
        "hedef_sure_saat": 24.0,
        
        "lock": threading.Lock(),
        "dur_sinyali": threading.Event(),
        "analiz_tetikleyici": threading.Event(),
        "son_fiyat_tick": 0.0,
        "cuzdan_gecmisi": [],
        "gun_baslangic_bakiye": cfg.INITIAL_BALANCE,
        "view_mode": "📊 Profesyonel Dashboard"
    }
    for k, v in default_state.items():
        if k not in st.session_state: st.session_state[k] = v
    
    # Persistent state'den yükle (ilk açılışta)
    if "_persistent_loaded" not in st.session_state:
        try:
            loaded = ps.state_yukle(ps.STATE_FILE)
        except Exception as e:
            print(f"⚠️ state_yukle hata: {e}")
            loaded = ps.DEFAULT_STATE.copy()
        if isinstance(loaded, dict) and loaded.get("bakiye", 0) > 0:
            st.session_state.bakiye = loaded.get("bakiye", cfg.INITIAL_BALANCE)
            st.session_state.baslangic_bakiye = loaded.get("baslangic_bakiye", cfg.INITIAL_BALANCE)
            st.session_state.gun_baslangic_bakiye = loaded.get("gun_baslangic_bakiye", st.session_state.bakiye)
            st.session_state.aktif_pozisyonlar = loaded.get("aktif_pozisyonlar", {})
            st.session_state.islem_gecmisi = loaded.get("islem_gecmisi", [])
            st.session_state.max_drawdown = loaded.get("max_drawdown", 0.0)
            st.session_state.pik_bakiye = loaded.get("pik_bakiye", st.session_state.bakiye)
            st.session_state.cuzdan_gecmisi = loaded.get("cuzdan_gecmisi", [])
            st.session_state.api_key_enc = loaded.get("api_key_enc", "")
            st.session_state.api_secret_enc = loaded.get("api_secret_enc", "")
            st.session_state.use_real_api = loaded.get("use_real_api", False)
        st.session_state._persistent_loaded = True

session_state_baslat()

# ==========================================
# 🚀 1. ONBOARDING (API KURULUM EKRANI)
# ==========================================
def api_kurulum_ekrani():
    st.markdown("<h2 style='text-align: center; color: #f3ba2f;'>🔶 Binance API Kurulumu</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #c5c6c7;'>Gerçek işlem yapmak istiyorsanız API bilgilerinizi girin. İstemiyorsanız doğrudan Paper Trading (Sanal Bakiye) moduna geçebilirsiniz.</p>", unsafe_allow_html=True)
    
    with st.container(border=True):
        use_real = st.checkbox("Gerçek Bakiye (Real API) Kullan", value=False)
        api_k = st.text_input("Binance API Key", type="password", disabled=not use_real)
        sec_k = st.text_input("Binance Secret Key", type="password", disabled=not use_real)
        
        b1, b2, b3 = st.columns([1,2,1])
        with b2:
            if st.button("💾 Kaydet ve Başla", use_container_width=True, type="primary"):
                # State'e şifreli kaydet
                state_kur = ps.state_yukle()
                state_kur["use_real_api"] = use_real
                if use_real:
                    state_kur["api_key_enc"] = ps.encode_key(api_k)
                    state_kur["api_secret_enc"] = ps.encode_key(sec_k)
                    st.session_state.api_key_enc = state_kur["api_key_enc"]
                    st.session_state.api_secret_enc = state_kur["api_secret_enc"]
                st.session_state.use_real_api = use_real
                ps.state_kaydet(state_kur)
                st.rerun()
    st.stop()  # API kurulumu tamamlanana kadar uygulamayı durdur

# Eğer gerçek API kullanmak isteniyorsa ama key yoksa, Kurulum ekranını göster
if st.session_state.get("use_real_api", False) and not st.session_state.get("api_key_enc", ""):
    api_kurulum_ekrani()
elif "_onboarding_passed" not in st.session_state:
    # İlk kez açılıyorsa (kullanıcıya sor)
    if not st.session_state.get("use_real_api", False) and not st.session_state.get("api_key_enc", ""):
        st.session_state["_onboarding_passed"] = False
    else:
        st.session_state["_onboarding_passed"] = True

if not st.session_state.get("_onboarding_passed", True):
    st.markdown("""<div style='text-align:center; padding: 20px;'><h3>🚀 PeroTrade Pro 7/24 AI Bot'a Hoş Geldiniz!</h3></div>""", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔑 Gerçek Binance API Kurulumu", use_container_width=True):
            st.session_state.use_real_api = True
            st.rerun()
    with b2:
        if st.button("🎮 Sanal Parayla (Paper Trading) Başla", use_container_width=True, type="primary"):
            st.session_state._onboarding_passed = True
            st.rerun()
    st.stop()


def gunluk_kar_hesapla(state):
    """Bileşik faiz odaklı: Bugünkü kâr/zarar yüzdesini günün başlangıç bakiyesine göre hesaplar."""
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", cfg.INITIAL_BALANCE))
    if gun_baslangic <= 0: return 0.0
    mevcut = state.get("bakiye", gun_baslangic) + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
    return ((mevcut - gun_baslangic) / gun_baslangic) * 100

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
def pnl_hesapla_coklu(pozlar, guncel_fiyatlar: dict) -> float:
    toplam_pnl = 0.0
    for sembol, poz in pozlar.items():
        anlik = guncel_fiyatlar.get(sembol, poz["giris_fiyati"])
        p_pnl = pnl_hesapla(poz["pozisyon"], poz["giris_fiyati"], anlik, poz["islem_margin"] * poz["islem_kaldirac"], poz["islem_kaldirac"])
        toplam_pnl += p_pnl
    return toplam_pnl

def aktif_margin_toplami(pozisyonlar: dict) -> float:
    return sum(p["islem_margin"] for p in pozisyonlar.values())

def islem_kapat(state, sembol, fiyat, neden, is_breakout=False, is_liq=False):
    poz = state["aktif_pozisyonlar"].get(sembol)
    if not poz: return
    
    eski_poz = poz["pozisyon"]
    margin = poz["islem_margin"]
    kaldirac = poz["islem_kaldirac"]
    aktif_pnl = pnl_hesapla(eski_poz, poz["giris_fiyati"], fiyat, margin * kaldirac, kaldirac)

    reel_getiri = margin + aktif_pnl
    state["bakiye"] += reel_getiri
    
    del state["aktif_pozisyonlar"][sembol]
    
    kz_str = f"{aktif_pnl:+.2f} USDT"
    icon = "☠️" if is_liq else "🛡️" if "TS" in neden else "🔴"
    
    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    state["islem_gecmisi"].append({
        "zaman": zaman, "sembol": sembol, "sinyal": f"{icon} KAPAT: {eski_poz}", 
        "fiyat": round(fiyat, 4), "kaldirac": f"{kaldirac}x", "poz_buyukluk": 0, 
        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": kz_str, "ai_notu": neden
    })
    log_ekle(f"{icon} POZİSYON KAPATILDI: {sembol} {eski_poz}. PNL: {kz_str}", state, is_breakout, is_liq)

def ws_fiyat_dinleyici(state, lock, dur_sinyali):
    async def dinle():
        try: exchange = getattr(ccxtpro, state["exchange_adi"])({"enableRateLimit": True})
        except: return
            
        guncel_fiyatlar = {}
            
        while not dur_sinyali.is_set():
            try:
                # 1. Taranan Breakout Coinini Dinle
                sembol = state["aktif_sembol"]
                fiyat_eski = state["fiyat"]
                if sembol and sembol != "Bekleniyor...":
                    ticker_tsk = asyncio.create_task(exchange.watch_ticker(sembol))
                    # 2. Aktif pozisyonu olan diğer coinleri de dinle
                    poz_tasks = []
                    diger_semboller = [s for s in state["aktif_pozisyonlar"].keys() if s != sembol]
                    for s in diger_semboller: poz_tasks.append(exchange.watch_ticker(s))
                    
                    try:
                        res = await asyncio.wait_for(asyncio.gather(ticker_tsk, *poz_tasks, return_exceptions=True), timeout=5.0)
                        
                        breakout_ticker = res[0]
                        if isinstance(breakout_ticker, dict):
                            with lock:
                                f = breakout_ticker.get("last", state["fiyat"])
                                deg = breakout_ticker.get("percentage", state["degisim_24s"])
                                hac = breakout_ticker.get("quoteVolume", state["hacim_24s"])
                                state["fiyat"] = f
                                if deg: state["degisim_24s"] = deg
                                if hac: state["hacim_24s"] = hac
                                guncel_fiyatlar[sembol] = f
                                
                                # --- SIFIR GECİKME (ZERO-LATENCY) TETİKLEME KONTROLÜ ---
                                if state["son_fiyat_tick"] > 0 and f != state["son_fiyat_tick"]:
                                    degisim_tick = abs((f - state["son_fiyat_tick"]) / state["son_fiyat_tick"]) * 100
                                    if degisim_tick >= 0.3: state["analiz_tetikleyici"].set()
                                state["son_fiyat_tick"] = f
                                
                        for i, s in enumerate(diger_semboller):
                            tck = res[i+1]
                            if isinstance(tck, dict): guncel_fiyatlar[s] = tck.get("last", guncel_fiyatlar.get(s, 0))
                                
                        with lock:
                            # Taranan breakout pozisyon fiyati lazim degil, anlik fiyati update ettik.
                            
                            # Global Stop-Loss Korumasi (>%20 Total Account Risk)
                            toplam_margin = aktif_margin_toplami(state["aktif_pozisyonlar"])
                            top_pnl_anlik = pnl_hesapla_coklu(state["aktif_pozisyonlar"], guncel_fiyatlar)
                            anlik_varlik = state["bakiye"] + toplam_margin + top_pnl_anlik
                            max_izin_verilir_risk = anlik_varlik * 0.20
                            
                            if top_pnl_anlik < 0 and abs(top_pnl_anlik) >= max_izin_verilir_risk:
                                # Panik kapat
                                acik_syms = list(state["aktif_pozisyonlar"].keys())
                                for s in acik_syms:
                                    f_s = guncel_fiyatlar.get(s, state["aktif_pozisyonlar"][s]["giris_fiyati"])
                                    islem_kapat(state, s, f_s, "🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam zarar %20'yi aştı.")
                                log_ekle("🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam Bakiye Korundu.", state, is_breakout=True)
                                
                            # --- ANLIK RİSK & POZİSYON KONTROLÜ (WS TICK) ---
                            kapanacak_semboller = []
                            for p_sembol, poz in list(state["aktif_pozisyonlar"].items()):
                                f_s = guncel_fiyatlar.get(p_sembol, poz["giris_fiyati"])
                                if f_s == 0: continue
                                
                                is_long = poz["pozisyon"] == "LONG"
                                is_short = poz["pozisyon"] == "SHORT"
                                liq_price = poz["likidasyon_fiyati"]
                            
                                aktif_pnl = pnl_hesapla(poz["pozisyon"], poz["giris_fiyati"], f_s, poz["islem_margin"] * poz["islem_kaldirac"], poz["islem_kaldirac"])
                                pnl_pct = (aktif_pnl / poz["islem_margin"]) * 100
                            
                                # 1. Likidasyon Kontrolü
                                if (is_long and f_s <= liq_price) or (is_short and f_s >= liq_price):
                                    islem_kapat(state, p_sembol, f_s, "Liquidation", is_liq=True)
                                    if state["bakiye"] <= 0:
                                        state["bot_durumu"] = "💀 İflas"
                                        state["bot_calisiyor"] = False
                                        dur_sinyali.set()
                            
                                else: # Liq olmadıysa
                                    # 2. %10 Kademeli TP %50 Kapat ve TS Breakeven
                                    if pnl_pct >= 10.0 and not poz.get("kademeli_tp_yapildi", False):
                                        poz["kademeli_tp_yapildi"] = True
                                        real_pnl = aktif_pnl / 2
                                        ret_margin = poz["islem_margin"] / 2
                                        state["bakiye"] += (ret_margin + real_pnl)
                                        poz["islem_margin"] /= 2
                                        poz["coin_miktar"] /= 2
                                        
                                        poz["ts_aktif"] = True
                                        poz["trailing_stop_fiyat"] = poz["giris_fiyati"]
                                        
                                        z = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                                        state["islem_gecmisi"].append({
                                            "zaman": z, "sembol": p_sembol, "sinyal": "💰 %50 TP", 
                                            "fiyat": round(f_s, 4), "kaldirac": f"{poz['islem_kaldirac']}x", 
                                            "poz_buyukluk": round(poz["coin_miktar"], 2), 
                                            "bakiye_usdt": round(state["bakiye"], 2), 
                                            "kar_zarar": f"{real_pnl:+.2f} USDT", "ai_notu": "%10 ROE: %50 Kâr Alındı, TS Başabaş."
                                        })
                                        log_ekle(f"💰 %10 ROE Tespiti: {p_sembol} pozisyonun yarısı kapatıldı. TS giriş fiyatına çekildi.", state, is_breakout=True)
                                        
                                    # 3. Normal TS Update
                                    if poz["ts_aktif"]:
                                        ts_hit = False
                                        if is_long:
                                            if pnl_pct >= 5.0 and not poz.get("kademeli_tp_yapildi", False):
                                                yeni_ts = f_s * 0.98
                                                if yeni_ts > poz["trailing_stop_fiyat"]: poz["trailing_stop_fiyat"] = yeni_ts
                                            if f_s <= poz["trailing_stop_fiyat"]: ts_hit = True
                                        else:
                                            if pnl_pct >= 5.0 and not poz.get("kademeli_tp_yapildi", False):
                                                yeni_ts = f_s * 1.02
                                                if yeni_ts < poz["trailing_stop_fiyat"]: poz["trailing_stop_fiyat"] = yeni_ts
                                            if f_s >= poz["trailing_stop_fiyat"]: ts_hit = True
                                            
                                        if ts_hit: kapanacak_semboller.append(p_sembol)
                                        
                                    # 4. Stagnation Switch (Fırsat Maliyeti / Yatay Seyir)
                                    gecen_dk = (time.time() - poz.get("acilis_zamani", time.time())) / 60.0
                                    # Eger 10 dk gecmis ve fiyat yerinden (±%0.2) neredeyse hic oynamamis ise
                                    if gecen_dk >= 10.0 and abs(pnl_pct) < 0.2:
                                        if p_sembol not in kapanacak_semboller:
                                            kapanacak_semboller.append(p_sembol)
                                            poz["kapat_nedeni"] = "Zaman Maliyeti: Yetersiz Volatilite (Daha hacimli coine geçiliyor)"
                            
                            for ks in kapanacak_semboller:
                                f_ks = guncel_fiyatlar.get(ks, state["aktif_pozisyonlar"][ks]["giris_fiyati"])
                                rsn = state["aktif_pozisyonlar"][ks].get("kapat_nedeni", "🛡️ TS KAPAT - İz Süren Stop")
                                islem_kapat(state, ks, f_ks, rsn)
                            
                            # --- DRAWDOWN TRACKER ---
                            anlik_v = state["bakiye"] + aktif_margin_toplami(state["aktif_pozisyonlar"]) + pnl_hesapla_coklu(state["aktif_pozisyonlar"], guncel_fiyatlar)
                            if anlik_v > state["pik_bakiye"]: state["pik_bakiye"] = anlik_v
                            elif state["pik_bakiye"] > 0:
                                dd = (state["pik_bakiye"] - anlik_v) / state["pik_bakiye"] * 100
                                if dd > state["max_drawdown"]: state["max_drawdown"] = dd

                    except asyncio.TimeoutError: pass
                else: await asyncio.sleep(0.5)
            except Exception as e:
                await asyncio.sleep(1)
        try: await exchange.close()
        except: pass
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dinle())

def bot_engine(state, lock: threading.Lock, dur_sinyali: threading.Event):
    try:
        exchange = getattr(ccxt, state["exchange_adi"])({"enableRateLimit": True})
    except Exception as e:
        with lock: log_ekle(f"❌ Exchange bağlantı hatası: {e}", state)
        return
    
    while not dur_sinyali.is_set():
        try:
            preset = MOD_PRESETLERI.get(state.get("mod", "⚡ Agresif Mod"), MOD_PRESETLERI["⚡ Agresif Mod"])
            
            # --- 1. AŞAMA: TARA VE CANLI VERİ ENTEGRASYONU ---
            with lock:
                acik_poz_var_mi = len(state.get("aktif_pozisyonlar", {})) > 0
                if not acik_poz_var_mi: log_ekle("🔍 Live Test: Breakout, BTC Trendi ve Fonlama verileri sentezleniyor...", state)
            
            try:
                btc_trend = ai_engine.btc_trendi_analiz_et(exchange)
            except Exception:
                btc_trend = "BİLİNMİYOR"
            
            try:
                top_coinler = ai_engine.top_coinleri_tara(exchange, limit=100)
            except Exception:
                top_coinler = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
                
            try:
                tarama_sonucu = ai_engine.anormallik_tara_ve_sec(exchange, top_coinler, preset["sma_kisa"], preset["sma_uzun"])
            except Exception:
                tarama_sonucu = {"secilen_sembol": "BTC/USDT", "secilen_pazar": {}, "secilen_sma": "BEKLE", "secilen_breakout": False, "taranan_liste": [], "karar_raporu": "", "haber_puanlari": {}}
            
            secilen_sembol = tarama_sonucu["secilen_sembol"] or "BTC/USDT"
            secilen_pazar = tarama_sonucu.get("secilen_pazar", {})
            secilen_sma = tarama_sonucu.get("secilen_sma", "BEKLE")
            is_breakout = tarama_sonucu.get("secilen_breakout", False)
            karar_raporu = tarama_sonucu.get("karar_raporu", "")
            
            with lock:
                state["taranan_coinler"] = tarama_sonucu.get("taranan_liste", [])
                state["aktif_sembol"] = secilen_sembol
                state["is_breakout"] = is_breakout
                if is_breakout:
                    state["bot_durumu"] = "🚨 Breakout Modu!"
                    log_ekle(f"🔥 HACİM PATLAMASI: {secilen_sembol} (Hız 5s->2s)", state, is_breakout=True)
                else: state["bot_durumu"] = "Çalışıyor"
                
                # Şeffaf Karar Raporu Logla
                if karar_raporu:
                    for rapor_satiri in karar_raporu.split('\n'):
                        log_ekle(f"📊 {rapor_satiri}", state)
            
            try:
                ticker = exchange.fetch_ticker(secilen_sembol)
                if isinstance(ticker, dict):
                    fiyat = ticker.get("last", 0) or secilen_pazar.get("fiyat", 0) if secilen_pazar else 0
                    degisim = ticker.get("percentage", 0) or 0
                    hacim = ticker.get("quoteVolume", 0) or 0
                else:
                    raise ValueError("Ticker is not a dict")
            except Exception:
                fiyat = secilen_pazar.get("fiyat", 0) if isinstance(secilen_pazar, dict) else 0
                degisim, hacim = 0, 0
                
            fonlama = ai_engine.fonlama_orani_getir(exchange, secilen_sembol)
            
            # --- Multi-Timeframe Analiz ---
            try:
                mtf = ai_engine.multi_timeframe_analiz(exchange, secilen_sembol)
                if isinstance(mtf, dict) and isinstance(mtf.get("detay"), dict):
                    d = mtf["detay"]
                    s5 = d.get("5dk", {}).get("sinyal", "?") if isinstance(d.get("5dk"), dict) else "?"
                    s15 = d.get("15dk", {}).get("sinyal", "?") if isinstance(d.get("15dk"), dict) else "?"
                    s1s = d.get("1s", {}).get("sinyal", "?") if isinstance(d.get("1s"), dict) else "?"
                    with lock:
                        state["mtf_konsensus"] = mtf.get("konsensus", "KARARSIZ")
                        log_ekle(f"🔬 Multi-TF: 5dk={s5} | 15dk={s15} | 1s={s1s} → {mtf.get('konsensus', '?')} (RSI Ort: {mtf.get('ortalama_rsi', 50)})", state)
                else:
                    mtf = {"konsensus": "KARARSIZ", "guc": 0}
            except Exception:
                mtf = {"konsensus": "KARARSIZ", "guc": 0}
            
            # --- Grid Analizi (Yatay piyasa algılama) ---
            grid_trade_yapildi = False
            try:
                df_grid = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "1h", limit=30)
                grid_bilgi = ai_engine.grid_destek_direnc(df_grid)
                if grid_bilgi["grid_uygun"] and secilen_sembol not in state.get("aktif_pozisyonlar", {}):
                    with lock:
                        log_ekle(f"📏 GRID MODU: {secilen_sembol} yatay seyirde. Destek: ${grid_bilgi['destek']}, Direnç: ${grid_bilgi['direnc']} (Aralık: %{grid_bilgi['aralik_pct']})", state)
                        # Grid seviyesine göre LONG/SHORT belirle
                        if fiyat <= grid_bilgi["destek"] * 1.01:  # Desteğe yakınsa LONG
                            karar_override = "LONG"
                            log_ekle(f"📏 GRID LONG: Fiyat (${fiyat:.4f}) destek seviyesine (${grid_bilgi['destek']}) yakın. Grid alım emirleri devrede.", state)
                            grid_trade_yapildi = True
                        elif fiyat >= grid_bilgi["direnc"] * 0.99:  # Direnci yakınsa SHORT
                            karar_override = "SHORT"
                            log_ekle(f"📏 GRID SHORT: Fiyat (${fiyat:.4f}) direnç seviyesine (${grid_bilgi['direnc']}) yakın. Grid satım emirleri devrede.", state)
                            grid_trade_yapildi = True
            except:
                grid_bilgi = {"grid_uygun": False}
                
            # --- 2. AŞAMA: DURUM KONTROLÜ (RİSK WS TARAFINDAN YÖNETİLİYOR) ---
            pozisyonu_kapat = False
            kapat_sinyali_nedeni = ""
            
            with lock:
                state["btc_trendi"] = btc_trend
                state["fonlama_orani"] = fonlama["oran"]
                state["fonlama_riski"] = fonlama["risk"]

            if dur_sinyali.is_set(): break

            # --- 3. AŞAMA: YAPAY ZEKA TAHMİNİ ---
            # Mevcut poz var mi secili coin icin?
            with lock: poz_durumu = state["aktif_pozisyonlar"].get(secilen_sembol, {}).get("pozisyon", "YOK")
                
            # --- ZAMAN BASKISI HESABI ---
            zaman_baski_carpani = 1.0
            if state.get("baslangic_zamani", 0) > 0 and state.get("hedef_sure_saat", 0) > 0:
                gecen_saat = (time.time() - state["baslangic_zamani"]) / 3600.0
                sure_orani = gecen_saat / state["hedef_sure_saat"]
                
                hedef_farki_pct = (state["hedef_bakiye"] - state["bakiye"]) / state["hedef_bakiye"]
                # Kademeli zaman baskısı sistemi
                if sure_orani >= 0.80 and hedef_farki_pct > 0.20:
                    # BERSERKER MODU: Son %20 dilim, hedefe uzak
                    zaman_baski_carpani = 4.0   # 4x çarpanı - Tüm riskleri göze al
                    with lock:
                        state["bot_durumu"] = "💥 BERSERKER Modu!"
                        log_ekle(f"💥 BERSERKER MODU AKTİF! Süre: %{sure_orani*100:.0f} geçti. Hedefe Uzaklık: %{hedef_farki_pct*100:.0f}. MAKSİMUM AGRESİFLİK!", state)
                elif sure_orani >= 0.70 and hedef_farki_pct > 0.30:
                    # FINAL HUNTER MODU
                    zaman_baski_carpani = 3.0
                    with lock:
                        log_ekle(f"🎯 FINAL HUNTER MODU AKTİF! Süre: %{sure_orani*100:.0f} geçti. Kaldıraç 3x çarpanı!", state)
                elif sure_orani >= 0.50 and hedef_farki_pct > 0.05:
                    zaman_baski_carpani = 2.0
                elif sure_orani > 0.30 and hedef_farki_pct > 0:
                    zaman_baski_carpani = 1.0 + (sure_orani * hedef_farki_pct * 2.0)
                    
            karar_paketi = {"karar": "BEKLE", "dusunce": kapat_sinyali_nedeni, "aralik_sn": 5}
            if not pozisyonu_kapat:
                # Pazar verisi None ise AI fonksiyonlarına geçirilmemeli
                if not isinstance(secilen_pazar, dict) or not secilen_pazar:
                    karar_paketi = {"karar": "BEKLE", "dusunce": "Pazar verisi alınamadı, bekleniyor.", "aralik_sn": 30, "guven_skoru": 0, "expected_growth": 0, "tavsiye_kaldirac": 10, "tavsiye_oran": 0.10, "ozet": "Veri yok"}
                elif state.get("ai_modu") == "OpenAI LLM" and state.get("openai_key"):
                    karar_paketi = ai_engine.llm_karar(secilen_sembol, secilen_pazar, secilen_sma, state["openai_key"], poz_durumu, btc_trend, fonlama, zaman_baski_carpani)
                else:
                    skor = ai_engine.kompozit_skor_hesapla(secilen_pazar, secilen_sma)
                    karar_paketi = ai_engine.mock_ai_karar(secilen_sembol, secilen_pazar, skor, poz_durumu, btc_trend, fonlama, zaman_baski_carpani)
                
                # --- NLP HABER VETO SİSTEMİ ---
                haber_puanlari = tarama_sonucu.get("haber_puanlari", {})
                if haber_puanlari:
                    veto_sonuc = ai_engine.haber_vetosu(haber_puanlari, karar_paketi["karar"])
                    if veto_sonuc["veto"]:
                        with lock: log_ekle(veto_sonuc["neden"], state)
                        karar_paketi["karar"] = "BEKLE"
                        karar_paketi["dusunce"] = veto_sonuc["neden"]
                    elif veto_sonuc["neden"]:
                        with lock: log_ekle(veto_sonuc["neden"], state)
                
                # --- GÜNLÜK RİSK BAROMETRESİ ---
                gunluk_kar = gunluk_kar_hesapla(state)
                if gunluk_kar >= 10.0:
                    # GÜVENLİ MOD: Günlük %10 hedefe ulaşıldı
                    karar_paketi["karar"] = "BEKLE"
                    karar_paketi["dusunce"] = f"🛡️ GÜVENLİ MOD: Günlük kâr hedefi (%{gunluk_kar:.1f}) aşıldı! Yeni işlem açılmıyor."
                    with lock:
                        state["bot_durumu"] = "🛡️ Güvenli Mod"
                        log_ekle(karar_paketi["dusunce"], state)
                elif gunluk_kar <= -5.0:
                    # PANİK KORUMASI: Günlük %5 kayıp
                    with lock:
                        state["bot_durumu"] = "🚨 Panik Koruması!"
                        log_ekle(f"🚨 PANİK KORUMASI: Günlük kayıp %{gunluk_kar:.1f}! Tüm işlemler askıya alınıyor.", state)
                    karar_paketi["karar"] = "BEKLE"
                    karar_paketi["dusunce"] = f"Panik Koruması aktif. Günlük kayıp: %{gunluk_kar:.1f}"
                
                # --- DCA KONTROLÜ (Açık pozisyonlar için) ---
                if secilen_sembol in state.get("aktif_pozisyonlar", {}):
                    poz = state["aktif_pozisyonlar"][secilen_sembol]
                    dca = ai_engine.dca_hesapla(poz, fiyat, state["bakiye"])
                    if dca["uygun"]:
                        with lock:
                            log_ekle(f"💱 DCA ÖNERİ: {secilen_sembol} - {dca['neden']}", state)
                            ekleme = dca["ekleme_margin"]
                            if ekleme <= state["bakiye"]:
                                state["aktif_pozisyonlar"][secilen_sembol]["islem_margin"] += ekleme
                                state["aktif_pozisyonlar"][secilen_sembol]["giris_fiyati"] = dca["yeni_ortalama"]
                                state["aktif_pozisyonlar"][secilen_sembol]["dca_sayisi"] = dca.get("dca_sayisi", 1)
                                state["bakiye"] -= ekleme
                                log_ekle(f"✅ DCA UYGULANDI: ${ekleme:.2f} eklendi. Yeni ortalama: ${dca['yeni_ortalama']}. {dca['neden']}", state)
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
                
                # Cüzdan değeri kaydı (zaman serisi)
                toplam_varlik = state["bakiye"] + aktif_margin_toplami(state["aktif_pozisyonlar"])
                state["cuzdan_gecmisi"].append({"zaman": datetime.now(timezone.utc).strftime("%H:%M:%S"), "deger": round(toplam_varlik, 2)})
                if len(state["cuzdan_gecmisi"]) > 200: state["cuzdan_gecmisi"] = state["cuzdan_gecmisi"][-200:]  # Max 200 veri noktasi
                
                # Global Risk Hesabi gostergesi
                total_kullanilan = aktif_margin_toplami(state["aktif_pozisyonlar"])
                top_v = state["bakiye"] + total_kullanilan
                if top_v > 0: risk_pct = (total_kullanilan / top_v)*100
                else: risk_pct = 0
                if risk_pct > 15: state["global_risk_seviyesi"] = "🔴 Yüksek Risk"
                elif risk_pct > 5: state["global_risk_seviyesi"] = "🟡 Orta Risk"
                else: state["global_risk_seviyesi"] = "🟢 Düşük Risk"
                
                if not pozisyonu_kapat:
                    log_ekle(f"🎯 {secilen_sembol} Analizi: {karar_paketi['dusunce']}", state, is_breakout=is_breakout)
                    # Karar loguna KARAR satırı ekle
                    sinyal_k = karar_paketi["karar"]
                    if sinyal_k in ["LONG", "SHORT"]:
                        log_ekle(f"📝 KARAR: {sinyal_k} - Sebep: {karar_paketi['dusunce'][:80]}...", state)
                
                sinyal = karar_paketi["karar"]
                zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                
                if (sinyal == "LONG" or sinyal == "SHORT") and secilen_sembol not in state["aktif_pozisyonlar"]:
                    tavsiye_kaldirac = karar_paketi.get("tavsiye_kaldirac", 10)
                    tavsiye_oran = karar_paketi.get("tavsiye_oran", 0.10)
                    
                    # AI-Managed Full Autonomy: Global Risk Limit kontrolü (Berserker modda esnetilir)
                    risk_limit = 0.40 if zaman_baski_carpani >= 4.0 else 0.30 if zaman_baski_carpani >= 3.0 else 0.20
                    kullanilabilir_max = min(tavsiye_oran, risk_limit - (risk_pct/100.0))
                    if kullanilabilir_max > 0:
                        margin = state["bakiye"] * kullanilabilir_max
                        buyukluk_usdt = margin * tavsiye_kaldirac
                    
                        yeni_poz = {
                            "pozisyon": sinyal,
                            "coin_miktar": buyukluk_usdt,
                            "giris_fiyati": fiyat,
                            "likidasyon_fiyati": likidasyon_hesapla(sinyal, fiyat, tavsiye_kaldirac),
                            "islem_margin": margin,
                            "islem_kaldirac": tavsiye_kaldirac,
                            "kademeli_tp_yapildi": False,
                            "ts_aktif": False,
                            "trailing_stop_fiyat": 0.0,
                            "acilis_zamani": time.time(),
                            "giris_nedeni": karar_paketi["dusunce"][:120],
                            "beklenen_hedef": karar_paketi.get("expected_growth", 0.0)
                        }
                        state["aktif_pozisyonlar"][secilen_sembol] = yeni_poz
                        state["bakiye"] -= margin
                        
                        state["islem_gecmisi"].append({
                            "zaman": zaman, "sembol": secilen_sembol, "sinyal": f"🟢 AÇ: {sinyal}", 
                            "fiyat": round(fiyat, 4), "kaldirac": f"{tavsiye_kaldirac}x", "poz_buyukluk": round(buyukluk_usdt, 2), 
                            "bakiye_usdt": round(state["bakiye"] + margin, 2), "kar_zarar": "—", "ai_notu": karar_paketi["dusunce"]
                        })
                        log_ekle(f"💰 {tavsiye_kaldirac}x {sinyal} POZİSYON AÇILDI: {secilen_sembol}. Giriş: {fiyat:.4f}, Liq: {yeni_poz['likidasyon_fiyati']:.4f}", state, is_breakout)
                    else:
                        log_ekle(f"🛡️ {secilen_sembol} Fırsatı Boş Geçildi: Global Risk Limiti (%20) Dolu.", state)
                    
                elif sinyal == "KAPAT" and secilen_sembol in state["aktif_pozisyonlar"]:
                    islem_kapat(state, secilen_sembol, fiyat, karar_paketi["dusunce"])
                
                # Hedef sarti (Tum acik pozisyonlarin guncel anlik degerini sonradan ekleyebiliriz WS'da yapiyoruz)
                # Basit bir check:
                if state["pik_bakiye"] >= state["hedef_bakiye"]:
                    state["bot_durumu"] = "🎯 Hedefi Ulaştı!"
                    state["bot_calisiyor"] = False
                    log_ekle("🏆 HEDEF ULAŞILDI! Bot durduruluyor.", state)
                    islem_gecmisi_kaydet(state["islem_gecmisi"])
                    safe_state_kaydet(state)
                    dur_sinyali.set()
                    break
            
            # Persistent State Kaydet (Her döngü sonu — serialize-safe)
            safe_state_kaydet(state)

            # --- 5. AŞAMA: BEKLEME (EVENT-DRIVEN SIFIR GECİKME) ---
            bekleme_suresi = int(karar_paketi["aralik_sn"])
            if not is_breakout: bekleme_suresi = int(bekleme_suresi * preset["aralik_carpan"])
            with lock: state["sonraki_analiz_sn"] = bekleme_suresi
            
            state["analiz_tetikleyici"].clear()
            for _ in range(bekleme_suresi):
                if dur_sinyali.is_set(): return
                tetiklendi = state["analiz_tetikleyici"].wait(timeout=1.0)
                if tetiklendi:
                    with lock:
                        log_ekle("⚡ SIFIR GECİKME: Anlık Hacim/Fiyat Patlaması tetiklendi! Bekleme iptal edildi.", state, is_breakout=True)
                        state["sonraki_analiz_sn"] = 0
                    break
                with lock: state["sonraki_analiz_sn"] -= 1

        except Exception as e:
            with lock:
                log_ekle(f"❌ Döngü Hatası (devam ediyor): {str(e)[:100]}", state)
                print(f"⚠️ bot_engine döngü hatası: {e}")
            time.sleep(5)


# ─────────────────────────────────────────────
# Uygulama Arayüzü (UI)
# ─────────────────────────────────────────────
def baslat():
    if not st.session_state.bot_calisiyor:
        st.session_state.dur_sinyali.clear()
        if st.session_state.baslangic_zamani == 0.0: st.session_state.baslangic_zamani = time.time()
        st.session_state.bot_calisiyor = True
        st.session_state.bot_durumu = "Çalışıyor"
        
        # API Entegrasyonu (Runtime'da Key'leri çözer)
        if st.session_state.use_real_api:
            cfg.USE_REAL_API = True
            cfg.API_KEY = ps.decode_key(st.session_state.api_key_enc)
            cfg.SECRET_KEY = ps.decode_key(st.session_state.api_secret_enc)
        else:
            cfg.USE_REAL_API = False
        
        t1 = threading.Thread(target=ws_fiyat_dinleyici, args=(st.session_state, st.session_state.lock, st.session_state.dur_sinyali), daemon=True)
        add_script_run_ctx(t1)
        t1.start()
        
        t2 = threading.Thread(target=bot_engine, args=(st.session_state, st.session_state.lock, st.session_state.dur_sinyali), daemon=True)
        add_script_run_ctx(t2)
        t2.start()

def durdur():
    st.session_state.dur_sinyali.set()
    st.session_state.bot_calisiyor = False
    st.session_state.bot_durumu = "Durduruldu"
    safe_state_kaydet(st.session_state)


with st.sidebar:
    st.markdown("## ⚙️ Kontrol Paneli")
    
    # ÇALIŞMA MODU SEÇİCİ
    cur_mod_str = "💰 Real (Binance API)" if st.session_state.use_real_api else "🎮 Demo (Sanal Para)"
    yeni_mod = st.radio("🕹️ Çalışma Modu", ["🎮 Demo (Sanal Para)", "💰 Real (Binance API)"], 
                        index=1 if st.session_state.use_real_api else 0, 
                        disabled=st.session_state.bot_calisiyor)
                        
    if yeni_mod != cur_mod_str:
        safe_state_kaydet(st.session_state)  # Eski modu kaydet
        st.session_state.use_real_api = (yeni_mod == "💰 Real (Binance API)")
        if "_persistent_loaded" in st.session_state:
            del st.session_state["_persistent_loaded"]
        st.rerun()
    
    st.markdown("---")
    # GÖRÜNÜM MODU SEÇİCİ (DUAL-VIEW)
    st.session_state.view_mode = st.radio(
        "👁️ Görünüm Modu", 
        ["📊 Profesyonel Dashboard", "📜 Sadece İşlem Logları"],
        help="Eski PC'lerde performans için 'Sadece İşlem Logları' modunu seçebilirsiniz."
    )
    st.markdown("---")
    
    st.title("🎛️ AI v4 (Otonom Fon Yöneticisi)")
    
    # DEMO/API ONBOARDING UI
    if st.session_state.use_real_api:
        st.markdown("### 🔑 API Anahtarları")
        api_key_input = st.text_input("API Key", type="password", value=ps.decode_key(st.session_state.api_key_enc), disabled=st.session_state.bot_calisiyor)
        api_secret_input = st.text_input("Secret Key", type="password", value=ps.decode_key(st.session_state.api_secret_enc), disabled=st.session_state.bot_calisiyor)
        
        if api_key_input: st.session_state.api_key_enc = ps.encode_key(api_key_input)
        if api_secret_input: st.session_state.api_secret_enc = ps.encode_key(api_secret_input)
        
        if not api_key_input or not api_secret_input:
            st.warning("Gerçek API kullanmak için anahtarlar girilmelidir.")
            st.session_state.can_start_bot = False
        else:
            st.session_state.can_start_bot = True
    else:
        st.session_state.can_start_bot = True # Sanal modda her zaman başlatılabilir
        st.info("Sanal bakiye ile limitsiz risksiz test.")

    st.session_state.exchange_adi = st.selectbox("🏦 Borsa", ["binance", "gateio"], disabled=st.session_state.bot_calisiyor)
    ai_mod = st.radio("🧠 Zeka Modeli", ["Mock AI", "OpenAI LLM"], disabled=st.session_state.bot_calisiyor)
    st.session_state.ai_modu = ai_mod
    if ai_mod == "OpenAI LLM": st.session_state.openai_key = st.text_input("OpenAI API Key", type="password", disabled=st.session_state.bot_calisiyor)
        
    st.markdown("---")
    st.session_state.baslangic_bakiye = st.number_input("Başlangıç (USDT)", min_value=1.0, value=st.session_state.baslangic_bakiye, disabled=st.session_state.bot_calisiyor)
    st.session_state.hedef_bakiye = st.number_input("Hedef (USDT)", min_value=2.0, value=st.session_state.hedef_bakiye, disabled=st.session_state.bot_calisiyor)
    st.session_state.hedef_sure_saat = st.number_input("Hedef Süre (Saat)", min_value=1.0, value=st.session_state.hedef_sure_saat, disabled=st.session_state.bot_calisiyor)
    
    if not st.session_state.bot_calisiyor: st.session_state.bakiye = st.session_state.baslangic_bakiye

    st.markdown("---")
    mod_sec = st.radio("Alım Satım Agresifliği", list(MOD_PRESETLERI.keys()), disabled=st.session_state.bot_calisiyor)
    st.session_state.mod = mod_sec
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Başlat", use_container_width=True, type="primary", disabled=st.session_state.bot_calisiyor or not st.session_state.can_start_bot):
            baslat()
            st.rerun()
    with c2:
        if st.button("⏹️ Durdur", use_container_width=True, disabled=not st.session_state.bot_calisiyor):
            durdur()
            st.rerun()
            
# ─ Günlük Bileşik Faiz ve Hedef ─
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📈 Günlük Bileşik Faiz")
    hedef_bakiye_gunluk = ps.bilesik_faiz_hedef(st.session_state)
    gun_kapanis = st.session_state.bakiye
    kalan_bakiye = hedef_bakiye_gunluk - gun_kapanis
    gunluk_pnl = gunluk_kar_hesapla(st.session_state)

    st.sidebar.metric("Bugünün Hedefi", f"${hedef_bakiye_gunluk:.2f}", f"+%{cfg.DAILY_TARGET_PCT} (Kalan: ${max(0, kalan_bakiye):.2f})")

    # ─ Cüzdan Özeti ─
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 💼 Cüzdan Özeti")
    state_bakiye = st.session_state.bakiye
    margin_total = aktif_margin_toplami(st.session_state.aktif_pozisyonlar)
    st.sidebar.markdown(f"**Toplam Varlık:** ${state_bakiye + margin_total:.2f}")
    
    st.markdown("---")
    st.markdown("### 💼 Cüzdan & Sağlık")
    bky = st.session_state.bakiye
    kullanilan = aktif_margin_toplami(st.session_state.aktif_pozisyonlar)
    tplm = bky + kullanilan
    
    st.metric("Toplam Varlık", f"${tplm:,.2f}")
    st.metric("Boşta USDT", f"${bky:,.2f}")
    st.metric("Kullanılan Margin", f"${kullanilan:,.2f}")
    
    gecen_sure = (time.time() - st.session_state.baslangic_zamani)/3600 if st.session_state.baslangic_zamani > 0 else 0
    kalan_sure = max(0, st.session_state.hedef_sure_saat - gecen_sure)
    if st.session_state.bot_calisiyor:
        st.info(f"⏳ Kalan Hedef Süresi: {kalan_sure:.1f} Saat")
    
    st.markdown("---")
    st.markdown("### 📈 Günlük Performans Takibi")
    gunluk_pnl = gunluk_kar_hesapla(st.session_state)
    hedef_pct = 10.0  # Günlük %10 hedef
    
    # Gauge benzeri görsel
    gauge_pct = max(0.0, min(gunluk_pnl / hedef_pct, 1.0)) if hedef_pct > 0 else 0.0
    if gunluk_pnl >= hedef_pct:
        gauge_renk = "#00ff88"
        gauge_emoji = "🏆"
        gauge_durum = "HEDEF TAMAM!"
    elif gunluk_pnl >= 0:
        gauge_renk = "#66fcf1"
        gauge_emoji = "📈"
        gauge_durum = "Kârda"
    else:
        gauge_renk = "#ff4444"
        gauge_emoji = "📉"
        gauge_durum = "Zararda"
    
    st.markdown(f"""
    <div style='background: rgba(31,40,51,0.8); border-radius: 12px; padding: 16px; border: 1px solid {gauge_renk};'>
        <div style='display: flex; justify-content: space-between; align-items: center;'>
            <span style='font-size: 14px; color: #c5c6c7;'>{gauge_emoji} Günlük Kâr/Zarar</span>
            <span style='font-size: 20px; font-weight: 800; color: {gauge_renk};'>%{gunluk_pnl:+.2f}</span>
        </div>
        <div style='background: #1a1a2e; border-radius: 8px; height: 12px; margin-top: 8px; overflow: hidden;'>
            <div style='background: {gauge_renk}; height: 100%; width: {gauge_pct*100:.0f}%; border-radius: 8px; transition: width 0.3s;'></div>
        </div>
        <div style='display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: #888;'>
            <span>0%</span>
            <span style='color: {gauge_renk}; font-weight: 600;'>{gauge_durum}</span>
            <span>%{hedef_pct:.0f} Hedef</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Cüzdan Değeri Çizgi Grafiği
    if st.session_state.cuzdan_gecmisi:
        st.markdown("### 📉 Portföy Değeri (Anlık)")
        chart_data = pd.DataFrame(st.session_state.cuzdan_gecmisi)
        st.line_chart(chart_data.set_index("zaman")["deger"], use_container_width=True, color="#66fcf1")

    # ─ 2 GÜNLÜK DEMO TAKİBİ ─
    if not st.session_state.use_real_api:
        st.sidebar.markdown("---")
        st.sidebar.markdown("### ⏳ 48 Saatlik Demo Testi")
        gecen_saniye = (time.time() - st.session_state.baslangic_zamani) if st.session_state.baslangic_zamani > 0 else 0
        kalan_saniye = max(0, (48 * 3600) - gecen_saniye)
        saat = int(kalan_saniye // 3600)
        dakika = int((kalan_saniye % 3600) // 60)
        ilerleme_pct = min(1.0, gecen_saniye / (48 * 3600))
        
        if kalan_saniye == 0 and st.session_state.bot_calisiyor:
            st.session_state.bot_durumu = "🎯 Test Bitti"
            st.session_state.bot_calisiyor = False
            
        st.sidebar.progress(ilerleme_pct)
        st.sidebar.markdown(f"**Kalan Süre:** {saat}s {dakika}d")
        if st.session_state.baslangic_zamani > 0 and kalan_saniye == 0:
            kapanan_islemler = [i for i in st.session_state.islem_gecmisi if "KAPAT" in i.get("sinyal", "")]
            pozitifler = [i for i in kapanan_islemler if isinstance(i.get("kar_zarar"), (int, float)) and float(i["kar_zarar"]) > 0]
            basari_orani = (len(pozitifler) / len(kapanan_islemler) * 100) if kapanan_islemler else 0
            
            st.sidebar.success(f"🎉 **2 Günlük Demo Tamamlandı!**\n\n"
                               f"📊 **Toplam İşlem:** {len(kapanan_islemler)}\n"
                               f"🎯 **Başarı Oranı:** %{basari_orani:.1f}\n"
                               f"💰 **Toplam Kâr:** ${st.session_state.bakiye - 100.0:.2f}")

# ─ Ana Ekran (DUAL-VIEW MANTIĞI) ─
if not st.session_state.use_real_api:
    st.markdown("<div style='background: #ff4b4b; color: white; padding: 10px; text-align: center; border-radius: 8px; font-weight: bold; margin-bottom: 20px;'>⚠️ DEMO MODU AKTİF - İşlemler Sanal Para İle Simüle Ediliyor</div>", unsafe_allow_html=True)

col_baslik, col_durum = st.columns([3, 1])
col_baslik.markdown("<h1 style='color: #66fcf1; font-weight: 800; margin-bottom: 0;'>🚀 PeroTrade Pro AI (Live)</h1>", unsafe_allow_html=True)

status_class = "status-stopped"
if st.session_state.bot_calisiyor: status_class = "status-breakout" if st.session_state.is_breakout else "status-running"
elif "Hedef" in st.session_state.bot_durumu: status_class = "status-target"
    
col_durum.markdown(f"<div style='text-align:right; margin-top:20px;'><span class='status-badge {status_class}'>Durum: {st.session_state.bot_durumu}</span></div>", unsafe_allow_html=True)
st.markdown(f"<div class='dashboard-header'><b>🎯 Odaklanılan Ticker: {st.session_state.aktif_sembol}</b> — Risk Barometresi: {st.session_state.global_risk_seviyesi}</div>", unsafe_allow_html=True)

tab_dash, tab_tv = st.tabs(["📊 Dashboard", "📈 Grafikler (TradingView)"])

with tab_dash:
    st.markdown("### 📊 Aktif Pozisyonlar Paneli")
    aktif_toplam_pnl = 0.0
    
    if not st.session_state.aktif_pozisyonlar:
        st.info("Açık Pozisyon Bulunmuyor.")
    else:
        st.markdown("#### ⚡ Anlık Durum Kartları")
        
        poz_liste = []
        for idx, (s, p) in enumerate(st.session_state.aktif_pozisyonlar.items()):
            try:
                guncel_fiyat = st.session_state.fiyat if s == st.session_state.aktif_sembol else p['giris_fiyati']
                # PNL Senkronizasyonu: Fiyat henuz gelmediyse veya 0 ise hesaplama yapma
                if guncel_fiyat <= 0 or p['giris_fiyati'] <= 0:
                    anlik_pnl = 0.0
                    pnl_pct = 0.0
                else:
                    anlik_pnl = pnl_hesapla(p['pozisyon'], p['giris_fiyati'], guncel_fiyat, p['islem_margin'] * p['islem_kaldirac'], p['islem_kaldirac'])
                    pnl_pct = (anlik_pnl / p['islem_margin']) * 100 if p['islem_margin'] > 0 else 0
                
                # Anormal PNL Kontrolü (Fiyat Kopuklugu koruması)
                if abs(pnl_pct) > 500:  # %500 uzerinde PNL olasi degildir, fiyat kopuklugu
                    anlik_pnl = 0.0
                    pnl_pct = 0.0
                    
            except Exception:
                anlik_pnl = 0.0
                pnl_pct = 0.0
                guncel_fiyat = p.get('giris_fiyati', 0)
                
            aktif_toplam_pnl += anlik_pnl
            
            pnl_renk = "#00ff88" if anlik_pnl >= 0 else "#ff4444"
            beklenen = p.get('beklenen_hedef', 0)
            giris_nedeni = p.get('giris_nedeni', 'Otonom AI Kararı')
            liq_risk_pct = abs((guncel_fiyat - p['likidasyon_fiyati']) / guncel_fiyat * 100) if guncel_fiyat > 0 else 0
            
            st.markdown(f"""
            <div style='background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; padding: 16px; margin-bottom: 12px; border-left: 4px solid {pnl_renk};'>
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;'>
                    <span style='font-size: 18px; font-weight: 700; color: #66fcf1;'>{s} ({p['pozisyon']} {p['islem_kaldirac']}x)</span>
                    <span style='font-size: 20px; font-weight: 800; color: {pnl_renk};'>{anlik_pnl:+.2f} USDT ({pnl_pct:+.1f}%)</span>
                </div>
                <div style='display: flex; gap: 24px; color: #c5c6c7; font-size: 13px; margin-bottom: 6px;'>
                    <span>💰 Giriş: <b>${p['giris_fiyati']:.4f}</b></span>
                    <span>📊 Anlık: <b>${guncel_fiyat:.4f}</b></span>
                    <span>🛡️ Margin: <b>${p['islem_margin']:.2f}</b></span>
                    <span>💣 Liq Riski: <b>%{liq_risk_pct:.1f}</b></span>
                </div>
                <div style='color: #45a29e; font-size: 12px; margin-top: 4px;'>
                    <b>📝 Giriş Nedeni:</b> {giris_nedeni}
                </div>
                <div style='color: #888; font-size: 11px; margin-top: 2px;'>
                    <b>🎯 Beklenen Hedef:</b> %{beklenen:+.1f} büyüme
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            poz_liste.append({
                "Sembol": s, 
                "Giriş Fiyatı": f"${p['giris_fiyati']:.4f}", 
                "Kaldıraç": f"{p['islem_kaldirac']}x", 
                "Kullanılan Margin": f"${p['islem_margin']:.2f}",
                "Anlık K/Z ($)": f"{anlik_pnl:+.2f}",
                "ROE (%)": f"{pnl_pct:+.2f}%",
                "Liq Riski": f"%{liq_risk_pct:.1f}",
                "Giriş Gerekçesi": giris_nedeni[:60]
            })
            
        st.markdown("#### 📋 Detaylı Tablo")
        st.dataframe(pd.DataFrame(poz_liste), use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    # ─ Finansal Metrikler ─
    k1, k2, k3, k4 = st.columns(4)
    with k1: st.metric("Anlık Fiyat", f"${st.session_state.fiyat:,.4f}" if st.session_state.fiyat else "—", f"%{st.session_state.degisim_24s:+.2f}")
    with k2: 
        hacim = st.session_state.hacim_24s
        hacim_str = f"${hacim/1e6:,.1f}M" if hacim > 1e6 else f"${hacim:,.0f}" if hacim else "—"
        st.metric("24s Hacim", hacim_str)
        
    bakiye = st.session_state.bakiye
    # Toplam Varlık = Boşta Bakiye + Kullanılan Marginler + Tüm Canlı PNL'ler
    toplam = bakiye + aktif_margin_toplami(st.session_state.aktif_pozisyonlar) + aktif_toplam_pnl
    kar_yuzde = ((toplam - st.session_state.baslangic_bakiye) / st.session_state.baslangic_bakiye * 100) if st.session_state.baslangic_bakiye else 0
    
    with k3: st.metric("Toplam Varlık (Tahmini)", f"${toplam:,.2f}", f"%{kar_yuzde:+.2f}")
    with k4: st.metric("Maks Drawdown", f"-%{st.session_state.max_drawdown:.2f}")
    
    prog_val = max(0.0, min(toplam / st.session_state.hedef_bakiye, 1.0)) if st.session_state.hedef_bakiye else 0.0
    st.progress(prog_val)
    st.markdown("---")
    
    col_sol, col_sag = st.columns([2, 1])
    with col_sol:
        st.markdown("<div class='dashboard-header'><b>📋 Vadeli İşlem Geçmişi</b></div>", unsafe_allow_html=True)
        if st.session_state.islem_gecmisi:
            df_log = pd.DataFrame(st.session_state.islem_gecmisi).iloc[::-1].reset_index(drop=True)
            st.dataframe(df_log, use_container_width=True, hide_index=True, height=250)
        else: st.info("Henüz işlem yok.")

with tab_tv:
    st.markdown("### 📈 TradingView Gözlem Ekranı")
    if st.session_state.aktif_sembol and st.session_state.aktif_sembol != "Bekleniyor...":
        tv_symbol = "BINANCE:" + st.session_state.aktif_sembol.replace('/', '')
        tv_html = f"""
        <!-- TradingView Widget BEGIN -->
        <div class="tradingview-widget-container">
          <div class="tradingview-widget-container__widget"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
          {{
          "width": "100%",
          "height": 600,
          "symbol": "{tv_symbol}",
          "interval": "15",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "tr",
          "enable_publishing": false,
          "backgroundColor": "rgba(11, 12, 16, 1)",
          "gridColor": "rgba(42, 46, 57, 0.06)",
          "hide_top_toolbar": false,
          "hide_legend": false,
          "save_image": false,
          "container_id": "tradingview_cf1ea"
        }}
          </script>
        </div>
        <!-- TradingView Widget END -->
        """
        components.html(tv_html, height=600)
    else:
        st.info("Kripto para bekleniyor...")
        
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
