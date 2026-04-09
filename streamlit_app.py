"""
PeroTrade Pro — Ultra-Lightweight Dashboard Observer
=====================================================
Sıfır ağır bağımlılık: ccxt, xgboost, joblib, bot_worker ASLA import edilmez.
Sadece utils.py (math), persistent_state.py (JSON I/O), config.py (sabitler).

Veri Kaynağı: persistent_state.json (okuma)
IPC Kanalı:   data/ui_settings.json, data/stop_signal.flag, data/close_commands.json
"""

import json
import time
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config as cfg
import persistent_state as ps
from utils import aktif_margin_toplami, pnl_hesapla, pnl_hesapla_coklu, gunluk_kar_hesapla


# ─────────────────────────────────────────────
# IPC Helpers (Dashboard → Engine)
# ─────────────────────────────────────────────
_APP_DIR = ps.get_app_path()
_DATA_DIR = os.path.join(_APP_DIR, "data")


def _engine_is_running() -> bool:
    """Engine çalışıyor mu? Lock dosyasını kontrol eder."""
    return os.path.exists(ps.get_lock_file_path())


def _write_ui_setting(key: str, value):
    """Dashboard'dan engine'e tek bir ayar gönder."""
    settings_file = os.path.join(_DATA_DIR, "ui_settings.json")
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        existing = {}
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing[key] = value
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _write_ui_settings(updates: dict):
    """Birden fazla ayarı toplu gönder."""
    settings_file = os.path.join(_DATA_DIR, "ui_settings.json")
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        existing = {}
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.update(updates)
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _request_stop():
    """Engine'e durdurma sinyali gönder (IPC flag)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    Path(os.path.join(_DATA_DIR, "stop_signal.flag")).touch()


def _request_start():
    """Engine'e başlatma sinyali gönder (IPC flag)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    Path(os.path.join(_DATA_DIR, "start_signal.flag")).touch()


