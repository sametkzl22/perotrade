"""
Kripto Paper-Trading Bot — AI Dashboard v2
========================================
Breakout Scanner, Web Trend Simülasyonu ve "AI Strateji Merkezi" ile
güçlendirilmiş akıllı otonom ticaret arayüzü.
"""

import threading
import time
import csv
from datetime import datetime, timezone

import ccxt
import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

import ai_engine

# ─────────────────────────────────────────────
# Sayfa Konfigürasyonu & CSS
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="PeroTrade AI v2 — Breakout Sonarı",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #0b0c10 0%, #1f2833 100%); color: #c5c6c7; font-family: 'Inter', sans-serif; }
[data-testid="stMetric"] { background: rgba(31, 40, 51, 0.8); border: 1px solid #45a29e; border-radius: 12px; padding: 16px; box-shadow: 0 4px 15px rgba(0,0,0,0.4); transition: transform 0.2s; }
[data-testid="stMetric"]:hover { transform: translateY(-3px); }
[data-testid="stMetricValue"] { color: #66fcf1 !important; font-weight: 700; }
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


# ─────────────────────────────────────────────
# Mod Presetleri
# ─────────────────────────────────────────────
MOD_PRESETLERI = {
    "⚡ Agresif Mod": {"risk": 1.0, "sma_kisa": 7, "sma_uzun": 25, "aralik_carpan": 0.5},
    "🌱 Soft Kar Modu": {"risk": 0.30, "sma_kisa": 14, "sma_uzun": 50, "aralik_carpan": 1.5},
}


# ─────────────────────────────────────────────
# Session State
# ─────────────────────────────────────────────
def session_state_baslat():
    default_state = {
        "bot_calisiyor": False,
        "bot_durumu": "Duraklatıldı",
        "bakiye": 10.0,
        "baslangic_bakiye": 10.0,
        "hedef_bakiye": 100.0,
        "coin_miktar": 0.0,
        "pozisyon": "YOK",
        "aktif_sembol": "Bekleniyor...",
        "is_breakout": False,
        "islem_gecmisi": [],
        "ai_dusunce_gunlugu": [],
        "taranan_coinler": [],
        "sonraki_analiz_sn": 0,
        
        # Piyasa & AI Metrikleri
        "fiyat": 0.0,
        "degisim_24s": 0.0,
        "hacim_24s": 0.0,
        "ai_guven_skoru": 0.0,
        "ai_beklenen_artis": 0.0,
        "ai_analiz_ozeti": "Piyasa taranıyor...",
        
        # Ayarlar
        "exchange_adi": "binance",
        "mod": "⚡ Agresif Mod",
        "ai_modu": "Mock AI",
        "openai_key": "",
        
        "lock": threading.Lock(),
        "dur_sinyali": threading.Event(),
    }
    for k, v in default_state.items():
        if k not in st.session_state:
            st.session_state[k] = v

session_state_baslat()


# ─────────────────────────────────────────────
# Yardımcı Araçlar
# ─────────────────────────────────────────────
def islem_gecmisi_kaydet(gecmis: list, dosya="trade_history.csv"):
    if not gecmis: return
    headers = ["zaman", "sembol", "sinyal", "fiyat", "miktar", "bakiye_usdt", "kar_zarar", "ai_notu"]
    with open(dosya, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(gecmis)

def log_ekle(mesaj: str, state, is_breakout=False):
    zaman = datetime.now(timezone.utc).strftime("%H:%M:%S")
    prefix = "🚨 BREAKOUT" if is_breakout else "🧠 INFO"
    state["ai_dusunce_gunlugu"].insert(0, {"time": zaman, "msg": mesaj, "breakout": is_breakout})
    if len(state["ai_dusunce_gunlugu"]) > 50:
        state["ai_dusunce_gunlugu"].pop()

# ─────────────────────────────────────────────
# AI Bot Engine (Arka Plan Thread)
# ─────────────────────────────────────────────
def bot_engine(state, lock: threading.Lock, dur_sinyali: threading.Event):
    exchange = getattr(ccxt, state["exchange_adi"])({"enableRateLimit": True})
    
    while not dur_sinyali.is_set():
        try:
            preset = MOD_PRESETLERI[state["mod"]]
            
            # --- 1. AŞAMA: COIN TARAMASI VE ANORMALLİK TESPİTİ ---
            with lock:
                if state["pozisyon"] == "YOK":
                    log_ekle("🔍 Hacim anormalliği (Breakout) ve Trend taranıyor...", state)
            
            if state["pozisyon"] == "YOK":
                top_coinler = ai_engine.top_coinleri_tara(exchange, limit=30)
                tarama_sonucu = ai_engine.anormallik_tara_ve_sec(exchange, top_coinler, preset["sma_kisa"], preset["sma_uzun"])
                
                secilen_sembol = tarama_sonucu["secilen_sembol"] or "BTC/USDT"
                secilen_pazar = tarama_sonucu.get("secilen_pazar", {})
                secilen_sma = tarama_sonucu.get("secilen_sma", "BEKLE")
                is_breakout = tarama_sonucu.get("secilen_breakout", False)
                taranan_liste = tarama_sonucu.get("taranan_liste", [])
                
                with lock:
                    state["taranan_coinler"] = taranan_liste
                    state["aktif_sembol"] = secilen_sembol
                    state["is_breakout"] = is_breakout
                    
                    if is_breakout:
                        state["bot_durumu"] = "🚨 Breakout Modu!"
                        log_ekle(f"🔥 HACİM PATLAMASI TESPİT EDİLDİ: {secilen_sembol}", state, is_breakout=True)
                    else:
                        state["bot_durumu"] = "Çalışıyor"
                        
            else:
                # Pozisyon varken mevcut coini takip et
                secilen_sembol = state["aktif_sembol"]
                df = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "1h", limit=preset["sma_uzun"]+5)
                secilen_pazar = ai_engine.pazar_durumu_cikar(df, secilen_sembol)
                secilen_sma = ai_engine.sinyal_uret(df, preset["sma_kisa"], preset["sma_uzun"])
                is_breakout = False # Kapatırken breakout flagini düşür
            
            try:
                ticker = exchange.fetch_ticker(secilen_sembol)
                fiyat, degisim, hacim = ticker.get("last", secilen_pazar.get("fiyat", 0)), ticker.get("percentage", 0), ticker.get("quoteVolume", 0)
            except Exception:
                fiyat, degisim, hacim = secilen_pazar.get("fiyat", 0), 0, 0
                
            # --- 2. AŞAMA: YAPAY ZEKA TAHMİNİ ---
            if state["ai_modu"] == "OpenAI LLM" and state["openai_key"]:
                karar_paketi = ai_engine.llm_karar(secilen_sembol, secilen_pazar, secilen_sma, state["openai_key"])
            else:
                skor = ai_engine.kompozit_skor_hesapla(secilen_pazar, secilen_sma)
                karar_paketi = ai_engine.mock_ai_karar(secilen_sembol, secilen_pazar, skor)
                
            # --- 3. AŞAMA: UI GÜNCELLEMESİ & TİCARET YÜRÜTME ---
            with lock:
                state["fiyat"] = fiyat
                state["degisim_24s"] = degisim
                state["hacim_24s"] = hacim
                
                # Yeni AI Panel Metrikleri
                state["ai_guven_skoru"] = karar_paketi.get("guven_skoru", 0.0)
                state["ai_beklenen_artis"] = karar_paketi.get("expected_growth", 0.0)
                state["ai_analiz_ozeti"] = karar_paketi.get("ozet", "Analiz tamamlandı.")
                
                log_ekle(f"🎯 {secilen_sembol} Analizi: {karar_paketi['dusunce']}", state, is_breakout=is_breakout)
                
                # Ticaret Mantığı
                sinyal = karar_paketi["karar"]
                zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                
                if sinyal == "AL" and state["pozisyon"] == "YOK":
                    miktar = (state["bakiye"] * preset["risk"]) / fiyat
                    state["coin_miktar"] = miktar
                    state["bakiye"] = state["bakiye"] * (1 - preset["risk"])
                    state["pozisyon"] = "ACIK"
                    
                    state["islem_gecmisi"].append({
                        "zaman": zaman, "sembol": secilen_sembol, "sinyal": "🟢 AL", 
                        "fiyat": round(fiyat, 2), "miktar": round(miktar, 8), 
                        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": "—", "ai_notu": karar_paketi["dusunce"]
                    })
                    log_ekle(f"💰 İŞLEM AÇILDI: {secilen_sembol} ALINDI. Fiyat: {fiyat:.2f}", state, is_breakout)
                    
                elif sinyal == "SAT" and state["pozisyon"] == "ACIK":
                    gelir = state["coin_miktar"] * fiyat
                    kz = 0.0
                    if state["islem_gecmisi"]:
                        son_alim = [i for i in state["islem_gecmisi"] if "AL" in i["sinyal"]]
                        if son_alim:
                            alis = son_alim[-1]["fiyat"]
                            kz = ((fiyat - alis) / alis) * 100
                            
                    state["bakiye"] += gelir
                    state["coin_miktar"] = 0.0
                    state["pozisyon"] = "YOK"
                    
                    state["islem_gecmisi"].append({
                        "zaman": zaman, "sembol": secilen_sembol, "sinyal": "🔴 SAT", 
                        "fiyat": round(fiyat, 2), "miktar": round(gelir / fiyat, 8), 
                        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": f"%{kz:+.2f}", "ai_notu": karar_paketi["dusunce"]
                    })
                    log_ekle(f"💸 İŞLEM KAPATILDI: {secilen_sembol} SATILDI. KZ: %{kz:+.2f}", state)
                
                # Hedef Kontrolü
                toplam = state["bakiye"] + (state["coin_miktar"] * fiyat)
                if toplam >= state["hedef_bakiye"]:
                    state["bot_durumu"] = "🎯 Hedefe Ulaştı!"
                    state["bot_calisiyor"] = False
                    log_ekle("🏆 HEDEF ULAŞILDI! Bot durduruluyor.", state)
                    islem_gecmisi_kaydet(state["islem_gecmisi"])
                    dur_sinyali.set()
                    return

            # --- 4. AŞAMA: BÖLGE/DÖNGÜ BEKLEMESİ ---
            bekleme_suresi = int(karar_paketi["aralik_sn"])
            if not is_breakout: bekleme_suresi = int(bekleme_suresi * preset["aralik_carpan"])
            
            with lock:
                state["sonraki_analiz_sn"] = bekleme_suresi
                
            for _ in range(bekleme_suresi):
                if dur_sinyali.is_set(): return
                time.sleep(1)
                with lock:
                    state["sonraki_analiz_sn"] -= 1

        except Exception as e:
            with lock:
                log_ekle(f"❌ Hata: {str(e)}", state)
            time.sleep(5)

# ─────────────────────────────────────────────
# Uygulama Arayüzü (UI)
# ─────────────────────────────────────────────
def baslat():
    if not st.session_state.bot_calisiyor:
        st.session_state.dur_sinyali.clear()
        st.session_state.bot_calisiyor = True
        st.session_state.bot_durumu = "Çalışıyor"
        t = threading.Thread(target=bot_engine, args=(st.session_state, st.session_state.lock, st.session_state.dur_sinyali), daemon=True)
        add_script_run_ctx(t)
        t.start()

def durdur():
    st.session_state.dur_sinyali.set()
    st.session_state.bot_calisiyor = False
    if "Hedef" not in st.session_state.bot_durumu:
        st.session_state.bot_durumu = "Duraklatıldı"
    islem_gecmisi_kaydet(st.session_state.islem_gecmisi)


# ─ Sidebar ─
with st.sidebar:
    st.title("🎛️ AI v2 Kontrol")
    
    st.session_state.exchange_adi = st.selectbox("🏦 Borsa", ["binance", "gateio"], disabled=st.session_state.bot_calisiyor)
    
    st.markdown("#### 🧠 Zeka Modeli")
    ai_mod = st.radio("Seçim", ["Mock AI", "OpenAI LLM"], disabled=st.session_state.bot_calisiyor, label_visibility="collapsed")
    st.session_state.ai_modu = ai_mod
    if ai_mod == "OpenAI LLM":
        st.session_state.openai_key = st.text_input("OpenAI API Key (Opsiyonel)", type="password", disabled=st.session_state.bot_calisiyor)
        
    st.markdown("---")
    st.session_state.baslangic_bakiye = st.number_input("Başlangıç (USDT)", min_value=1.0, value=st.session_state.baslangic_bakiye, disabled=st.session_state.bot_calisiyor)
    st.session_state.hedef_bakiye = st.number_input("Hedef (USDT)", min_value=2.0, value=st.session_state.hedef_bakiye, disabled=st.session_state.bot_calisiyor)
    
    if not st.session_state.bot_calisiyor:
        st.session_state.bakiye = st.session_state.baslangic_bakiye

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
col_baslik.markdown("<h1 style='color: #66fcf1; font-weight: 800; margin-bottom: 0;'>🤖 PeroTrade Breakout AI </h1>", unsafe_allow_html=True)

status_class = "status-stopped"
if st.session_state.bot_calisiyor:
    status_class = "status-breakout" if st.session_state.is_breakout else "status-running"
elif "Hedef" in st.session_state.bot_durumu:
    status_class = "status-target"
    
col_durum.markdown(
    f"<div style='text-align:right; margin-top:20px;'>"
    f"<span class='status-badge {status_class}'>"
    f"Durum: {st.session_state.bot_durumu}</span></div>", 
    unsafe_allow_html=True
)

st.markdown(f"<div class='dashboard-header'><b>🎯 Odaklanılan Coin: {st.session_state.aktif_sembol}</b> — Sonraki Analiz: {st.session_state.sonraki_analiz_sn}s</div>", unsafe_allow_html=True)

# ─ AI Strateji ve Tahmin Merkezi (YENİ PANEL) ─
st.markdown("### 🧠 AI Strateji ve Tahmin Merkezi")
m1, m2, m3 = st.columns(3)

with m1:
    bk_yuzde = st.session_state.ai_beklenen_artis
    renk = "#96c93d" if bk_yuzde > 0 else "#FF416C"
    isim = "Yükseliş" if bk_yuzde > 0 else "Düşüş"
    st.markdown(f"""
    <div class="metric-card">
        <h3>Tahmini Değişim</h3>
        <h1 style="color: {renk}">%{abs(bk_yuzde):.2f} {isim}</h1>
        <p>Volatilite ve Trend bazlı tahmin</p>
    </div>
    """, unsafe_allow_html=True)

with m2:
    skor = st.session_state.ai_guven_skoru
    st.markdown(f"""
    <div class="metric-card">
        <h3>AI Güven Skoru</h3>
        <h1 style="color: #66fcf1">%{skor:.0f}</h1>
        <p>Karar mekanizması tutarlılığı</p>
    </div>
    """, unsafe_allow_html=True)
    
with m3:
    ozet = st.session_state.ai_analiz_ozeti
    st.markdown(f"""
    <div class="metric-card" style="padding-top: 30px; padding-bottom: 30px;">
        <h3 style="margin-bottom: 10px; color:#c5c6c7;">Analiz Özeti</h3>
        <span style="font-size: 1.1rem; color: #fff;">{ozet}</span>
    </div>
    """, unsafe_allow_html=True)
    
st.markdown("---")

# ─ Finansal Metrikler ─
k1, k2, k3, k4 = st.columns(4)
with k1: st.metric("Anlık Fiyat", f"${st.session_state.fiyat:,.4f}" if st.session_state.fiyat else "—", f"%{st.session_state.degisim_24s:+.2f}")
with k2: 
    hacim = st.session_state.hacim_24s
    hacim_str = f"${hacim/1e6:,.1f}M" if hacim > 1e6 else f"${hacim:,.0f}" if hacim else "—"
    st.metric("24s Hacim", hacim_str)
    
bakiye = st.session_state.bakiye
coin_deger = st.session_state.coin_miktar * st.session_state.fiyat
toplam = bakiye + coin_deger
kar_yuzde = ((toplam - st.session_state.baslangic_bakiye) / st.session_state.baslangic_bakiye * 100) if st.session_state.baslangic_bakiye else 0

with k3: st.metric("Toplam Portföy", f"${toplam:,.2f}", f"%{kar_yuzde:+.2f}")
with k4: st.metric("Boşta USDT", f"${bakiye:,.2f}")

st.progress(min(toplam / st.session_state.hedef_bakiye, 1.0) if st.session_state.hedef_bakiye else 0.0)

st.markdown("---")

# ─ Veri Tabloları ve Loglar ─
col_sol, col_sag = st.columns([2, 1])

with col_sol:
    st.markdown("<div class='dashboard-header'><b>📋 İşlem Geçmişi</b></div>", unsafe_allow_html=True)
    if st.session_state.islem_gecmisi:
        df_log = pd.DataFrame(st.session_state.islem_gecmisi).iloc[::-1].reset_index(drop=True)
        st.dataframe(df_log, use_container_width=True, hide_index=True, height=250)
    else:
        st.info("Henüz işlem yok.")
        
    st.markdown("<div class='dashboard-header'><b>🔥 Breakout Radarı (Anlık Tarama)</b></div>", unsafe_allow_html=True)
    if st.session_state.taranan_coinler:
        df_scan = pd.DataFrame(st.session_state.taranan_coinler)
        st.dataframe(df_scan, use_container_width=True, hide_index=True)
    else:
        st.info("Piyasa taraması bekleniyor...")

with col_sag:
    st.markdown("<div class='dashboard-header'><b>🧠 AI Düşünce Günlüğü</b></div>", unsafe_allow_html=True)
    log_kutusu = st.container(height=500, border=True)
    for log in st.session_state.ai_dusunce_gunlugu:
        cls_name = 'ai-log-breakout' if log.get('breakout') else 'ai-log-box'
        log_kutusu.markdown(f"<div class='{cls_name}'>[{log['time']}] {log['msg']}</div>", unsafe_allow_html=True)

if st.session_state.bot_calisiyor:
    time.sleep(1) # Daha akıcı UI yenileme
    st.rerun()
