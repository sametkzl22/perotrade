"""
Kripto Paper-Trading Bot — Streamlit Dashboard
===============================================
Arka planda thread ile bot çalıştırır, Streamlit UI ile
canlı fiyat, cüzdan durumu ve işlem geçmişini gösterir.

Kullanım:
    streamlit run streamlit_app.py
"""

import threading
import time
import csv
from datetime import datetime, timezone

import ccxt
import pandas as pd
import numpy as np
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx


# ─────────────────────────────────────────────
# Sayfa Konfigürasyonu
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="PeroTrade — Kripto Bot Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─────────────────────────────────────────────
# Özel CSS — Modern, koyu tema kartları
# ─────────────────────────────────────────────
st.markdown("""
<style>
/* Ana arka plan */
.stApp {
    background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
}

/* Metrik kartları */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, rgba(30,30,60,0.8), rgba(20,20,50,0.9));
    border: 1px solid rgba(0,210,255,0.15);
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 30px rgba(0,210,255,0.15), inset 0 1px 0 rgba(255,255,255,0.08);
}
[data-testid="stMetricValue"] {
    font-size: 1.4rem !important;
    font-weight: 700;
    color: #e0e0ff;
}
[data-testid="stMetricLabel"] {
    color: #8888aa;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 1px;
}
[data-testid="stMetricDelta"] > div {
    font-size: 0.9rem;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d0d1a 0%, #1a1a30 100%);
    border-right: 1px solid rgba(0,210,255,0.1);
}

/* Butonlar */
.stButton > button {
    border-radius: 8px;
    font-weight: 600;
    padding: 0.5rem 1.5rem;
    transition: all 0.3s ease;
    border: 1px solid rgba(0,210,255,0.3);
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 15px rgba(0,210,255,0.2);
}

/* Veri tablosu */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
}

/* Progress bar */
.stProgress > div > div {
    background: linear-gradient(90deg, #00d2ff, #3a7bd5, #00d2ff);
    background-size: 200% 100%;
    animation: shimmer 2s ease infinite;
    border-radius: 6px;
}
@keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
}

/* Başlık ayracı */
.dashboard-header {
    background: linear-gradient(90deg, rgba(0,210,255,0.1), transparent);
    border-left: 3px solid #00d2ff;
    padding: 8px 16px;
    margin: 16px 0 8px 0;
    border-radius: 0 8px 8px 0;
}

/* Durum badge */
.status-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.95rem;
    letter-spacing: 0.5px;
}
.status-running {
    background: linear-gradient(135deg, #00b09b, #96c93d);
    color: #fff;
    box-shadow: 0 2px 12px rgba(0,176,155,0.4);
    animation: pulse 2s ease infinite;
}
.status-stopped {
    background: linear-gradient(135deg, #434343, #636363);
    color: #ccc;
}
.status-target {
    background: linear-gradient(135deg, #f7971e, #ffd200);
    color: #1a1a2e;
    box-shadow: 0 2px 12px rgba(247,151,30,0.4);
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.8; }
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Mod Presetleri
# ─────────────────────────────────────────────
MOD_PRESETLERI = {
    "⚡ Agresif Mod": {
        "risk": 1.0,
        "sma_kisa": 7,
        "sma_uzun": 25,
        "aciklama": "Haftada 10x hedef · %100 risk · SMA(7/25)",
    },
    "🌱 Soft Kar Modu": {
        "risk": 0.30,
        "sma_kisa": 14,
        "sma_uzun": 50,
        "aciklama": "Günlük %1-2 kâr · %30 risk · SMA(14/50)",
    },
}


# ─────────────────────────────────────────────
# Session State Başlatma
# ─────────────────────────────────────────────
def session_state_baslat():
    """İlk çalışmada session_state değerlerini oluşturur."""
    varsayilanlar = {
        "bot_calisiyor": False,
        "bot_durumu": "Duraklatıldı",
        "bakiye": 10.0,
        "baslangic_bakiye": 10.0,
        "hedef_bakiye": 100.0,
        "coin_miktar": 0.0,
        "pozisyon": "YOK",
        "islem_gecmisi": [],
        "son_fiyat": 0.0,
        "degisim_24s": 0.0,
        "hacim_24s": 0.0,
        "sembol": "BTC/USDT",
        "exchange_adi": "binance",
        "mod": "⚡ Agresif Mod",
        "lock": threading.Lock(),
        "bot_thread": None,
        "dur_sinyali": threading.Event(),
    }
    for anahtar, deger in varsayilanlar.items():
        if anahtar not in st.session_state:
            st.session_state[anahtar] = deger


session_state_baslat()


# ─────────────────────────────────────────────
# Yardımcı Fonksiyonlar
# ─────────────────────────────────────────────
def exchange_olustur(exchange_name: str) -> ccxt.Exchange:
    """ccxt exchange nesnesi oluşturur (public erişim)."""
    sinif = getattr(ccxt, exchange_name, None)
    if sinif is None:
        raise ValueError(f"Desteklenmeyen exchange: {exchange_name}")
    return sinif({"enableRateLimit": True})


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
    """
    SMA crossover sinyali üretir.
    Son iki mumu karşılaştırarak kesişimi tespit eder.
    """
    df = df.copy()
    df["sma_k"] = sma_hesapla(df["close"], sma_kisa)
    df["sma_u"] = sma_hesapla(df["close"], sma_uzun)

    if df["sma_k"].isna().iloc[-1] or df["sma_u"].isna().iloc[-1]:
        return "BEKLE"

    onceki = df.iloc[-2]
    son = df.iloc[-1]

    # Golden cross — kısa MA uzun MA'yı yukarı kesiyor
    if onceki["sma_k"] <= onceki["sma_u"] and son["sma_k"] > son["sma_u"]:
        return "AL"

    # Death cross — kısa MA uzun MA'yı aşağı kesiyor
    if onceki["sma_k"] >= onceki["sma_u"] and son["sma_k"] < son["sma_u"]:
        return "SAT"

    return "BEKLE"


def ticker_bilgisi_cek(exchange, symbol: str) -> dict:
    """24 saatlik ticker verisini çeker (fiyat, değişim, hacim)."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return {
            "fiyat": ticker.get("last", 0.0),
            "degisim": ticker.get("percentage", 0.0),
            "hacim": ticker.get("quoteVolume", 0.0),
        }
    except Exception:
        return {"fiyat": 0.0, "degisim": 0.0, "hacim": 0.0}


