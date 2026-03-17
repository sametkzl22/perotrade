"""
Kripto Paper-Trading Bot — AI Dashboard v5
==========================================
Background Worker mimarisi: Bot arka planda 7/24 çalışır,
UI sadece Global Singleton'dan veri okur.
"""

import threading
import time
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config as cfg
import persistent_state as ps
from bot_worker import BotWorker, GlobalBotState, aktif_margin_toplami, pnl_hesapla


# ─────────────────────────────────────────────
# Singleton Worker (@st.cache_resource)
# ─────────────────────────────────────────────
@st.cache_resource
def get_bot_worker() -> BotWorker:
    """Process boyunca tek instance. Sekme kapansa bile yaşar."""
    return BotWorker()


def get_app_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────
# Streamlit Konfigürasyonu
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="PeroTrade Pro AI v5",
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


# ─────────────────────────────────────────────
# Worker Singleton'ı Al
# ─────────────────────────────────────────────
worker = get_bot_worker()

# UI-only session state (görünüm modu gibi)
if "view_mode" not in st.session_state:
    st.session_state.view_mode = "📊 Profesyonel Dashboard"
if "_onboarding_passed" not in st.session_state:
    st.session_state._onboarding_passed = not worker.state.get("use_real_api", False) and not worker.state.get("api_key_enc", "")
    if worker.state.get("api_key_enc", ""):
        st.session_state._onboarding_passed = True

# State snapshot (thread-safe kopya)
S = worker.state.snapshot()


# ─────────────────────────────────────────────
# Yardımcı UI Fonksiyonları
# ─────────────────────────────────────────────
def gunluk_kar_hesapla_ui(snap):
    gun_baslangic = snap.get("gun_baslangic_bakiye", snap.get("baslangic_bakiye", cfg.INITIAL_BALANCE))
    if gun_baslangic <= 0:
        return 0.0
    mevcut = snap.get("bakiye", gun_baslangic) + aktif_margin_toplami(snap.get("aktif_pozisyonlar", {}))
    return ((mevcut - gun_baslangic) / gun_baslangic) * 100


# ─────────────────────────────────────────────
# Onboarding (API Kurulumu)
# ─────────────────────────────────────────────
def api_kurulum_ekrani():
    st.markdown("<h2 style='text-align: center; color: #f3ba2f;'>🔶 Binance API Kurulumu</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #c5c6c7;'>Gerçek işlem yapmak istiyorsanız API bilgilerinizi girin.</p>", unsafe_allow_html=True)

    with st.container(border=True):
        use_real = st.checkbox("Gerçek Bakiye (Real API) Kullan", value=False)
        api_k = st.text_input("Binance API Key", type="password", disabled=not use_real)
        sec_k = st.text_input("Binance Secret Key", type="password", disabled=not use_real)

        b1, b2, b3 = st.columns([1, 2, 1])
        with b2:
            if st.button("💾 Kaydet ve Başla", use_container_width=True, type="primary"):
                worker.state.set("use_real_api", use_real)
                if use_real:
                    worker.state.set("api_key_enc", ps.encode_key(api_k))
                    worker.state.set("api_secret_enc", ps.encode_key(sec_k))
                worker.state.save_to_persistent()
                st.session_state._onboarding_passed = True
                st.rerun()
    st.stop()


if S.get("use_real_api", False) and not S.get("api_key_enc", ""):
    api_kurulum_ekrani()