def _request_close_trade(trade_id: str, fiyat: float = 0):
    """Engine'e pozisyon kapatma komutu gönder (IPC command)."""
    cmd_file = os.path.join(_DATA_DIR, "close_commands.json")
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        existing = []
        if os.path.exists(cmd_file):
            with open(cmd_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append({"trade_id": trade_id, "fiyat": fiyat})
        with open(cmd_file, "w", encoding="utf-8") as f:
            json.dump(existing, f)
    except Exception:
        pass


def _switch_mode(use_real_api: bool):
    """Demo/Real mod değiştir."""
    ps.set_last_mode(use_real_api)
    _write_ui_setting("use_real_api", use_real_api)


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
# State Okuma (Persistent JSON — Read-Only Snapshot)
# ─────────────────────────────────────────────
S = ps.state_yukle()
_is_running = _engine_is_running()

# UI-only session state (görünüm modu gibi)
if "view_mode" not in st.session_state:
    st.session_state.view_mode = "📊 Profesyonel Dashboard"
if "_onboarding_passed" not in st.session_state:
    st.session_state._onboarding_passed = not S.get("use_real_api", False) and not S.get("api_key_enc", "")
    if S.get("api_key_enc", ""):
        st.session_state._onboarding_passed = True


# ─────────────────────────────────────────────
# Yardımcı UI Fonksiyonları
# ─────────────────────────────────────────────
# gunluk_kar_hesapla artık utils.py'den geliyor (import satırında yukarıda)
guunluk_kar_hesapla_ui = gunluk_kar_hesapla  # backward compat alias


# ─────────────────────────────────────────────
# Onboarding (API Kurulumu)
# ─────────────────────────────────────────────
def api_kurulum_ekrani():
    st.markdown("<h2 style='text-align: center; color: #f3ba2f;'>🔶 Binance API Kurulumu</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #c5c6c7;'>Gerçek işlem yapmak istiyorsanız API bilgilerinizi girin.</p>", unsafe_allow_html=True)

    err_placeholder = st.empty()

    with st.container(border=True):
        use_real = st.checkbox("Gerçek Bakiye (Real API) Kullan", value=False)
        with st.form("onb_form"):
            api_k = st.text_input("Binance API Key", type="password", key="onb_api", disabled=not use_real)
            sec_k = st.text_input("Binance Secret Key", type="password", key="onb_sec", disabled=not use_real)
            
            b1, b2, b3 = st.columns([1, 2, 1])
            with b2:
                submitted = st.form_submit_button("💾 Kaydet ve Başla", type="primary", use_container_width=True)
            
            if submitted:
                if use_real:
                    if not api_k or not sec_k:
                        err_placeholder.error("⚠️ Lütfen Binance API Key ve Secret Key bilgilerini girin!")
                    else:
                        try:
                            import ccxt
                            test_exc = ccxt.binance({
                                'apiKey': api_k,
                                'secret': sec_k,
                                'enableRateLimit': True,
                                'options': {'defaultType': getattr(cfg, "FUTURES_TYPE", "future")}
                            })
                            test_exc.fetch_balance()
                            
                            # IPC: Engine'e API keys gönder + persistent state'e kaydet
                            enc_key = ps.encode_key(api_k)
                            enc_sec = ps.encode_key(sec_k)
                            _write_ui_settings({
                                "use_real_api": True,
                                "api_key_enc": enc_key,
                                "api_secret_enc": enc_sec,
                            })
                            ps.set_last_mode(True)
                            # Persistent state'e de yaz (crash recovery için)
                            state = ps.state_yukle()
                            state["use_real_api"] = True
                            state["api_key_enc"] = enc_key
                            state["api_secret_enc"] = enc_sec
                            ps.state_kaydet(state)
                            st.session_state._onboarding_passed = True
                            st.rerun()
                        except Exception as e:
                            err_placeholder.error(f"❌ API Bağlantı Hatası: Lütfen anahtarlarınızı kontrol edin. ({e})")
                else:
                    _write_ui_setting("use_real_api", False)
                    ps.set_last_mode(False)
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
            _write_ui_setting("use_real_api", True)
            ps.set_last_mode(True)
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

    # Motor durumu banner
    if not _is_running:
        st.warning("⚠️ Motor çalışmıyor. `python3 bot.py` ile başlatın.")

    # Çalışma Modu
    cur_is_real = S.get("use_real_api", False)
    cur_mod_str = "💰 Real (Binance API)" if cur_is_real else "🎮 Demo (Sanal Para)"
    yeni_mod = st.radio("🕹️ Çalışma Modu", ["🎮 Demo (Sanal Para)", "💰 Real (Binance API)"],
                        index=1 if cur_is_real else 0,
                        disabled=_is_running)

    if yeni_mod != cur_mod_str:
        new_real = (yeni_mod == "💰 Real (Binance API)")
        _switch_mode(new_real)
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
    
    if S.get("auth_error_notified"):
        st.error(f"⚠️ API Kimlik Hatası: {S.get('auth_error_msg', 'Bağlantı/Yetki hatası')}")

    # API Key gösterimi (Tüm Modlarda Görünür)
    st.markdown("### 🔑 API Anahtarları")
    
    # st.session_state ile persistence sağlanıyor
    if "binance_key_input" not in st.session_state:
        st.session_state.binance_key_input = ps.decode_key(S.get("api_key_enc", ""))
    if "binance_secret_input" not in st.session_state:
        st.session_state.binance_secret_input = ps.decode_key(S.get("api_secret_enc", ""))
        
    ping_res = st.empty()
    
    with st.form(key='api_form'):
        api_k = st.text_input("API Key", type="password", key="binance_key_input")
        sec_k = st.text_input("Secret Key", type="password", key="binance_secret_input")
        submitted = st.form_submit_button(label='Ayarları Uygula ve Kaydet')
        
        if submitted:
            if not api_k or not sec_k:
                if cur_is_real:
                    ping_res.error("⚠️ Lütfen API Key ve Secret Key giriniz.")
            else:
                try:
                    import ccxt
                    ping_res.info("🔄 API bağlantısı test ediliyor...")
                    test_exc = ccxt.binance({
                        'apiKey': api_k,
                        'secret': sec_k,
                        'enableRateLimit': True,
                        'options': {'defaultType': getattr(cfg, "FUTURES_TYPE", "future")}
                    })
                    
                    bal_data = test_exc.fetch_balance()
                    free_usdt = float(bal_data.get('USDT', {}).get('free', 0.0))
                    
                    # IPC: Engine'e gönder + persistent state'e kaydet
                    enc_key = ps.encode_key(api_k)
                    enc_sec = ps.encode_key(sec_k)
                    _write_ui_settings({
                        "api_key_enc": enc_key,
                        "api_secret_enc": enc_sec,
                    })
                    state = ps.state_yukle()
                    state["api_key_enc"] = enc_key
                    state["api_secret_enc"] = enc_sec
                    ps.state_kaydet(state)
                    
                    st.session_state.balance = free_usdt
                    ping_res.success(f"✅ Doğrulandı ve Kaydedildi! Gerçek Bakiye: ${free_usdt:.2f}")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    if "Authentication" in str(e):
                        ping_res.error("❌ Kimlik Doğrulama Hatası: API anahtarlarınız geçersiz, IP kısıtlaması var veya süresi dolmuş.")
                    else:
                        ping_res.error(f"❌ Bağlantı Başarısız: ({str(e)[:60]})")

    st.markdown("---")

    # Start / Stop (IPC ile)
    col1, col2 = st.columns(2)
    start_err = st.empty()
    with col1:
        start_disabled = _is_running
        if st.button("▶️ Başlat", use_container_width=True, type="primary", disabled=start_disabled):
            k_val = st.session_state.get("binance_key_input", "")
            s_val = st.session_state.get("binance_secret_input", "")
            if cur_is_real and (not k_val or not s_val):
                start_err.error("⚠️ Lütfen Real Mode için API Key ve Secret girin!")
            else:
                _request_start()
                time.sleep(1)
                st.rerun()
    with col2:
        if st.button("⏹️ Durdur", use_container_width=True, disabled=not _is_running):
            _request_stop()
            time.sleep(1)
            st.rerun()

    # Mod seçimi
    st.markdown("---")
    mod_listesi = ["⚡ Agresif Mod", "🌱 Soft Kar Modu", "💎 Ultra-Scalper", "🚀 94-Day Challenge", "🚀 Evolutionary Trainer"]
    mevcut_mod = S.get("mod", "⚡ Agresif Mod")
    mevcut_idx = mod_listesi.index(mevcut_mod) if mevcut_mod in mod_listesi else 0
    secilen_mod = st.selectbox("🎯 İşlem Modu", mod_listesi, index=mevcut_idx)
    if secilen_mod != mevcut_mod:
        _write_ui_setting("mod", secilen_mod)
        # Challenge mod aktivasyonu
        if secilen_mod == "🚀 94-Day Challenge":
            ch = S.get("challenge_session", {})
            if not isinstance(ch, dict):
                ch = {}
            if not ch.get("aktif"):
                st.session_state["_challenge_setup_pending"] = True
        st.rerun()

    # Challenge başlangıç sermayesi kurulum dialogu
    if st.session_state.get("_challenge_setup_pending", False):
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🚀 Challenge Kurulumu")
        ch_baslangic_sermaye = st.sidebar.number_input(
            "Başlangıç Sermayesi ($)", min_value=1.0, max_value=10000.0,
            value=10.0, step=1.0, help="Challenge boyunca sanal bakiye olarak kullanılacak tutar."
        )
        if st.sidebar.button("✅ Challenge'ı Başlat", use_container_width=True, type="primary"):
            import time as _time
            ch_yeni = {
                "aktif": True,
                "baslangic_bakiye": ch_baslangic_sermaye,
                "gun_baslangic_bakiye": ch_baslangic_sermaye,
                "bakiye": ch_baslangic_sermaye,
                "pik_bakiye": ch_baslangic_sermaye,
                "gun": 1,
                "baslangic_zamani": _time.time(),
                "gun_baslangic_zamani": _time.time(),
                "toplam_islem": 0,
                "toplam_kar": 0.0,
                "gunluk_pik_kar_pct": 0.0,
                "trailing_stop_seviyesi": 0.0,
                "islem_gecmisi": [],
                "cuzdan_gecmisi": [],
                "max_drawdown": 0.0,
            }
            _write_ui_setting("challenge_session", ch_yeni)
            st.session_state["_challenge_setup_pending"] = False
            st.sidebar.success(f"✅ Challenge başlatıldı! Sanal Sermaye: ${ch_baslangic_sermaye:.2f}")
            time.sleep(1)
            st.rerun()

    # Haber Veto Toggle
    haber_veto_aktif = st.toggle("🛡️ Haber Vetosunu Aktifleştir", value=cfg.ENABLE_NEWS_VETO,
                                  help="Kapatıldığında bot, haberlerdeki 'Savaş', 'Çöküş' gibi kelimeleri yoksayarak sadece teknik verilere göre işlem açar.")
    if haber_veto_aktif != cfg.ENABLE_NEWS_VETO:
        cfg.ENABLE_NEWS_VETO = haber_veto_aktif
        _write_ui_setting("ENABLE_NEWS_VETO", haber_veto_aktif)

    # Opsiyonel Martingale Toggle
    mart_aktif = st.toggle("🔄 Martingale Stratejisi (Deneysel)", value=S.get("martingale_aktif", False), 
                           help="Sadece kayıplı işlemlerde bakiyeyi korumak için margin miktarını katlayarak yeni işlem açar.")
    if mart_aktif != S.get("martingale_aktif", False):
        _write_ui_setting("martingale_aktif", mart_aktif)
        
    # V19: Gelişmiş Risk Yönetimi Slider'ları
    st.sidebar.markdown("### 🛡️ Risk Yönetimi (V29)")
    max_wallet_risk_pct = st.sidebar.slider("Max Cüzdan Riski (%)", min_value=1.0, max_value=100.0, value=float(S.get("max_wallet_risk_pct", getattr(cfg, "MAX_WALLET_RISK_PCT", 100.0))), help="Toplam varlığın en fazla yüzde kaçı margin olarak risk edilebilir?")
    
    # V29: Free Will aktifken slider disabled gösterilir
    if getattr(cfg, "CONFIDENCE_BASED_SIZING", False):
        st.sidebar.slider("İşlem Başına Risk (%)", min_value=1.0, max_value=50.0, value=float(S.get("trade_risk_pct", getattr(cfg, "TRADE_RISK_PCT", 10.0))), disabled=True, help="⚠️ V29 Özgür İrade Aktif: Bot güvene göre otomatik ayar yapıyor")
        st.sidebar.info("🧠 V29 Özgür İrade Aktif: Bot, AI güven skoruna göre margin'i otomatik ayarlıyor. (%98+ → %50, %90-97 → %25, <%90 → %15)")
        trade_risk_pct = S.get("trade_risk_pct", getattr(cfg, "TRADE_RISK_PCT", 10.0))
    else:
        trade_risk_pct = st.sidebar.slider("İşlem Başına Risk (%)", min_value=1.0, max_value=50.0, value=float(S.get("trade_risk_pct", getattr(cfg, "TRADE_RISK_PCT", 10.0))), help="Her işleme toplam cüzdanın en fazla yüzde kaçı margin olarak ayrılabilir?")
    
    if max_wallet_risk_pct != S.get("max_wallet_risk_pct") or trade_risk_pct != S.get("trade_risk_pct"):
        _write_ui_settings({"max_wallet_risk_pct": max_wallet_risk_pct, "trade_risk_pct": trade_risk_pct})

    # Bot durumu gösterge
    if _is_running:
        st.markdown(f"**🔵 Durum:** {S.get('bot_durumu', 'Çalışıyor')} (Analiz: {S.get('sonraki_analiz_sn', 0)}sn)")
    else:
        st.markdown(f"**🔴 Durum:** {S.get('bot_durumu', 'Durduruldu')}")

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
    st.sidebar.markdown(f"**💵 Boşta Kalan Para:** ${state_bakiye:.2f}")
    st.sidebar.markdown(f"**🔒 İşlemdeki Margin:** ${margin_total:.2f}")

    if st.sidebar.button("Günlük İstatistikleri ve Kilidi Sıfırla", use_container_width=True, help="Günlük kâr hedefine ulaşıldıysa ve botu tekrar çalıştırmak istiyorsanız bu butona basarak başlangıç bakiyesini güncelleyebilir ve Güvenli Mod'u kapatabilirsiniz."):
        _write_ui_settings({
            "gun_baslangic_bakiye": state_bakiye + margin_total,
            "bot_durumu": "Çalışıyor (Resetlendi)",
        })
        st.sidebar.success("✅ Günlük İstatistikler ve Kâr Kilidi Sıfırlandı!")
        time.sleep(1)
        st.rerun()

    # ====== 🚀 94-Day Challenge Dashboard ======
    ch_data = S.get("challenge_session", {})
    if S.get("mod") == "🚀 94-Day Challenge" and isinstance(ch_data, dict) and ch_data.get("aktif"):
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🚀 94-Day Challenge")

        ch_gun = ch_data.get("gun", ch_data.get("current_day", 1))
        ch_toplam_gun = getattr(cfg, "CHALLENGE_TOTAL_DAYS", 94)
        ch_baslangic = ch_data.get("baslangic_bakiye", 10.0)
        ch_gun_bas = ch_data.get("gun_baslangic_bakiye", 10.0)
        ch_bakiye = ch_data.get("bakiye", ch_gun_bas)
        ch_hedef = getattr(cfg, "CHALLENGE_TARGET_BALANCE", 100000.0)
        ch_daily_target = getattr(cfg, "CHALLENGE_DAILY_TARGET_PCT", 10.0)

        ch_gunluk_pnl = ((ch_bakiye - ch_gun_bas) / ch_gun_bas * 100) if ch_gun_bas > 0 else 0
        ch_kalan_pct = max(0.0, ch_daily_target - ch_gunluk_pnl)

        import math
        if ch_bakiye > ch_baslangic and ch_hedef > ch_baslangic:
            ch_progress = min(1.0, math.log(ch_bakiye / ch_baslangic) / math.log(ch_hedef / ch_baslangic))
        else:
            ch_progress = 0.0

        ch_ts = ch_data.get("trailing_stop_seviyesi", 0.0)

        ch_pnl_renk = "#00ff88" if ch_gunluk_pnl >= 0 else "#ff4444"
        ch_gun_emoji = "🎯" if ch_gunluk_pnl >= ch_daily_target else "📈" if ch_gunluk_pnl > 0 else "📉"

        st.sidebar.markdown(f"""
        <div style='background: linear-gradient(135deg, #1a1a2e, #16213e); border-radius: 12px; padding: 16px; border: 1px solid #f7971e; margin-bottom: 10px;'>
            <div style='font-size: 16px; font-weight: 800; color: #f7971e; margin-bottom: 12px;'>🏆 Challenge Status</div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 8px;'>
                <span style='color: #c5c6c7;'>📅 Challenge Günü:</span>
                <span style='color: #66fcf1; font-weight: 700;'>{ch_gun}/{ch_toplam_gun}</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 8px;'>
                <span style='color: #c5c6c7;'>{ch_gun_emoji} Bugünkü Hedef:</span>
                <span style='color: {ch_pnl_renk}; font-weight: 700;'>%{ch_daily_target:.0f} (Kalan: %{ch_kalan_pct:.1f})</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 8px;'>
                <span style='color: #c5c6c7;'>💰 Challenge Bakiye:</span>
                <span style='color: #66fcf1; font-weight: 700;'>${ch_bakiye:,.2f}</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 8px;'>
                <span style='color: #c5c6c7;'>📊 Günlük PNL:</span>
                <span style='color: {ch_pnl_renk}; font-weight: 700;'>%{ch_gunluk_pnl:+.2f}</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 8px;'>
                <span style='color: #c5c6c7;'>🏁 Başlangıç Sermayesi:</span>
                <span style='color: #888; font-weight: 700;'>${ch_baslangic:,.2f}</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-bottom: 12px;'>
                <span style='color: #c5c6c7;'>🛡️ Trailing Stop:</span>
                <span style='color: #f7971e; font-weight: 700;'>{'%' + f'{ch_ts:.1f}' if ch_ts > 0 else 'Pasif (< %10)'}</span>
            </div>
            <div style='margin-bottom: 4px; font-size: 13px; color: #c5c6c7;'>🚀 100.000$ Yolculuğu: %{ch_progress*100:.1f} Tamamlandı</div>
            <div style='background: #1a1a2e; border-radius: 8px; height: 12px; overflow: hidden;'>
                <div style='background: linear-gradient(90deg, #f7971e, #ffd200); height: 100%; width: {ch_progress*100:.0f}%; border-radius: 8px; transition: width 0.3s;'></div>
            </div>
            <div style='display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: #888;'>
                <span>${ch_baslangic:,.0f}</span>
                <span style='color: #ffd200; font-weight: 600;'>${ch_bakiye:,.2f}</span>
                <span>${ch_hedef:,.0f}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.sidebar.button("🔄 Challenge Verilerini Sıfırla", use_container_width=True,
                             help="Sadece challenge gününü ve bakiyesini sıfırlar. AI eğitimi için kritik olan trade_logs.db veritabanına DOKUNMAZ."):
            import time as _time
            yeni_ch = {
                "aktif": True,
                "baslangic_bakiye": ch_baslangic,
                "gun_baslangic_bakiye": ch_baslangic,
                "bakiye": ch_baslangic,
                "pik_bakiye": ch_baslangic,
                "current_day": 1,
                "gun": 1,
                "baslangic_zamani": _time.time(),
                "gun_baslangic_zamani": _time.time(),
                "toplam_islem": 0,
                "toplam_kar": 0.0,
                "gunluk_pik_kar_pct": 0.0,
                "trailing_stop_seviyesi": 0.0,
                "target_achieved": False,
                "accumulated_pnl": 0.0,
                "islem_gecmisi": [],
                "cuzdan_gecmisi": [],
                "max_drawdown": 0.0,
            }
            _write_ui_setting("challenge_session", yeni_ch)
            st.sidebar.success(f"✅ Challenge sıfırlandı (${ch_baslangic:.0f}'dan tekrar)! 📊 trade_logs.db verileri korunuyor.")
            time.sleep(1)
            st.rerun()

    # ====== V23: 🔥 Potansiyel Fırsatlar Paneli ======
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔥 Potansiyel Fırsatlar")
    taranan_firsatlar = S.get("taranan_coinler", [])
    guclü_firsatlar = []   # 80+
    orta_firsatlar = []    # 60-79
    for coin_data in taranan_firsatlar:
        if isinstance(coin_data, dict):
            skor = coin_data.get("guven_skoru", coin_data.get("skor", coin_data.get("score", 0)))
            try:
                skor_val = float(skor)
            except (ValueError, TypeError):
                skor_val = 0
            if skor_val >= 80:
                guclü_firsatlar.append(coin_data)
            elif skor_val >= 60:
                orta_firsatlar.append(coin_data)

    def _firsat_karti(firsat, yon_renk_default=None):
        f_sembol = firsat.get("sembol", firsat.get("symbol", "?"))
        f_skor = firsat.get("guven_skoru", firsat.get("skor", firsat.get("score", 0)))
        f_yon = firsat.get("sinyal", firsat.get("yon", firsat.get("signal", "—")))
        f_kaldirac = firsat.get("kaldirac", firsat.get("leverage", "—"))
        f_of = firsat.get("order_flow", "")
        
        if f_yon in ["LONG", "AL", "GÜÇLÜ AL", "ZAYIF AL"]:
            yon_renk = "#00ff88"
            yon_ikon = "🟢"
            yon_text = "LONG"
        elif f_yon in ["SHORT", "SAT", "GÜÇLÜ SAT", "ZAYIF SAT"]:
            yon_renk = "#ff4444"
            yon_ikon = "🔴"
            yon_text = "SHORT"
        else:
            yon_renk = yon_renk_default or "#888"
            yon_ikon = "⚪"
            yon_text = str(f_yon)
            
        of_html = f"<div style='margin-top:6px; font-size: 11px; color:#c5c6c7; border-top: 1px dashed rgba(255,255,255,0.1); padding-top:4px;'>📊 Emir Akışı: {f_of}</div>" if f_of else ""
        
        st.sidebar.markdown(f"""
        <div style='background: rgba(31,40,51,0.7); border-radius: 8px; padding: 10px; margin-bottom: 6px; border-left: 3px solid {yon_renk};'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <span style='color: #66fcf1; font-weight: 700; font-size: 14px;'>{f_sembol}</span>
                <span style='color: {yon_renk}; font-weight: 800; font-size: 13px;'>{yon_ikon} {yon_text}</span>
            </div>
            <div style='display: flex; justify-content: space-between; margin-top: 4px; font-size: 12px; color: #a4a5a6;'>
                <span>Güven: <b style='color: #ffd200;'>%{f_skor}</b></span>
                <span>Kaldıraç: <b>{f_kaldirac}x</b></span>
            </div>
            {of_html}
        </div>
        """, unsafe_allow_html=True)

    if guclü_firsatlar:
        st.sidebar.markdown("<div style='font-size:12px; color:#ffd200; font-weight:700; margin-bottom:4px;'>🔥 Yüksek Güven (%80+)</div>", unsafe_allow_html=True)
        for firsat in guclü_firsatlar[:5]:
            _firsat_karti(firsat)

    if orta_firsatlar:
        st.sidebar.markdown("<div style='font-size:12px; color:#66fcf1; font-weight:700; margin-bottom:4px; margin-top:8px;'>⚡ Orta Güven (%60–79)</div>", unsafe_allow_html=True)
        for firsat in orta_firsatlar[:5]:
            _firsat_karti(firsat)

    if not guclü_firsatlar and not orta_firsatlar:
        st.sidebar.info("Henüz %60+ güvenli fırsat bulunamadı.")

    # Esnek Demo Test Süresi
    if not S.get("use_real_api", False):
        st.sidebar.markdown("---")
        st.sidebar.markdown("### ⏳ Demo Test Süresi")
        hedef_saat = st.sidebar.number_input("Test Süresi (Saat)", min_value=1, max_value=720, value=int(S.get("hedef_sure_saat", 48)))
        if hedef_saat != S.get("hedef_sure_saat", 48.0):
            _write_ui_setting("hedef_sure_saat", float(hedef_saat))
            
        bas_zamani = S.get("baslangic_zamani", 0)
        gecen_saniye = (time.time() - bas_zamani) if bas_zamani > 0 else 0
        hedef_saniye = hedef_saat * 3600
        kalan_saniye = max(0.0, hedef_saniye - gecen_saniye)
        saat = int(kalan_saniye // 3600)
        dakika = int((kalan_saniye % 3600) // 60)
        ilerleme_pct = min(1.0, gecen_saniye / hedef_saniye) if hedef_saniye > 0 else 1.0

        st.sidebar.progress(ilerleme_pct)
        st.sidebar.markdown(f"**Kalan Süre:** {saat}s {dakika}d")

        if bas_zamani > 0 and kalan_saniye == 0:
            islem_gecmisi = S.get("islem_gecmisi", [])
            kapanan_islemler = [i for i in islem_gecmisi if "KAPAT" in i.get("sinyal", "")]
            pozitifler = [i for i in kapanan_islemler if isinstance(i.get("kar_zarar"), (int, float)) and float(str(i["kar_zarar"]).replace(" USDT", "").replace("+", "")) > 0]
            basari_orani = (len(pozitifler) / len(kapanan_islemler) * 100) if kapanan_islemler else 0

            st.sidebar.success(f"🎉 **{int(hedef_saat)} Saatlik Demo Tamamlandı!**\n\n"
                               f"📊 **Toplam İşlem:** {len(kapanan_islemler)}\n"
                               f"🎯 **Başarı Oranı:** %{basari_orani:.1f}\n"
                               f"💰 **Toplam Kâr:** ${state_bakiye - cfg.INITIAL_BALANCE:.2f}")

# ─────────────────────────────────────────────
# GLOBAL HEADER / METRICS (Ortak)
# ─────────────────────────────────────────────
usdt_d = S.get("usdt_d_deger", 0.0)
usdt_trend = S.get("usdt_d_trend", "YATAY")
trend_ikon = "⬆️" if usdt_trend == "YUKARI" else "⬇️" if usdt_trend == "ASAGI" else "➡️"
trend_renk = "#ff4444" if usdt_trend == "YUKARI" else "#00ff88" if usdt_trend == "ASAGI" else "#cccccc"
global_aktif_pnl = pnl_hesapla_coklu(S.get("aktif_pozisyonlar", {}), S.get("guncel_fiyatlar", {}))
pnl_renk = "#00ff88" if global_aktif_pnl >= 0 else "#ff4444"


# ─────────────────────────────────────────────
# LOG-ONLY MODE
# ─────────────────────────────────────────────
if st.session_state.view_mode == "📜 Sadece İşlem Logları":
    margin_total = aktif_margin_toplami(S.get("aktif_pozisyonlar", {}))
    anlik_toplam_bakiye = S.get("bakiye", 0) + margin_total + global_aktif_pnl

    st.markdown(f"""
    <div class='dashboard-header' style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;'>
        <h1 style='color: #66fcf1; margin: 0; font-size: 24px;'>📜 İşlem Logları</h1>
        <div style='display:flex; gap: 20px; font-size: 15px;'>
            <span style='color: #c5c6c7; font-weight: bold;'>📉 Toplam Varlık: ${anlik_toplam_bakiye:,.2f}</span>
            <span style='color: {pnl_renk}; font-weight: bold;'>💵 Aktif PNL: ${global_aktif_pnl:+.2f}</span>
            <span style='color:{trend_renk}; font-weight:800;'>📊 USDT.D: %{usdt_d:.2f} {trend_ikon}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

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

    if _is_running:
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
if _is_running:
    status_class = "status-breakout" if S.get("is_breakout") else "status-running"
elif "Hedef" in S.get("bot_durumu", ""):
    status_class = "status-target"

col_durum.markdown(f"<div style='text-align:right; margin-top:20px;'><span class='status-badge {status_class}'>Durum: {S.get('bot_durumu', 'Durduruldu')}</span></div>", unsafe_allow_html=True)

st.markdown(f"""
<div class='dashboard-header' style='display: flex; justify-content: space-between; align-items: center;'>
    <span><b>🎯 Odaklanılan Ticker: {S.get('aktif_sembol', 'Bekleniyor...')}</b> — Risk Barometresi: {S.get('global_risk_seviyesi', 'Normal')}</span>
    <span style='color:{trend_renk}; font-weight:800; font-size:16px;'>USDT.D: %{usdt_d:.2f} {trend_ikon}</span>
</div>
""", unsafe_allow_html=True)

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
if _is_running:
    st.info(f"⏳ Kalan Hedef Süresi: {kalan_sure:.1f} Saat")

st.markdown("---")
st.markdown("### 📈 Günlük Performans Takibi")
gunluk_pnl = gunluk_kar_hesapla_ui(S)
hedef_pct = 10.0

gauge_pct = max(0.0, min(gunluk_pnl / hedef_pct, 1.0)) if hedef_pct > 0 else 0.0
if gunluk_pnl >= hedef_pct:
    gauge_renk, gauge_emoji, gauge_durum = "#00ff88", "🏆", "Hedef Tamamlandı (Extra Kârda)"
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
tab_dash, tab_tv, tab_gecmis, tab_canli = st.tabs(["📊 Dashboard", "📈 Grafikler (TradingView)", "📚 Geçmiş Performans", "⚡ Canlı İşlem Akışı"])

with tab_dash:
    st.markdown("### 💼 Cüzdan Özeti")
    state_bakiye = S.get("bakiye", 0)
    aktif_pozlar = S.get("aktif_pozisyonlar", {})
    margin_total = aktif_margin_toplami(aktif_pozlar)
    fiyat_haritasi = S.get("guncel_fiyatlar", {})
    
    aktif_toplam_pnl = 0.0
    for tid, p in aktif_pozlar.items():
        try:
            s = p.get("sembol", tid)
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
        for idx, (tid, p) in enumerate(aktif_pozlar.items()):
            try:
                s = p.get("sembol", tid)
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

            # V35: TP1/TP2 hedef fiyatları ve tp1_yapildi badge
            tp1_f = p.get('tp1_fiyat', 0)
            tp2_f = p.get('tp2_fiyat', 0)
            tp1_yapildi = p.get('tp1_yapildi', False)
            tp1_badge_html = ""
            if tp1_yapildi:
                tp1_badge_html = "<span style='background: linear-gradient(135deg, #00b894, #00cec9); color: #fff; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; margin-left: 8px;'>🎯 TP1 ALINDI - Risksiz Mod</span>"
            tp_satir_html = ""
            if tp1_f > 0 or tp2_f > 0:
                tp1_renk = '#00ff88' if tp1_yapildi else '#ffd200'
                tp1_str = f"<span style='color:{tp1_renk}; font-weight:700;'>${tp1_f:.4f}</span>" if tp1_f > 0 else "—"
                tp2_str = f"<span style='color:#ff6b6b; font-weight:700;'>${tp2_f:.4f}</span>" if tp2_f > 0 else "—"
                tp1_check = ' ✅' if tp1_yapildi else ''
                tp_satir_html = f"""
                <div style='display: flex; gap: 24px; color: #c5c6c7; font-size: 13px; margin-top: 4px; padding-top: 4px; border-top: 1px dashed rgba(255,255,255,0.08);'>
                    <span>🎯 TP1: {tp1_str}{tp1_check}</span>
                    <span>💰 TP2: {tp2_str}</span>
                </div>"""

            st.markdown(f"""
            <div style='background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; padding: 16px; margin-bottom: 12px; border-left: 4px solid {pnl_renk};'>
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;'>
                    <span style='font-size: 18px; font-weight: 700; color: #66fcf1;'>{s} ({p.get('pozisyon', '?')} {p.get('islem_kaldirac', 0)}x) <span style='font-size: 11px; color: #888;'>#{tid}</span>{tp1_badge_html}</span>
                    <span style='font-size: 20px; font-weight: 800; color: {pnl_renk};'>{anlik_pnl:+.2f} USDT ({pnl_pct:+.1f}%)</span>
                </div>
                <div style='display: flex; gap: 24px; color: #c5c6c7; font-size: 13px; margin-bottom: 6px;'>
                    <span>💰 Giriş: <b>{f"${p.get('giris_fiyati', 0):.4f}" if p.get("giris_fiyati", 0) > 0 else "Veri Bekleniyor..."}</b></span>
                    <span>📊 Anlık: <b>{f"${guncel_fiyat:.4f}" if guncel_fiyat > 0 else "Veri Bekleniyor..."}</b></span>
                    <span>🛡️ Margin: <b>${p.get('islem_margin', 0):.2f}</b></span>
                    <span>💣 Liq Riski: <b>%{liq_risk_pct:.1f}</b></span>
                </div>{tp_satir_html}
                <div style='color: #45a29e; font-size: 12px; margin-top: 4px;'>
                    <b>📝 Giriş Nedeni:</b> {giris_nedeni}
                </div>
                <div style='color: #888; font-size: 11px; margin-top: 2px;'>
                    <b>🎯 Beklenen Hedef:</b> %{beklenen:+.1f} büyüme
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Manuel Kapatma Butonu (IPC ile)
            if st.button(f"❌ İşlemi Kapat", key=f"close_{tid}", use_container_width=False):
                close_fiyat = fiyat_haritasi.get(s, guncel_fiyat)
                if close_fiyat > 0:
                    _request_close_trade(tid, close_fiyat)
                    st.success(f"📤 {s} kapatma komutu gönderildi. Engine işleyecek...")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning(f"⚠️ {s} için güncel fiyat alınamadı. Lütfen tekrar deneyin.")

            poz_liste.append({
                "Trade ID": tid,
                "Sembol": s,
                "Giriş Fiyatı": f"${p.get('giris_fiyati', 0):.4f}" if p.get('giris_fiyati', 0) > 0 else "Veri Bekleniyor...",
                "Kaldıraç": f"{p.get('islem_kaldirac', 0)}x",
                "Kullanılan Margin": f"${p.get('islem_margin', 0):.2f}",
                "Anlık K/Z ($)": f"{anlik_pnl:+.2f}",
                "ROE (%)": f"{pnl_pct:+.2f}%",
                "TP1": (f"${tp1_f:.4f} ✅" if tp1_yapildi else f"${tp1_f:.4f}") if tp1_f > 0 else "—",
                "TP2": f"${tp2_f:.4f}" if tp2_f > 0 else "—",
                "Liq Riski": f"%{liq_risk_pct:.1f}",
                "Giriş Gerekçesi": giris_nedeni[:60]
            })

        st.markdown("#### 📋 Detaylı Tablo")
        st.dataframe(pd.DataFrame(poz_liste), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Finansal Metrikler
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        fiyat_placeholder = st.empty()
        anlik_s = S.get('fiyat', 0)
        
        if "last_fiyat_val" not in st.session_state:
            st.session_state.last_fiyat_val = -1
            
        if anlik_s != st.session_state.last_fiyat_val:
            fiyat_placeholder.metric("Anlık Fiyat", f"${anlik_s:,.4f}" if anlik_s else "Veri Bekleniyor...", f"%{S.get('degisim_24s', 0):+.2f}" if anlik_s else None)
            st.session_state.last_fiyat_val = anlik_s
        else:
            fiyat_placeholder.metric("Anlık Fiyat", f"${anlik_s:,.4f}" if anlik_s else "Veri Bekleniyor...", f"%{S.get('degisim_24s', 0):+.2f}" if anlik_s else None)
            
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
        tv_base = aktif_s.replace('/', '')
        tv_symbol = f"BINANCE:{tv_base}.P"
        tv_html = f"""
        <!-- TradingView Widget BEGIN -->
        <div class="tradingview-widget-container" style="height:600px;width:100%">
          <div class="tradingview-widget-container__widget" style="height:calc(100% - 32px);width:100%"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
          {{
          "autosize": true,
          "symbol": "{tv_symbol}",
          "interval": "15",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "tr",
          "allow_symbol_change": true,
          "support_host": "https://www.tradingview.com",
          "enable_publishing": false,
          "backgroundColor": "rgba(11, 12, 16, 1)",
          "gridColor": "rgba(42, 46, 57, 0.06)",
          "hide_top_toolbar": false,
          "hide_legend": false,
          "save_image": false,
          "studies": ["RSI@tv-basicstudies", "BB@tv-basicstudies", "Volume@tv-basicstudies"],
          "container_id": "tradingview_futures"
        }}
          </script>
        </div>
        <!-- TradingView Widget END -->
        """
        components.html(tv_html, height=620)
    else:
        st.info("Kripto para bekleniyor...")

    st.markdown("<div class='dashboard-header'><b>🔥 Breakout Radarı (Anlık Tarama)</b></div>", unsafe_allow_html=True)
    taranan = S.get("taranan_coinler", [])
    if taranan:
        df_scan = pd.DataFrame(taranan)
        st.dataframe(df_scan, use_container_width=True, hide_index=True)
    else:
        st.info("Piyasa taraması bekleniyor...")

with tab_gecmis:
    st.markdown("### 📚 Geçmiş Performans (SQLite Veritabanı)")
    # Lazy import: data_logger sadece bu tab açıldığında yüklenir
    import data_logger as _dl
    stats = _dl.basari_orani_getir(son_n=100)
    
    st.markdown("#### Kümülatif Kâr/Zarar Başarısı (Son 100 İşlem)")
    gc1, gc2, gc3, gc4 = st.columns(4)
    gc1.metric("Toplam İşlem", stats["toplam"])
    gc2.metric("Kârlı Kapanış", stats["karli"])
    gc3.metric("Zararlı Kapanış", stats["zarari"])
    gc4.metric("Başarı Oranı", f"%{stats['oran']}")
    
    st.markdown("---")
    islemler = _dl.son_islemler_getir(limit=50)
    if islemler:
        df_log = pd.DataFrame(islemler)
        
        st.markdown("#### Yakın Geçmiş PNL Trendi ($)")
        st.bar_chart(df_log.set_index("zaman")["pnl"])
        
        st.markdown("#### Son Kapanan İşlemler Defteri")
        df_ui = df_log.rename(columns={
            "zaman": "Tarih", "sembol": "Coin", "tip": "Yön", 
            "giris": "Giriş", "cikis": "Çıkış", "pnl": "PNL ($)", 
            "pnl_pct": "ROE (%)", "kaldirac": "Kaldıraç", 
            "margin": "Margin", "neden": "Kapatma Nedeni"
        })
        st.dataframe(df_ui, use_container_width=True, hide_index=True)
    else:
        st.info("Henüz veritabanında işlem kaydı yok. Bot işlem yapmaya başladığında veriler buraya yansıyacaktır.")


# ─────────────────────────────────────────────
# V29: ⚡ Canlı İşlem Akışı Tab'ı
# ─────────────────────────────────────────────
with tab_canli:
    st.markdown("### ⚡ Canlı İşlem Akışı (Real-Time Trades)")
    st.markdown("""<div style='background: linear-gradient(135deg, #0b0c10 0%, #1f2833 100%); 
        border-radius: 12px; padding: 12px; margin-bottom: 16px; border-left: 4px solid #66fcf1;'>
        <span style='color: #66fcf1; font-size: 14px;'>📡 ccxt.pro WebSocket ile aktif pozisyonlarınızın anlık alım/satım akışını izleyin.</span>
    </div>""", unsafe_allow_html=True)

    canli_data = S.get("canli_islemler", {})
    aktif_poz_semboller = list(set(
        p.get("sembol", tid)
        for tid, p in S.get("aktif_pozisyonlar", {}).items()
        if isinstance(p, dict)
    ))

    if not aktif_poz_semboller:
        st.info("💤 Aktif pozisyon yok. Pozisyon açıldığında canlı işlem akışı burada görünecek.")
    else:
        for sembol_canli in aktif_poz_semboller:
            st.markdown(f"#### 💹 {sembol_canli}")
            trades_list = canli_data.get(sembol_canli, [])
            if trades_list:
                df_trades = pd.DataFrame(trades_list)
                def renk_isle(row):
                    renk = 'color: #00ff88' if 'Buy' in str(row.get('yon', '')) else 'color: #ff4444'
                    return [renk] * len(row)

                st.dataframe(
                    df_trades.rename(columns={
                        "zaman": "Zaman", "fiyat": "Fiyat ($)",
                        "miktar": "Miktar", "yon": "Yön",
                        "buyukluk_usdt": "Büyüklük (USDT)"
                    }),
                    use_container_width=True, hide_index=True, height=350
                )

                toplam_buy = sum(1 for t in trades_list if 'Buy' in str(t.get('yon', '')))
                toplam_sell = len(trades_list) - toplam_buy
                buy_pct = (toplam_buy / len(trades_list) * 100) if trades_list else 0
                buy_vol = sum(t.get('buyukluk_usdt', 0) for t in trades_list if 'Buy' in str(t.get('yon', '')))
                sell_vol = sum(t.get('buyukluk_usdt', 0) for t in trades_list if 'Sell' in str(t.get('yon', '')))

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Alıcı", f"{toplam_buy} (​%{buy_pct:.0f})")
                c2.metric("Satıcı", f"{toplam_sell} (%{100-buy_pct:.0f})")
                c3.metric("Alıcı Hacim", f"${buy_vol:,.0f}")
                c4.metric("Satıcı Hacim", f"${sell_vol:,.0f}")
            else:
                st.warning(f"📡 {sembol_canli} için trade verisi bekleniyor...")

    st.markdown("---")
    st.caption("🔄 Veriler ccxt.pro WebSocket üzerinden canlı olarak güncellenir. Sadece aktif pozisyonlu semboller izlenir.")

# ─────────────────────────────────────────────
# Auto-Refresh (Engine çalışırken)
# ─────────────────────────────────────────────
if _is_running:
    time.sleep(2.0)
    st.rerun()