def islem_gecmisi_kaydet(gecmis: list, dosya: str = "trade_history.csv"):
    """İşlem geçmişini CSV dosyasına yazar."""
    if not gecmis:
        return
    basliklar = ["zaman", "sinyal", "fiyat", "miktar", "bakiye_usdt", "kar_zarar"]
    with open(dosya, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=basliklar)
        writer.writeheader()
        writer.writerows(gecmis)


# ─────────────────────────────────────────────
# Bot Engine — Arka Plan Thread
# ─────────────────────────────────────────────
def bot_engine(state, lock: threading.Lock, dur_sinyali: threading.Event):
    """
    Arka planda çalışan bot döngüsü.
    Her 5 dakikada bir veri çekip analiz yapar.
    Session state üzerinden UI ile iletişim kurar.
    """
    try:
        exchange = exchange_olustur(state["exchange_adi"])
        preset = MOD_PRESETLERI[state["mod"]]

        while not dur_sinyali.is_set():
            try:
                # Ticker bilgisi (fiyat, 24h değişim, hacim)
                ticker = ticker_bilgisi_cek(exchange, state["sembol"])

                # Mum verisi çek
                limit = preset["sma_uzun"] + 5
                df = mum_verisi_cek(exchange, state["sembol"], "1h", limit)
                son_fiyat = float(df["close"].iloc[-1])

                # SMA crossover sinyali
                sinyal = sinyal_uret(df, preset["sma_kisa"], preset["sma_uzun"])

                zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                with lock:
                    # Ticker verilerini güncelle
                    state["son_fiyat"] = ticker["fiyat"] or son_fiyat
                    state["degisim_24s"] = ticker["degisim"] or 0.0
                    state["hacim_24s"] = ticker["hacim"] or 0.0

                    # İşlem mantığı
                    if sinyal == "AL" and state["pozisyon"] == "YOK":
                        miktar = (state["bakiye"] * preset["risk"]) / son_fiyat
                        state["coin_miktar"] = miktar
                        state["bakiye"] = state["bakiye"] * (1 - preset["risk"])
                        state["pozisyon"] = "ACIK"

                        state["islem_gecmisi"].append({
                            "zaman": zaman,
                            "sinyal": "🟢 AL",
                            "fiyat": round(son_fiyat, 2),
                            "miktar": round(miktar, 8),
                            "bakiye_usdt": round(state["bakiye"], 2),
                            "kar_zarar": "—",
                        })

                    elif sinyal == "SAT" and state["pozisyon"] == "ACIK":
                        gelir = state["coin_miktar"] * son_fiyat
                        # Kâr/zarar hesapla
                        onceki_toplam = state["baslangic_bakiye"]
                        if state["islem_gecmisi"]:
                            # Son alım fiyatından hesapla
                            son_alim = [
                                i for i in state["islem_gecmisi"] if "AL" in i["sinyal"]
                            ]
                            if son_alim:
                                alis_fiyat = son_alim[-1]["fiyat"]
                                kz = ((son_fiyat - alis_fiyat) / alis_fiyat) * 100
                            else:
                                kz = 0.0
                        else:
                            kz = 0.0

                        state["bakiye"] += gelir
                        state["coin_miktar"] = 0.0
                        state["pozisyon"] = "YOK"

                        state["islem_gecmisi"].append({
                            "zaman": zaman,
                            "sinyal": "🔴 SAT",
                            "fiyat": round(son_fiyat, 2),
                            "miktar": round(gelir / son_fiyat, 8),
                            "bakiye_usdt": round(state["bakiye"], 2),
                            "kar_zarar": f"%{kz:+.2f}",
                        })

                    # Toplam portföy değeri
                    toplam = state["bakiye"] + (state["coin_miktar"] * son_fiyat)

                    # Hedef kontrolü
                    if toplam >= state["hedef_bakiye"]:
                        state["bot_durumu"] = "🎯 Hedefe Ulaştı!"
                        state["bot_calisiyor"] = False
                        islem_gecmisi_kaydet(state["islem_gecmisi"])
                        dur_sinyali.set()
                        return

            except Exception as e:
                # API hataları vb. — döngüyü kırmadan devam et
                print(f"Bot engine hata: {e}")

            # 5 dakika bekle (her saniye dur sinyalini kontrol et)
            for _ in range(300):
                if dur_sinyali.is_set():
                    return
                time.sleep(1)

    except Exception as e:
        with lock:
            state["bot_durumu"] = f"Hata: {e}"
            state["bot_calisiyor"] = False