if not st.session_state.get("_onboarding_passed", True):
    st.markdown("""<div style='text-align:center; padding: 20px;'><h3>🚀 PeroTrade Pro 7/24 AI Bot'a Hoş Geldiniz!</h3></div>""", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔑 Gerçek Binance API Kurulumu", use_container_width=True):
            worker.state.set("use_real_api", True)
            st.rerun()
    with b2:
        if st.button("🎮 Sanal Parayla (Paper Trading) Başla", use_container_width=True, type="primary"):
            st.session_state._onboarding_passed = True
            st.rerun()
    st.stop()


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Kontrol Paneli")

    # Çalışma Modu
    cur_is_real = S.get("use_real_api", False)
    cur_mod_str = "💰 Real (Binance API)" if cur_is_real else "🎮 Demo (Sanal Para)"
    yeni_mod = st.radio("🕹️ Çalışma Modu", ["🎮 Demo (Sanal Para)", "💰 Real (Binance API)"],
                        index=1 if cur_is_real else 0,
                        disabled=worker.is_running)

    if yeni_mod != cur_mod_str:
        new_real = (yeni_mod == "💰 Real (Binance API)")
        worker.switch_mode(new_real)
        st.rerun()

    st.markdown("---")

    # Görünüm Modu
    st.session_state.view_mode = st.radio(
        "👁️ Görünüm Modu",
        ["📊 Profesyonel Dashboard", "📜 Sadece İşlem Logları"],
        help="Eski PC'lerde performans için 'Sadece İşlem Logları' modunu seçebilirsiniz."
    )
    st.markdown("---")

    st.title("🎛️ AI v5 (7/24 Arka Plan)")

    # API Key gösterimi
    if cur_is_real:
        st.markdown("### 🔑 API Anahtarları")
        api_key_input = st.text_input("API Key", type="password", value=ps.decode_key(S.get("api_key_enc", "")), disabled=worker.is_running)
        api_secret_input = st.text_input("Secret Key", type="password", value=ps.decode_key(S.get("api_secret_enc", "")), disabled=worker.is_running)

        if api_key_input:
            worker.state.set("api_key_enc", ps.encode_key(api_key_input))
        if api_secret_input:
            worker.state.set("api_secret_enc", ps.encode_key(api_secret_input))

    st.markdown("---")

    # Start / Stop
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Başlat", use_container_width=True, type="primary", disabled=worker.is_running):
            worker.start()
            st.rerun()
    with col2:
        if st.button("⏹️ Durdur", use_container_width=True, disabled=not worker.is_running):
            worker.stop()
            st.rerun()

    # Mod seçimi
    st.markdown("---")
    mod_listesi = ["⚡ Agresif Mod", "🌱 Soft Kar Modu", "💎 Ultra-Scalper"]
    mevcut_mod = S.get("mod", "⚡ Agresif Mod")
    mevcut_idx = mod_listesi.index(mevcut_mod) if mevcut_mod in mod_listesi else 0
    secilen_mod = st.selectbox("🎯 İşlem Modu", mod_listesi, index=mevcut_idx)
    if secilen_mod != mevcut_mod:
        worker.state.set("mod", secilen_mod)
        worker.state.save_to_persistent()
        st.rerun()

    # Haber Veto Toggle
    haber_veto_aktif = st.toggle("🛡️ Haber Vetosunu Aktifleştir", value=cfg.ENABLE_NEWS_VETO,
                                  help="Kapatıldığında bot, haberlerdeki 'Savaş', 'Çöküş' gibi kelimeleri yoksayarak sadece teknik verilere göre işlem açar.")
    if haber_veto_aktif != cfg.ENABLE_NEWS_VETO:
        cfg.ENABLE_NEWS_VETO = haber_veto_aktif

    # Bot durumu gösterge
    if worker.is_running:
        st.success(f"🟢 Bot Çalışıyor: {S.get('bot_durumu', 'Çalışıyor')}")
        st.markdown(f"**Sonraki Analiz:** {S.get('sonraki_analiz_sn', 0)} sn")
    else:
        st.warning(f"🔴 Bot Durdu: {S.get('bot_durumu', 'Durduruldu')}")

    # BTC Trendi, Fonlama, MTF
    st.markdown("---")
    st.markdown(f"**₿ BTC Trendi:** {S.get('btc_trendi', 'Taranıyor')}")
    st.markdown(f"**📊 Fonlama:** {S.get('fonlama_orani', 0):.3f}% ({S.get('fonlama_riski', 'Yok')})")
    st.markdown(f"**🔬 MTF Konsensüs:** {S.get('mtf_konsensus', 'KARARSIZ')}")
    st.markdown(f"**🧠 AI Güven:** %{S.get('ai_guven_skoru', 0):.0f}")
    st.markdown(f"**📈 AI Beklenti:** %{S.get('ai_beklenen_artis', 0):+.2f}")

    # Cüzdan Özeti
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 💼 Cüzdan Özeti")
    state_bakiye = S.get("bakiye", 0)
    margin_total = aktif_margin_toplami(S.get("aktif_pozisyonlar", {}))
    st.sidebar.markdown(f"**Toplam Varlık:** ${state_bakiye + margin_total:.2f}")

    if st.sidebar.button("Günlük İstatistikleri ve Kilidi Sıfırla", use_container_width=True, help="Günlük kâr hedefine ulaşıldıysa ve botu tekrar çalıştırmak istiyorsanız bu butona basarak başlangıç bakiyesini güncelleyebilir ve Güvenli Mod'u kapatabilirsiniz."):
        worker.state.set("gun_baslangic_bakiye", state_bakiye + margin_total)
        worker.state.set("bot_durumu", "Çalışıyor (Resetlendi)")
        worker.state.save_to_persistent()
        st.sidebar.success("✅ Günlük İstatistikler ve Kâr Kilidi Sıfırlandı!")
        time.sleep(1)
        st.rerun()

    # 48 Saatlik Demo Takibi
    if not S.get("use_real_api", False):
        st.sidebar.markdown("---")
        st.sidebar.markdown("### ⏳ 48 Saatlik Demo Testi")
        bas_zamani = S.get("baslangic_zamani", 0)
        gecen_saniye = (time.time() - bas_zamani) if bas_zamani > 0 else 0
        kalan_saniye = max(0, (48 * 3600) - gecen_saniye)
        saat = int(kalan_saniye // 3600)
        dakika = int((kalan_saniye % 3600) // 60)
        ilerleme_pct = min(1.0, gecen_saniye / (48 * 3600))

        st.sidebar.progress(ilerleme_pct)
        st.sidebar.markdown(f"**Kalan Süre:** {saat}s {dakika}d")

        if bas_zamani > 0 and kalan_saniye == 0:
            islem_gecmisi = S.get("islem_gecmisi", [])
            kapanan_islemler = [i for i in islem_gecmisi if "KAPAT" in i.get("sinyal", "")]
            pozitifler = [i for i in kapanan_islemler if isinstance(i.get("kar_zarar"), (int, float)) and float(str(i["kar_zarar"]).replace(" USDT", "").replace("+", "")) > 0]
            basari_orani = (len(pozitifler) / len(kapanan_islemler) * 100) if kapanan_islemler else 0

            st.sidebar.success(f"🎉 **2 Günlük Demo Tamamlandı!**\n\n"
                               f"📊 **Toplam İşlem:** {len(kapanan_islemler)}\n"
                               f"🎯 **Başarı Oranı:** %{basari_orani:.1f}\n"
                               f"💰 **Toplam Kâr:** ${state_bakiye - 100.0:.2f}")


# ─────────────────────────────────────────────
# LOG-ONLY MODE
# ─────────────────────────────────────────────
if st.session_state.view_mode == "📜 Sadece İşlem Logları":
    st.markdown("<h1 style='color: #66fcf1;'>📜 İşlem Logları (Full Screen)</h1>", unsafe_allow_html=True)

    # Düşünce Günlüğü
    st.markdown("### 🧠 AI Düşünce Günlüğü")
    log_kutusu = st.container(height=400, border=True)
    for log in S.get("ai_dusunce_gunlugu", []):
        cls_name = 'ai-log-breakout' if log.get('liq') or log.get('breakout') else 'ai-log-box'
        if '🛡️' in log.get('msg', ''):
            cls_name = 'ai-log-breakout'
        log_kutusu.markdown(f"<div class='{cls_name}'>[{log.get('time', '')}] {log.get('msg', '')}</div>", unsafe_allow_html=True)

    # İşlem Geçmişi
    st.markdown("### 📋 İşlem Geçmişi")
    islem_gecmisi = S.get("islem_gecmisi", [])
    if islem_gecmisi:
        df_log = pd.DataFrame(islem_gecmisi).iloc[::-1].reset_index(drop=True)
        st.dataframe(df_log, use_container_width=True, hide_index=True)
    else:
        st.info("Henüz işlem yok.")

    if worker.is_running:
        time.sleep(0.5)
        st.rerun()
    st.stop()


# ─────────────────────────────────────────────
# DASHBOARD MODE
# ─────────────────────────────────────────────
# Demo modu banner
if not S.get("use_real_api", False):
    st.markdown("<div style='background: #ff4b4b; color: white; padding: 10px; text-align: center; border-radius: 8px; font-weight: bold; margin-bottom: 20px;'>⚠️ DEMO MODU AKTİF - İşlemler Sanal Para İle Simüle Ediliyor</div>", unsafe_allow_html=True)

# Başlık
col_baslik, col_durum = st.columns([3, 1])
col_baslik.markdown("<h1 style='color: #66fcf1; font-weight: 800; margin-bottom: 0;'>🚀 PeroTrade Pro AI v5 (7/24)</h1>", unsafe_allow_html=True)

status_class = "status-stopped"
if worker.is_running:
    status_class = "status-breakout" if S.get("is_breakout") else "status-running"
elif "Hedef" in S.get("bot_durumu", ""):
    status_class = "status-target"

col_durum.markdown(f"<div style='text-align:right; margin-top:20px;'><span class='status-badge {status_class}'>Durum: {S.get('bot_durumu', 'Durduruldu')}</span></div>", unsafe_allow_html=True)
st.markdown(f"<div class='dashboard-header'><b>🎯 Odaklanılan Ticker: {S.get('aktif_sembol', 'Bekleniyor...')}</b> — Risk Barometresi: {S.get('global_risk_seviyesi', 'Normal')}</div>", unsafe_allow_html=True)

# Performans Metrikleri
st.markdown("---")
st.markdown("### 💼 Cüzdan & Sağlık")
bky = S.get("bakiye", 0)
kullanilan = aktif_margin_toplami(S.get("aktif_pozisyonlar", {}))
tplm = bky + kullanilan

st.metric("Toplam Varlık", f"${tplm:,.2f}")
st.metric("Boşta USDT", f"${bky:,.2f}")
st.metric("Kullanılan Margin", f"${kullanilan:,.2f}")

gecen_sure = (time.time() - S.get("baslangic_zamani", 0)) / 3600 if S.get("baslangic_zamani", 0) > 0 else 0
kalan_sure = max(0, S.get("hedef_sure_saat", 24) - gecen_sure)
if worker.is_running:
    st.info(f"⏳ Kalan Hedef Süresi: {kalan_sure:.1f} Saat")

st.markdown("---")
st.markdown("### 📈 Günlük Performans Takibi")
gunluk_pnl = gunluk_kar_hesapla_ui(S)
hedef_pct = 10.0

gauge_pct = max(0.0, min(gunluk_pnl / hedef_pct, 1.0)) if hedef_pct > 0 else 0.0
if gunluk_pnl >= hedef_pct:
    gauge_renk, gauge_emoji, gauge_durum = "#00ff88", "🏆", "HEDEF TAMAM!"
elif gunluk_pnl >= 0:
    gauge_renk, gauge_emoji, gauge_durum = "#66fcf1", "📈", "Kârda"
else:
    gauge_renk, gauge_emoji, gauge_durum = "#ff4444", "📉", "Zararda"

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

# Portföy Değeri Grafiği
if S.get("cuzdan_gecmisi"):
    st.markdown("### 📉 Portföy Değeri (Anlık)")
    chart_data = pd.DataFrame(S["cuzdan_gecmisi"])
    st.line_chart(chart_data.set_index("zaman")["deger"], use_container_width=True, color="#66fcf1")


# ─────────────────────────────────────────────
# Dashboard Tabs
# ─────────────────────────────────────────────
tab_dash, tab_tv = st.tabs(["📊 Dashboard", "📈 Grafikler (TradingView)"])

with tab_dash:
    st.markdown("### 💼 Cüzdan Özeti")
    state_bakiye = S.get("bakiye", 0)
    aktif_pozlar = S.get("aktif_pozisyonlar", {})
    margin_total = aktif_margin_toplami(aktif_pozlar)
    fiyat_haritasi = S.get("guncel_fiyatlar", {})
    
    aktif_toplam_pnl = 0.0
    for s, p in aktif_pozlar.items():
        try:
            gf = fiyat_haritasi.get(s, S.get("fiyat", 0) if s == S.get("aktif_sembol") else p.get('giris_fiyati', 0))
            if gf > 0 and p.get('giris_fiyati', 0) > 0:
                pnl = pnl_hesapla(p.get('pozisyon', 'YOK'), p.get('giris_fiyati', 0), gf, 
                                  p.get('islem_margin', 0) * p.get('islem_kaldirac', 1), p.get('islem_kaldirac', 1))
                if abs((pnl / p.get('islem_margin', 1)) * 100) <= 500:
                    aktif_toplam_pnl += pnl
        except Exception:
            pass

    c1, c2, c3 = st.columns(3)
    c1.metric("Kullanılabilir USDT", f"${state_bakiye:.2f}")
    c2.metric("İşlemdeki Margin", f"${margin_total:.2f}")
    c3.metric("Toplam Varlık", f"${state_bakiye + margin_total + aktif_toplam_pnl:.2f}", delta=f"{aktif_toplam_pnl:+.2f} USDT")

    st.markdown("---")
    st.markdown("### 📊 Aktif Pozisyonlar Paneli")
    aktif_toplam_pnl = 0.0

    if not aktif_pozlar:
        st.info("Açık Pozisyon Bulunmuyor.")
    else:
        st.markdown("#### ⚡ Anlık Durum Kartları")
        poz_liste = []
        for idx, (s, p) in enumerate(aktif_pozlar.items()):
            try:
                fiyat_haritasi = S.get("guncel_fiyatlar", {})
                guncel_fiyat = fiyat_haritasi.get(s, S.get("fiyat", 0) if s == S.get("aktif_sembol") else p.get('giris_fiyati', 0))
                if guncel_fiyat <= 0 or p.get('giris_fiyati', 0) <= 0:
                    anlik_pnl = 0.0
                    pnl_pct = 0.0
                else:
                    anlik_pnl = pnl_hesapla(p.get('pozisyon', 'YOK'), p.get('giris_fiyati', 0), guncel_fiyat,
                                             p.get('islem_margin', 0) * p.get('islem_kaldirac', 1), p.get('islem_kaldirac', 1))
                    pnl_pct = (anlik_pnl / p.get('islem_margin', 1)) * 100 if p.get('islem_margin', 0) > 0 else 0

                if abs(pnl_pct) > 500:
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
            liq_risk_pct = abs((guncel_fiyat - p.get('likidasyon_fiyati', 0)) / guncel_fiyat * 100) if guncel_fiyat > 0 else 0

            st.markdown(f"""
            <div style='background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; padding: 16px; margin-bottom: 12px; border-left: 4px solid {pnl_renk};'>
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;'>
                    <span style='font-size: 18px; font-weight: 700; color: #66fcf1;'>{s} ({p.get('pozisyon', '?')} {p.get('islem_kaldirac', 0)}x)</span>
                    <span style='font-size: 20px; font-weight: 800; color: {pnl_renk};'>{anlik_pnl:+.2f} USDT ({pnl_pct:+.1f}%)</span>
                </div>
                <div style='display: flex; gap: 24px; color: #c5c6c7; font-size: 13px; margin-bottom: 6px;'>
                    <span>💰 Giriş: <b>${p.get('giris_fiyati', 0):.4f}</b></span>
                    <span>📊 Anlık: <b>${guncel_fiyat:.4f}</b></span>
                    <span>🛡️ Margin: <b>${p.get('islem_margin', 0):.2f}</b></span>
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
                "Giriş Fiyatı": f"${p.get('giris_fiyati', 0):.4f}",
                "Kaldıraç": f"{p.get('islem_kaldirac', 0)}x",
                "Kullanılan Margin": f"${p.get('islem_margin', 0):.2f}",
                "Anlık K/Z ($)": f"{anlik_pnl:+.2f}",
                "ROE (%)": f"{pnl_pct:+.2f}%",
                "Liq Riski": f"%{liq_risk_pct:.1f}",
                "Giriş Gerekçesi": giris_nedeni[:60]
            })

        st.markdown("#### 📋 Detaylı Tablo")
        st.dataframe(pd.DataFrame(poz_liste), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Finansal Metrikler
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Anlık Fiyat", f"${S.get('fiyat', 0):,.4f}" if S.get("fiyat") else "—", f"%{S.get('degisim_24s', 0):+.2f}")
    with k2:
        hacim = S.get("hacim_24s", 0)
        hacim_str = f"${hacim/1e6:,.1f}M" if hacim > 1e6 else f"${hacim:,.0f}" if hacim else "—"
        st.metric("24s Hacim", hacim_str)

    bakiye = S.get("bakiye", 0)
    toplam = bakiye + aktif_margin_toplami(S.get("aktif_pozisyonlar", {})) + aktif_toplam_pnl
    baslangic_bky = S.get("baslangic_bakiye", cfg.INITIAL_BALANCE)
    kar_yuzde = ((toplam - baslangic_bky) / baslangic_bky * 100) if baslangic_bky else 0

    with k3:
        st.metric("Toplam Varlık (Tahmini)", f"${toplam:,.2f}", f"%{kar_yuzde:+.2f}")
    with k4:
        st.metric("Maks Drawdown", f"-%{S.get('max_drawdown', 0):.2f}")

    hedef_bky = S.get("hedef_bakiye", cfg.TARGET_BALANCE)
    prog_val = max(0.0, min(toplam / hedef_bky, 1.0)) if hedef_bky else 0.0
    st.progress(prog_val)
    st.markdown("---")

    col_sol, col_sag = st.columns([2, 1])
    with col_sol:
        st.markdown("<div class='dashboard-header'><b>📋 Vadeli İşlem Geçmişi</b></div>", unsafe_allow_html=True)
        islem_gecmisi = S.get("islem_gecmisi", [])
        if islem_gecmisi:
            df_log = pd.DataFrame(islem_gecmisi).iloc[::-1].reset_index(drop=True)
            st.dataframe(df_log, use_container_width=True, hide_index=True, height=250)
        else:
            st.info("Henüz işlem yok.")

    with col_sag:
        st.markdown("<div class='dashboard-header'><b>🧠 Pro Live Düşünce Günlüğü</b></div>", unsafe_allow_html=True)
        log_kutusu = st.container(height=500, border=True)
        for log in S.get("ai_dusunce_gunlugu", []):
            cls_name = 'ai-log-breakout' if log.get('liq') or log.get('breakout') else 'ai-log-box'
            if '🛡️' in log.get('msg', ''):
                cls_name = 'ai-log-breakout'
            log_kutusu.markdown(f"<div class='{cls_name}'>[{log.get('time', '')}] {log.get('msg', '')}</div>", unsafe_allow_html=True)

with tab_tv:
    st.markdown("### 📈 TradingView Gözlem Ekranı")
    aktif_s = S.get("aktif_sembol", "")
    if aktif_s and aktif_s != "Bekleniyor...":
        tv_symbol = "BINANCE:" + aktif_s.replace('/', '')
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
    taranan = S.get("taranan_coinler", [])
    if taranan:
        df_scan = pd.DataFrame(taranan)
        st.dataframe(df_scan, use_container_width=True, hide_index=True)
    else:
        st.info("Piyasa taraması bekleniyor...")


# ─────────────────────────────────────────────
# Auto-Refresh (Bot çalışırken)
# ─────────────────────────────────────────────
if worker.is_running:
    time.sleep(0.3)
    st.rerun()