# ─────────────────────────────────────────────
# Bot Kontrol Fonksiyonları
# ─────────────────────────────────────────────
def botu_baslat():
    """Arka plan thread'ini başlatır."""
    if st.session_state.bot_calisiyor:
        return

    # Dur sinyalini sıfırla
    st.session_state.dur_sinyali.clear()
    st.session_state.bot_calisiyor = True
    st.session_state.bot_durumu = "Çalışıyor"

    # Paylaşılan state dict (thread-safe erişim için)
    shared = st.session_state

    t = threading.Thread(
        target=bot_engine,
        args=(shared, shared.lock, shared.dur_sinyali),
        daemon=True,
    )
    add_script_run_ctx(t)
    t.start()
    st.session_state.bot_thread = t


def botu_durdur():
    """Arka plan thread'ini güvenli şekilde durdurur."""
    st.session_state.dur_sinyali.set()
    st.session_state.bot_calisiyor = False
    if st.session_state.bot_durumu != "🎯 Hedefe Ulaştı!":
        st.session_state.bot_durumu = "Duraklatıldı"
    islem_gecmisi_kaydet(st.session_state.islem_gecmisi)


# ─────────────────────────────────────────────
# SIDEBAR — Kontrol Paneli
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎛️ Kontrol Paneli")
    st.markdown("---")

    # Exchange seçimi
    exchange_sec = st.selectbox(
        "🏦 Exchange",
        ["binance", "gateio"],
        index=0,
        disabled=st.session_state.bot_calisiyor,
    )
    st.session_state.exchange_adi = exchange_sec

    # İşlem çifti
    cift_sec = st.selectbox(
        "💱 İşlem Çifti",
        ["BTC/USDT", "ETH/USDT"],
        index=0,
        disabled=st.session_state.bot_calisiyor,
    )
    st.session_state.sembol = cift_sec

    st.markdown("---")

    # Bakiye ayarları
    st.markdown("#### 💰 Bakiye Ayarları")
    baslangic = st.number_input(
        "Sanal Başlangıç Bakiye (USDT)",
        min_value=1.0,
        max_value=10000.0,
        value=st.session_state.baslangic_bakiye,
        step=1.0,
        disabled=st.session_state.bot_calisiyor,
    )
    hedef = st.number_input(
        "Hedef Bakiye (USDT)",
        min_value=2.0,
        max_value=100000.0,
        value=st.session_state.hedef_bakiye,
        step=10.0,
        disabled=st.session_state.bot_calisiyor,
    )

    # Bakiyeleri güncelle (bot durmaktayken)
    if not st.session_state.bot_calisiyor:
        st.session_state.baslangic_bakiye = baslangic
        st.session_state.bakiye = baslangic
        st.session_state.hedef_bakiye = hedef

    st.markdown("---")

    # Mod seçimi
    st.markdown("#### 🎯 Strateji Modu")
    mod_sec = st.radio(
        "Mod",
        list(MOD_PRESETLERI.keys()),
        index=0,
        disabled=st.session_state.bot_calisiyor,
        label_visibility="collapsed",
    )
    st.session_state.mod = mod_sec

    preset = MOD_PRESETLERI[mod_sec]
    st.caption(preset["aciklama"])
    st.markdown(
        f"<small>Risk: <b>%{preset['risk']*100:.0f}</b> · "
        f"SMA: <b>{preset['sma_kisa']}/{preset['sma_uzun']}</b></small>",
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # Aksiyon butonları
    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button(
            "▶️ Başlat",
            use_container_width=True,
            disabled=st.session_state.bot_calisiyor,
            type="primary",
        ):
            botu_baslat()
            st.rerun()
    with col_stop:
        if st.button(
            "⏹️ Durdur",
            use_container_width=True,
            disabled=not st.session_state.bot_calisiyor,
        ):
            botu_durdur()
            st.rerun()

    # Reset butonu
    st.markdown("")
    if st.button(
        "🔄 Sıfırla",
        use_container_width=True,
        disabled=st.session_state.bot_calisiyor,
    ):
        botu_durdur()
        st.session_state.bakiye = st.session_state.baslangic_bakiye
        st.session_state.coin_miktar = 0.0
        st.session_state.pozisyon = "YOK"
        st.session_state.islem_gecmisi = []
        st.session_state.son_fiyat = 0.0
        st.session_state.degisim_24s = 0.0
        st.session_state.hacim_24s = 0.0
        st.session_state.bot_durumu = "Duraklatıldı"
        st.rerun()


# ─────────────────────────────────────────────
# ANA SAYFA — Dashboard
# ─────────────────────────────────────────────

# Başlık
st.markdown(
    "<h1 style='text-align:center; "
    "background: linear-gradient(90deg, #00d2ff, #3a7bd5); "
    "-webkit-background-clip: text; -webkit-text-fill-color: transparent; "
    "font-size: 2.2rem; margin-bottom: 0;'>"
    "🤖 PeroTrade — Kripto Bot Dashboard</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center; color:#6a6a8a; margin-top:0;'>"
    f"{st.session_state.exchange_adi.upper()} · {st.session_state.sembol} · "
    f"Paper Trading</p>",
    unsafe_allow_html=True,
)

# ── Bot Durumu ──
durum_txt = st.session_state.bot_durumu
if "Çalışıyor" in durum_txt:
    badge_cls = "status-running"
elif "Hedefe" in durum_txt:
    badge_cls = "status-target"
else:
    badge_cls = "status-stopped"

st.markdown(
    f"<div style='text-align:center; margin: 12px 0 20px 0;'>"
    f"<span class='status-badge {badge_cls}'>{durum_txt}</span></div>",
    unsafe_allow_html=True,
)

# ── Canlı Fiyat Kartları ──
st.markdown("<div class='dashboard-header'><b>📈 Piyasa Verileri</b></div>", unsafe_allow_html=True)

with st.session_state.lock:
    fiyat = st.session_state.son_fiyat
    degisim = st.session_state.degisim_24s
    hacim = st.session_state.hacim_24s

k1, k2, k3 = st.columns(3)
with k1:
    st.metric(
        label=f"{st.session_state.sembol} Fiyat",
        value=f"${fiyat:,.2f}" if fiyat else "—",
        delta=f"%{degisim:+.2f}" if degisim else None,
    )
with k2:
    st.metric(
        label="24s Değişim",
        value=f"%{degisim:+.2f}" if degisim else "—",
    )
with k3:
    hacim_gosterim = f"${hacim/1e6:,.1f}M" if hacim > 1e6 else f"${hacim:,.0f}"
    st.metric(
        label="24s Hacim",
        value=hacim_gosterim if hacim else "—",
    )

# ── Sanal Cüzdan Özeti ──
st.markdown("<div class='dashboard-header'><b>💼 Sanal Cüzdan</b></div>", unsafe_allow_html=True)

with st.session_state.lock:
    bakiye = st.session_state.bakiye
    coin = st.session_state.coin_miktar
    baslangic_b = st.session_state.baslangic_bakiye
    hedef_b = st.session_state.hedef_bakiye

coin_deger = coin * fiyat if fiyat else 0.0
toplam_deger = bakiye + coin_deger

# Kâr/zarar yüzdesi
if baslangic_b > 0:
    kar_zarar_pct = ((toplam_deger - baslangic_b) / baslangic_b) * 100
else:
    kar_zarar_pct = 0.0

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("USDT Bakiye", f"${bakiye:.2f}")
with c2:
    st.metric("Coin Değeri", f"${coin_deger:.2f}")
with c3:
    st.metric(
        "Toplam Portföy",
        f"${toplam_deger:.2f}",
        delta=f"%{kar_zarar_pct:+.1f}",
    )
with c4:
    kalan = max(0, hedef_b - toplam_deger)
    st.metric("Hedefe Kalan", f"${kalan:.2f}")

# Hedef ilerleme çubuğu
ilerleme = min(toplam_deger / hedef_b, 1.0) if hedef_b > 0 else 0.0
st.progress(ilerleme, text=f"Hedef: ${hedef_b:.0f} — İlerleme: %{ilerleme*100:.1f}")

# ── İşlem Geçmişi ──
st.markdown("<div class='dashboard-header'><b>📋 İşlem Geçmişi</b></div>", unsafe_allow_html=True)

with st.session_state.lock:
    gecmis = list(st.session_state.islem_gecmisi)

if gecmis:
    df_log = pd.DataFrame(gecmis)
    df_log.columns = ["Zaman", "Sinyal", "Fiyat ($)", "Miktar", "Bakiye (USDT)", "Kâr/Zarar"]
    # En son işlem en üstte
    df_log = df_log.iloc[::-1].reset_index(drop=True)
    st.dataframe(
        df_log,
        use_container_width=True,
        hide_index=True,
        height=min(400, 50 + len(df_log) * 35),
    )
else:
    st.info("📭 Henüz işlem yapılmadı. Botu başlatın ve crossover sinyalini bekleyin.")

# ── Strateji Bilgisi ──
with st.expander("ℹ️ Strateji Detayları", expanded=False):
    aktif_preset = MOD_PRESETLERI[st.session_state.mod]
    st.markdown(f"""
    **Aktif Mod:** {st.session_state.mod}

    | Parametre | Değer |
    |-----------|-------|
    | Risk Oranı | %{aktif_preset['risk']*100:.0f} |
    | Kısa SMA | {aktif_preset['sma_kisa']} periyot |
    | Uzun SMA | {aktif_preset['sma_uzun']} periyot |
    | Mum Periyodu | 1 saat |
    | Analiz Aralığı | 5 dakika |

    **Nasıl Çalışır:**
    - SMA({aktif_preset['sma_kisa']}) yukarıdan SMA({aktif_preset['sma_uzun']})'i kestiğinde → **Alım**
    - SMA({aktif_preset['sma_kisa']}) aşağıdan SMA({aktif_preset['sma_uzun']})'i kestiğinde → **Satım**
    - Kesişim yoksa → **Beklemede**
    """)

# ── Alt bilgi ──
st.markdown("---")
st.markdown(
    "<p style='text-align:center; color:#4a4a6a; font-size:0.8rem;'>"
    "⚠️ Paper Trading — Gerçek işlem yapılmaz. Yalnızca simülasyon amaçlıdır."
    "</p>",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# Auto-refresh — Bot çalışıyorken 5 saniyede bir
# ─────────────────────────────────────────────
if st.session_state.bot_calisiyor:
    time.sleep(5)
    st.rerun()
