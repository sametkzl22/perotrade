"""
Bot Worker — Global Singleton + Background Thread Manager (v11)
================================================================
Streamlit UI'dan bağımsız çalışan arka plan işçisi.
@st.cache_resource ile oluşturulur, sekme kapansa bile process
yaşadığı sürece bellekte kalır ve bot 7/24 çalışmaya devam eder.
v11: Binance Futures entegrasyonu, Cooling, GC, Auto-Reconnect.
"""

import gc
import uuid
import threading
import time
import csv
import asyncio
import os
import sqlite3
from datetime import datetime, timezone

import ccxt
import ccxt.pro as ccxtpro

import ai_engine
import config as cfg
import persistent_state as ps
import data_logger
import train_model
from data_provider import DataProvider


# ─────────────────────────────────────────────
# V23: Telegram Notification Engine (requests)
# ─────────────────────────────────────────────
import requests as _requests

_TG_LAST_UPDATE_ID = 0  # Global polling offset


def send_telegram_msg(text: str) -> bool:
    """requests tabanlı Telegram mesaj gönderici. Hata botun çalışmasını etkilemez."""
    token = getattr(cfg, "TELEGRAM_TOKEN", "")
    chat_id = getattr(cfg, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        resp = _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=6,
        )
        return resp.status_code == 200
    except Exception:
        return False


# Geriye uyumluluk: eski isim de çalışsın
telegram_bildirim_gonder = send_telegram_msg


def _tg_trade_acilis_mesaji(sembol: str, yon: str, kaldirac: int, giris: float,
                             margin: float, trade_id: str) -> str:
    """Pozisyon açılışı için detaylı Telegram mesajı oluşturur."""
    pozisyon_usdt = margin * kaldirac
    return (
        f"<b>🟢 POZİSYON AÇILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Coin:</b> {sembol}\n"
        f"🎯 <b>Yön:</b> {yon}\n"
        f"⚡ <b>Kaldıraç:</b> {kaldirac}x\n"
        f"💰 <b>Giriş Fiyatı:</b> ${giris:.4f}\n"
        f"🛡️ <b>Margin:</b> ${margin:.2f}\n"
        f"📊 <b>Pozisyon:</b> ${pozisyon_usdt:.2f}\n"
        f"🔑 <b>Trade ID:</b> #{trade_id}"
    )


def _tg_trade_kapanis_mesaji(sembol: str, yon: str, giris: float, cikis: float,
                              pnl: float, margin: float, neden: str, trade_id: str) -> str:
    """Pozisyon kapanışı için detaylı Telegram mesajı oluşturur."""
    roe_pct = (pnl / margin * 100) if margin > 0 else 0
    emoji = "✅" if pnl >= 0 else "❌"
    return (
        f"<b>{emoji} POZİSYON KAPATILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Coin:</b> {sembol}\n"
        f"🎯 <b>Yön:</b> {yon}\n"
        f"💰 <b>Giriş:</b> ${giris:.4f} → <b>Çıkış:</b> ${cikis:.4f}\n"
        f"{'📈' if pnl >= 0 else '📉'} <b>PNL:</b> {pnl:+.2f} USDT\n"
        f"⚡ <b>ROE:</b> {roe_pct:+.1f}%\n"
        f"📝 <b>Neden:</b> {neden[:80]}\n"
        f"🔑 <b>Trade ID:</b> #{trade_id}"
    )


def _tg_firsat_mesaji(firsatlar: list) -> str:
    """Yüksek güvenli fırsat listesi için Telegram mesajı oluşturur."""
    satirlar = ["<b>🔥 YÜKSEK GÜVEN FIRSATLARI (%85+)</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for f in firsatlar[:8]:  # Max 8 fırsat
        sembol = f.get("sembol", "?")
        skor = f.get("guven_skoru", f.get("skor", 0))
        sinyal = f.get("sinyal", f.get("yon", "?"))
        kaldirac = f.get("kaldirac", "-")
        yon_emoji = "🟢" if sinyal in ["LONG", "AL", "GÜÇLÜ AL"] else "🔴" if sinyal in ["SHORT", "SAT", "GÜÇLÜ SAT"] else "⚪"
        satirlar.append(f"{yon_emoji} <b>{sembol}</b> | {sinyal} | %{skor:.0f} | {kaldirac}x")
    return "\n".join(satirlar)


# ─────────────────────────────────────────────
# V23: Telegram Command Listener Thread
# ─────────────────────────────────────────────
def telegram_komut_dinleyici(state_ref, lock, dur_sinyali: threading.Event):
    """Arka planda Telegram getUpdates endpoint'ini polling yapar.
    /status komutu gelince güncel özet gönderir."""
    global _TG_LAST_UPDATE_ID

    def _get_updates():
        token = getattr(cfg, "TELEGRAM_TOKEN", "")
        if not token:
            return []
        try:
            resp = _requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _TG_LAST_UPDATE_ID + 1, "timeout": 10},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("result", [])
        except Exception:
            pass
        return []

    def _status_ozeti() -> str:
        with lock:
            bakiye = state_ref.get("bakiye", 0)
            aktif = state_ref.get("aktif_pozisyonlar", {})
            durum = state_ref.get("bot_durumu", "?")
            mod = state_ref.get("mod", "?")
            guncel_fiyatlar = state_ref.get("guncel_fiyatlar", {})

        satirlar = [
            "<b>📊 PeroTrade — Durum Özeti</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🤖 <b>Durum:</b> {durum}",
            f"🎯 <b>Mod:</b> {mod}",
            f"💵 <b>Boşta Bakiye:</b> ${bakiye:,.2f}",
            f"📂 <b>Aktif Pozisyon:</b> {len(aktif)}",
        ]
        for tid, poz in list(aktif.items())[:5]:
            s = poz.get("sembol", tid)
            yon = poz.get("pozisyon", "?")
            giris = poz.get("giris_fiyati", 0)
            anlik = guncel_fiyatlar.get(s, giris)
            margin = poz.get("islem_margin", 0)
            kaldirac = poz.get("islem_kaldirac", 1)
            pnl = pnl_hesapla(yon, giris, anlik, margin * kaldirac, kaldirac)
            roe = (pnl / margin * 100) if margin > 0 else 0
            satirlar.append(f"  └ {s} {yon} | GF: ${giris:.4f} | PNL: {pnl:+.2f}$ ({roe:+.1f}%)")
        return "\n".join(satirlar)

    while not dur_sinyali.is_set():
        token = getattr(cfg, "TELEGRAM_TOKEN", "")
        chat_id = getattr(cfg, "TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            dur_sinyali.wait(30)  # Credentials yoksa 30s bekle
            continue

        updates = _get_updates()
        for upd in updates:
            _TG_LAST_UPDATE_ID = max(_TG_LAST_UPDATE_ID, upd.get("update_id", 0))
            msg = upd.get("message", {})
            text = msg.get("text", "").strip().lower()
            from_chat = msg.get("chat", {}).get("id")

            if text == "/status" and str(from_chat) == str(chat_id):
                threading.Thread(
                    target=send_telegram_msg,
                    args=(_status_ozeti(),),
                    daemon=True,
                ).start()

        dur_sinyali.wait(5)  # 5 saniyede bir polling


# ─────────────────────────────────────────────
# v11: Futures Sembol Dönüştürücü
# ─────────────────────────────────────────────
def futures_sembol_donustur(sembol: str) -> str:
    """Spot sembolü Futures Perpetual formatına çevirir.
    BTC/USDT → BTC/USDT:USDT
    Zaten ':USDT' içeriyorsa dokunmaz.
    """
    if not sembol:
        return sembol
    suffix = getattr(cfg, "FUTURES_SYMBOL_SUFFIX", ":USDT")
    if suffix and suffix not in sembol and "/USDT" in sembol:
        return sembol + suffix
    return sembol


def _exchange_olustur(state: dict, pro: bool = False) -> object:
    """v11: Futures-uyumlu exchange nesnesi oluşturur. Real API ise credentials ekler."""
    exchange_adi = state.get("exchange_adi", "binance")
    futures_type = getattr(cfg, "FUTURES_TYPE", "future")

    params = {
        "enableRateLimit": True,
        "options": {"defaultType": futures_type},
    }

    # Real API ise credential ekle
    if state.get("use_real_api", False):
        api_key = ps.decode_key(state.get("api_key_enc", ""))
        api_secret = ps.decode_key(state.get("api_secret_enc", ""))
        if api_key and api_secret:
            params["apiKey"] = api_key
            params["secret"] = api_secret

    lib = ccxtpro if pro else ccxt
    return getattr(lib, exchange_adi)(params)


# ─────────────────────────────────────────────
# Global Bot State (Thread-Safe)
# ─────────────────────────────────────────────
class GlobalBotState:
    """Thread-safe dict sarmalayıcı. Tüm trading verileri burada tutulur."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "bot_calisiyor": False,
            "bot_durumu": "Duraklatıldı",
            "bakiye": cfg.INITIAL_BALANCE,
            "baslangic_bakiye": cfg.INITIAL_BALANCE,
            "hedef_bakiye": cfg.TARGET_BALANCE,
            "aktif_pozisyonlar": {},
            "pik_bakiye": cfg.INITIAL_BALANCE,
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
            "mtf_konsensus": "KARARSIZ",

            # v7 State
            "usdt_d_deger": 0.0,
            "usdt_d_trend": "YATAY",
            "martingale_ardisik_kayip": 0,
            "martingale_carpan": 1.0,
            "baslangic_zamani": time.time(),

            "exchange_adi": cfg.EXCHANGE_NAME,
            "mod": "⚡ Agresif Mod",
            "ai_modu": "Mock AI",
            "openai_key": "",
            "global_risk_seviyesi": "Normal",
            "kaldirac": 10,
            "baslangic_zamani": 0.0,
            "hedef_sure_saat": 24.0,

            "son_fiyat_tick": 0.0,
            "cuzdan_gecmisi": [],
            "gun_baslangic_bakiye": cfg.INITIAL_BALANCE,

            # Serializable olmayan nesneler
            "dur_sinyali": threading.Event(),
            "analiz_tetikleyici": threading.Event(),

            # Mod bilgisi
            "use_real_api": False,
            "api_key_enc": "",
            "api_secret_enc": "",
            "guncel_fiyatlar": {},
        }

    @property
    def lock(self):
        return self._lock

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value

    def update(self, d: dict):
        with self._lock:
            self._data.update(d)

    def snapshot(self) -> dict:
        """UI için state'in thread-safe kopyasını döner."""
        with self._lock:
            snap = {}
            for k, v in self._data.items():
                if 'Lock' in str(type(v)) or 'Event' in str(type(v)):
                    continue
                if isinstance(v, dict):
                    snap[k] = v.copy()
                elif isinstance(v, list):
                    snap[k] = v.copy()
                else:
                    snap[k] = v
            return snap

    def raw(self) -> dict:
        """Bot engine thread'leri için doğrudan referans (lock ile birlikte kullanılmalı)."""
        return self._data

    def load_from_persistent(self):
        """Persistent state'den yükle."""
        try:
            loaded = ps.state_yukle()
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            print(f"⚠️ state_yukle hata: {e}")
            loaded = ps.DEFAULT_STATE.copy()

        if not isinstance(loaded, dict):
            loaded = ps.DEFAULT_STATE.copy()

        with self._lock:
            if loaded.get("bakiye", 0) > 0:
                self._data["bakiye"] = loaded.get("bakiye", cfg.INITIAL_BALANCE)
                self._data["baslangic_bakiye"] = loaded.get("baslangic_bakiye", cfg.INITIAL_BALANCE)
                self._data["gun_baslangic_bakiye"] = loaded.get("gun_baslangic_bakiye", self._data["bakiye"])
                self._data["aktif_pozisyonlar"] = loaded.get("aktif_pozisyonlar", {})
                self._data["islem_gecmisi"] = loaded.get("islem_gecmisi", [])
                self._data["max_drawdown"] = loaded.get("max_drawdown", 0.0)
                self._data["pik_bakiye"] = loaded.get("pik_bakiye", self._data["bakiye"])
                self._data["cuzdan_gecmisi"] = loaded.get("cuzdan_gecmisi", [])
                self._data["api_key_enc"] = loaded.get("api_key_enc", "")
                self._data["api_secret_enc"] = loaded.get("api_secret_enc", "")
                self._data["use_real_api"] = loaded.get("use_real_api", False)
                self._data["baslangic_zamani"] = loaded.get("baslangic_zamani", 0.0)
                self._data["hedef_bakiye"] = loaded.get("hedef_bakiye", cfg.TARGET_BALANCE)
                # v10: Challenge session'ı persistent state'den yükle
                ch_loaded = loaded.get("challenge_session", {})
                if isinstance(ch_loaded, dict) and ch_loaded:
                    self._data["challenge_session"] = ch_loaded
                # v10: Mod bilgisini de yükle
                if loaded.get("mod"):
                    self._data["mod"] = loaded["mod"]

    def save_to_persistent(self):
        """Disk'e serialize-safe kaydet."""
        try:
            temiz = {}
            with self._lock:
                for k, v in self._data.items():
                    if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                        temiz[k] = v
            ps.state_kaydet(temiz)
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            print(f"⚠️ save_to_persistent hata: {e}")


# ─────────────────────────────────────────────
# Yardımcı Fonksiyonlar
# ─────────────────────────────────────────────
MOD_PRESETLERI = {
    "⚡ Agresif Mod": {"risk": 1.0, "sma_kisa": 7, "sma_uzun": 25, "aralik_carpan": 0.5},
    "🌱 Soft Kar Modu": {"risk": 0.30, "sma_kisa": 14, "sma_uzun": 50, "aralik_carpan": 1.5},
    "💎 Ultra-Scalper": {"risk": 0.10, "sma_kisa": 3, "sma_uzun": 10, "aralik_carpan": 0.05},
    "🚀 94-Day Challenge": {"risk": 0.20, "sma_kisa": 5, "sma_uzun": 14, "aralik_carpan": 0.3},
    "🚀 Evolutionary Trainer": {"risk": 0.15, "sma_kisa": 5, "sma_uzun": 14, "aralik_carpan": 0.30},
}


def log_ekle(mesaj: str, state: dict, is_breakout=False, is_liq=False):
    zaman = datetime.now(timezone.utc).strftime("%H:%M:%S")
    state["ai_dusunce_gunlugu"].insert(0, {"time": zaman, "msg": mesaj, "breakout": is_breakout, "liq": is_liq})
    if len(state["ai_dusunce_gunlugu"]) > 60:
        state["ai_dusunce_gunlugu"].pop()


def pnl_hesapla(pozisyon, giris, anlik, miktar, kaldirac) -> float:
    if pozisyon == "YOK" or giris == 0:
        return 0.0
    margin = miktar / kaldirac
    if pozisyon == "LONG":
        pnl_pct = ((anlik - giris) / giris)
    else:
        pnl_pct = ((giris - anlik) / giris)
    return margin * pnl_pct * kaldirac


def likidasyon_hesapla(pozisyon, giris, kaldirac) -> float:
    if pozisyon == "YOK" or giris == 0:
        return 0.0
    if pozisyon == "LONG":
        return giris * (1 - (1 / kaldirac))
    elif pozisyon == "SHORT":
        return giris * (1 + (1 / kaldirac))
    return 0.0


def trade_id_olustur() -> str:
    """Benzersiz trade_id üretir (8 karakter hex)."""
    return uuid.uuid4().hex[:8]


def sembol_acik_mi(pozisyonlar: dict, sembol: str) -> bool:
    """Verilen sembolde açık pozisyon var mı kontrol eder."""
    return any(p.get("sembol") == sembol for p in pozisyonlar.values())


def sembol_icin_trade_id_bul(pozisyonlar: dict, sembol: str) -> str | None:
    """Verilen semboldeki ilk açık pozisyonun trade_id'sini döner."""
    for tid, p in pozisyonlar.items():
        if p.get("sembol") == sembol:
            return tid
    return None


def aktif_margin_toplami(pozisyonlar: dict) -> float:
    return sum(p.get("islem_margin", 0) for p in pozisyonlar.values())


def pnl_hesapla_coklu(pozlar, guncel_fiyatlar: dict) -> float:
    toplam_pnl = 0.0
    for tid, poz in pozlar.items():
        s = poz.get("sembol", tid)  # Geriye uyum: eski format sembol key ise tid=sembol
        anlik = guncel_fiyatlar.get(s, poz.get("giris_fiyati", 0))
        p_pnl = pnl_hesapla(poz.get("pozisyon", "YOK"), poz.get("giris_fiyati", 0), anlik,
                             poz.get("islem_margin", 0) * poz.get("islem_kaldirac", 1),
                             poz.get("islem_kaldirac", 1))
        toplam_pnl += p_pnl
    return toplam_pnl


def gunluk_kar_hesapla(state: dict) -> float:
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", cfg.INITIAL_BALANCE))
    if gun_baslangic <= 0:
        return 0.0
    mevcut = state.get("bakiye", gun_baslangic) + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
    return ((mevcut - gun_baslangic) / gun_baslangic) * 100


def islem_gecmisi_kaydet(gecmis: list, dosya="trade_history.csv"):
    if not gecmis:
        return
    headers = ["zaman", "sembol", "sinyal", "fiyat", "kaldirac", "poz_buyukluk", "bakiye_usdt", "kar_zarar", "ai_notu"]
    try:
        with open(dosya, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(gecmis)
    except (ccxt.BaseError, sqlite3.Error, Exception):
        pass


def islem_kapat(state, trade_id, fiyat, neden, is_breakout=False, is_liq=False):
    poz = state["aktif_pozisyonlar"].get(trade_id)
    if not poz:
        return

    sembol = poz.get("sembol", trade_id)  # Geriye uyum
    eski_poz = poz["pozisyon"]
    margin = poz["islem_margin"]
    kaldirac = poz["islem_kaldirac"]
    aktif_pnl = pnl_hesapla(eski_poz, poz["giris_fiyati"], fiyat, margin * kaldirac, kaldirac)
    poz_giris = poz["giris_fiyati"]  # v7: SQLite için sakla

    # v10: Challenge mod — komisyon simülasyonu + izole bakiye
    is_challenge = state.get("mod") == "🚀 94-Day Challenge"
    etiket = ""
    if is_challenge:
        ch = state.get("challenge_session", {})
        if isinstance(ch, dict) and ch.get("aktif"):
            komisyon_oran = getattr(cfg, "CHALLENGE_COMMISSION_RATE", 0.001)
            islem_hacmi = margin * kaldirac
            cikis_komisyon = islem_hacmi * komisyon_oran
            net_pnl = aktif_pnl - cikis_komisyon
            # Challenge bakiyesini güncelle (margin geri dön + net PNL)
            ch["bakiye"] = ch.get("bakiye", 10.0) + margin + net_pnl
            ch["toplam_kar"] = ch.get("toplam_kar", 0.0) + net_pnl
            ch["toplam_islem"] = ch.get("toplam_islem", 0) + 1
            if ch["bakiye"] > ch.get("pik_bakiye", 0):
                ch["pik_bakiye"] = ch["bakiye"]
            state["challenge_session"] = ch
            etiket = "CHALLENGE_MODE"
            log_ekle(f"🚀 CH KAPAT: {sembol} Net PNL: {net_pnl:+.4f} (Kom: {cikis_komisyon:.4f}). CH Bakiye: ${ch['bakiye']:.4f}", state)

    reel_getiri = margin + aktif_pnl
    state["bakiye"] += reel_getiri

    del state["aktif_pozisyonlar"][trade_id]

    kz_str = f"{aktif_pnl:+.2f} USDT"
    icon = "☠️" if is_liq else "🛡️" if "TS" in neden or "SL" in neden else "🔴"

    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    state["islem_gecmisi"].append({
        "zaman": zaman, "sembol": sembol, "sinyal": f"{icon} KAPAT: {eski_poz}",
        "fiyat": round(fiyat, 4), "kaldirac": f"{kaldirac}x", "poz_buyukluk": 0,
        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": kz_str, "ai_notu": neden
    })
    log_ekle(f"{icon} POZİSYON KAPATILDI: {sembol} {eski_poz}. PNL: {kz_str}", state, is_breakout, is_liq)
    # V23: Detaylı Telegram kapatış bildirimi
    threading.Thread(
        target=send_telegram_msg,
        args=(_tg_trade_kapanis_mesaji(
            sembol, eski_poz, poz_giris, fiyat, aktif_pnl, margin, neden, trade_id
        ),),
        daemon=True,
    ).start()

    # V25: Martingale kaldırıldı — artık risk hiçbir zaman katlanmıyor
    state["martingale_ardisik_kayip"] = 0
    state["martingale_carpan"] = 1.0

    # v7: SQLite'a işlem kapanışı kaydet
    # 🚀 Evolutionary Trainer: Genişletilmiş teknik indikatör verileri + ödül/ceza
    evo_rsi = None
    evo_boll_ust = None
    evo_boll_alt = None
    evo_hacim_oran = None
    is_evo = state.get("mod") == "🚀 Evolutionary Trainer"

    if is_evo:
        try:
            # Ödül/Ceza sistemi: perotrade_model.pkl güncelle
            ai_engine.evo_reward_update(aktif_pnl, sembol)
            puan_str = f"+{cfg.EVO_REWARD_POINTS}" if aktif_pnl > 0 else str(cfg.EVO_PENALTY_POINTS)
            log_ekle(f"🧪 EVO {'ÖDÜL' if aktif_pnl > 0 else 'CEZA'}: {sembol} PNL {aktif_pnl:+.4f} → Model Puan: {puan_str}", state)
        except (ccxt.BaseError, sqlite3.Error, Exception):
            pass
        # Genişletilmiş analiz: RSI, Bollinger, Hacim oranı hesapla
        try:
            import ccxt
            _evo_exchange = getattr(ccxt, state.get("exchange_adi", "binance"))({"enableRateLimit": True})
            evo_df = ai_engine.mum_verisi_cek(_evo_exchange, sembol, "1h", limit=30)
            if evo_df is not None and not evo_df.empty:
                evo_rsi = ai_engine.rsi_hesapla(evo_df)
                evo_boll = ai_engine.bollinger_hesapla(evo_df)
                evo_boll_ust = evo_boll.get("ust", 0.0)
                evo_boll_alt = evo_boll.get("alt", 0.0)
                if len(evo_df) >= 14:
                    son_hacim = float(evo_df['volume'].iloc[-1])
                    ort_hacim = float(evo_df['volume'].iloc[-14:].mean())
                    evo_hacim_oran = round(son_hacim / ort_hacim, 2) if ort_hacim > 0 else 0.0
                neden_detay = (f"RSI:{evo_rsi:.1f} | Boll:[{evo_boll_alt:.2f}-{evo_boll_ust:.2f}] | H.Oran:{evo_hacim_oran or 0:.2f}x")
                log_ekle(f"🧪 EVO DETAY: {sembol} {neden_detay}", state)
        except (ccxt.BaseError, sqlite3.Error, Exception):
            pass

    try:
        pnl_pct_val = (aktif_pnl / margin * 100) if margin > 0 else 0
        evo_etiket = "EVO_TRAINER" if is_evo else etiket
        data_logger.islem_kaydet(
            sembol=sembol, tip=eski_poz, giris_fiyati=poz_giris,
            cikis_fiyati=fiyat, pnl=aktif_pnl, pnl_pct=pnl_pct_val,
            kaldirac=kaldirac, margin=margin, neden=neden,
            etiket=evo_etiket,
            trade_id=trade_id,
            rsi=evo_rsi, bollinger_ust=evo_boll_ust,
            bollinger_alt=evo_boll_alt, hacim_oran=evo_hacim_oran,
            order_purpose=neden[:80] if neden else "",
            ai_confidence=state.get("ai_guven_skoru"),
            liquidity_depth_score=state.get("liquidity_depth_score")
        )
    except (ccxt.BaseError, sqlite3.Error, Exception):
        pass

    # Immediate Save: Kapanma sonrası anında mühürle
    try:
        temiz_kapat = {k: v for k, v in state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
        ps.state_kaydet(temiz_kapat)
    except Exception:
        pass


def dinamik_stop_loss_hesapla(exchange, sembol: str, pozisyon_tipi: str, giris_fiyati: float, kaldirac: int, atr_carpan: float = 1.5) -> float:
    """v6: ATR tabanlı dinamik stop-loss fiyatı. Oynak piyasa = geniş stop, durgun = dar stop."""
    try:
        df = ai_engine.mum_verisi_cek(exchange, sembol, "1h", limit=30)
        atr = ai_engine.atr_hesapla(df, 14)
        if atr <= 0 or giris_fiyati <= 0:
            return likidasyon_hesapla(pozisyon_tipi, giris_fiyati, kaldirac)
        sl_mesafe = atr * atr_carpan
        max_sl_mesafe = giris_fiyati * (0.8 / kaldirac)  # Likidasyon öncesinde kal
        sl_mesafe = min(sl_mesafe, max_sl_mesafe)
        if pozisyon_tipi == "LONG":
            return giris_fiyati - sl_mesafe
        else:
            return giris_fiyati + sl_mesafe
    except (ccxt.BaseError, sqlite3.Error, Exception):
        return likidasyon_hesapla(pozisyon_tipi, giris_fiyati, kaldirac)


def islem_kapat_with_retry(state, trade_id, fiyat, neden, exchange=None, max_retry=3, slippage_tolerance=0.005, is_breakout=False, is_liq=False):
    """v6: Slippage kontrollü kapama. Fiyat kayarsa retry yapar."""
    poz_ref = state.get("aktif_pozisyonlar", {}).get(trade_id, {})
    sembol = poz_ref.get("sembol", trade_id)
    for attempt in range(max_retry):
        try:
            guncel_fiyat = fiyat
            if exchange is not None and attempt > 0:
                try:
                    ticker = exchange.fetch_ticker(sembol)
                    if isinstance(ticker, dict) and ticker.get("last"):
                        guncel_fiyat = float(ticker["last"])
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    pass
            if attempt > 0 and abs(guncel_fiyat - fiyat) / max(fiyat, 0.0001) > slippage_tolerance:
                log_ekle(f"⚠️ SLIPPAGE #{attempt}: {sembol} fiyat kaydı ${fiyat:.4f}→${guncel_fiyat:.4f}. Yeniden deneniyor...", state)
            islem_kapat(state, trade_id, guncel_fiyat, neden, is_breakout, is_liq)
            return True
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            if attempt < max_retry - 1:
                log_ekle(f"⚠️ RETRY #{attempt+1}: {sembol} [{trade_id}] kapama hatası: {str(e)[:60]}", state)
                time.sleep(0.5)
            else:
                log_ekle(f"❌ KAPAMA BAŞARISIZ: {sembol} [{trade_id}] {max_retry} deneme sonrası kapanamadı!", state)
                return False
    return False


def bot_engine(state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
    # v11: Futures exchange init + auto-reconnect
    exchange = None
    def _baglanti_kur():
        nonlocal exchange
        for attempt in range(5):
            try:
                exchange = _exchange_olustur(state, pro=False)
                # Market Yükleme Güvencesi: try-except + retry
                for _mkt_attempt in range(3):
                    try:
                        exchange.load_markets()
                        break
                    except Exception as _mkt_err:
                        print(f"⚠️ Market data fetch failed, retrying... ({_mkt_attempt+1}/3): {str(_mkt_err)[:60]}")
                        time.sleep(5)
                with lock:
                    if not state.get("rest_connected_logged"):
                        log_ekle(f"🌐 Futures REST API bağlantısı kuruldu (defaultType: {getattr(cfg, 'FUTURES_TYPE', 'future')})", state)
                        state["rest_connected_logged"] = True
                return True
            except (ccxt.BaseError, sqlite3.Error, Exception) as e:
                with lock:
                    log_ekle(f"❌ Exchange bağlantı hatası (deneme {attempt+1}/5): {str(e)[:80]}", state)
                time.sleep(5)
        return False

    if not _baglanti_kur():
        with lock:
            log_ekle("❌ Exchange bağlantısı 5 denemede kurulamadı. Bot durduruluyor.", state)
        return

    # v11: Döngü sayacı (GC + Cooling)
    _dongü_sayaci = 0
    # Error throttling: 30sn'de bir özet log
    _error_counts = {}
    _last_error_log_time = time.time()

    # --- v10: Binance Position Sync (Prevent 0-price bug on restart) ---
    if state.get("use_real_api", False) and state.get("aktif_pozisyonlar"):
        try:
            positions = exchange.fetch_positions()
            with lock:
                for tid, poz in state["aktif_pozisyonlar"].items():
                    s = poz.get("sembol", tid)
                    for api_poz in positions:
                        if api_poz.get("symbol") == s and float(api_poz.get("contracts", 0)) > 0:
                            entry_price = float(api_poz.get("entryPrice", 0))
                            if entry_price > 0:
                                poz["giris_fiyati"] = entry_price
                                if "guncel_fiyatlar" not in state:
                                    state["guncel_fiyatlar"] = {}
                                state["guncel_fiyatlar"][s] = float(api_poz.get("markPrice", entry_price))
                            break
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            with lock:
                log_ekle(f"⚠️ Pozisyon Senkronizasyon Hatası: {e}", state)

    son_kayit_zamani = time.time()
    son_kayit_bakiye = state.get("bakiye", 0.0)

    while not dur_sinyali.is_set():
        try:
            preset = MOD_PRESETLERI.get(state.get("mod", "⚡ Agresif Mod"), MOD_PRESETLERI["⚡ Agresif Mod"])

            with lock:
                acik_poz_var_mi = len(state.get("aktif_pozisyonlar", {})) > 0
                if not acik_poz_var_mi:
                    log_ekle("🔍 Live Test: Breakout, BTC Trendi ve Fonlama verileri sentezleniyor...", state)

            try:
                btc_trend = ai_engine.btc_trendi_analiz_et(exchange)
            except (ccxt.BaseError, sqlite3.Error, Exception):
                btc_trend = "BİLİNMİYOR"

            try:
                top_coinler = ai_engine.top_coinleri_tara(exchange, limit=100)
            except (ccxt.BaseError, sqlite3.Error, Exception):
                top_coinler = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

            try:
                tarama_sonucu = ai_engine.anormallik_tara_ve_sec(exchange, top_coinler, preset["sma_kisa"], preset["sma_uzun"])
            except (ccxt.BaseError, sqlite3.Error, Exception):
                tarama_sonucu = {"secilen_sembol": "BTC/USDT", "secilen_pazar": {}, "secilen_sma": "BEKLE", "secilen_breakout": False, "taranan_liste": [], "karar_raporu": "", "haber_puanlari": {}}

            # --- MULTI-POSITION DÖNGÜSÜ ---
            secilen_coinler = tarama_sonucu.get("secilen_coinler", [])
            state_taranan_liste = tarama_sonucu.get("taranan_liste", [])
            if not secilen_coinler:
                secilen_coinler = [{"sembol": "BTC/USDT", "pazar": {}, "sma": "BEKLE", "is_breakout": False, "rapor": ""}]

            with lock:
                state["taranan_coinler"] = state_taranan_liste
            
            is_breakout_global = False
            bekleme_suresi_global = 30
            karar_paketi = {}

            # Bulk Ticker: Tüm fiyatları tek istekte çek (Rate Limit %90 azalır)
            try:
                _bulk_tickers = exchange.fetch_tickers()
            except (ccxt.BaseError, sqlite3.Error, Exception):
                _bulk_tickers = {}

            for index, c_data in enumerate(secilen_coinler):
                if dur_sinyali.is_set():
                    break
                    
                with lock:
                    mevcut_islem_sayisi = len(state.get("aktif_pozisyonlar", {}))
                max_islem = getattr(cfg, "MAX_CONCURRENT_TRADES", 99)
                
                secilen_sembol = c_data.get("sembol", "BTC/USDT")
                # BERSERKER MODE: Sınırsız işlem yapması için limit koşulu kapatıldı.
                # if mevcut_islem_sayisi >= max_islem and secilen_sembol not in state.get("aktif_pozisyonlar", {}):
                #     continue

                secilen_pazar = c_data.get("pazar", {})
                secilen_sma = c_data.get("sma", "BEKLE")
                is_breakout = c_data.get("is_breakout", False)
                karar_raporu = c_data.get("rapor", "")
                
                if is_breakout: is_breakout_global = True

                with lock:
                    state["taranan_coinler"] = tarama_sonucu.get("taranan_liste", [])
                    state["aktif_sembol"] = secilen_sembol
                    state["is_breakout"] = is_breakout
                    if is_breakout:
                        state["bot_durumu"] = "🚨 Breakout Modu!"
                        log_ekle(f"🔥 HACİM PATLAMASI: {secilen_sembol} (Hız 5s->2s)", state, is_breakout=True)
                    else:
                        state["bot_durumu"] = f"Çalışıyor ({mevcut_islem_sayisi}/{max_islem} İşlem)"
                    if index == 0 and karar_raporu:
                        for rapor_satiri in karar_raporu.split('\n'):
                            log_ekle(f"📊 {rapor_satiri}", state)

                # Fiyat Senkronizasyonu: Bulk cache → tekil fallback → son bilinen fiyat
                ticker = _bulk_tickers.get(secilen_sembol, {})
                if not isinstance(ticker, dict) or not ticker.get("last"):
                    try:
                        ticker = exchange.fetch_ticker(secilen_sembol)
                    except (ccxt.BaseError, sqlite3.Error, Exception):
                        ticker = {}
                if isinstance(ticker, dict) and ticker.get("last"):
                    fiyat = ticker.get("last", 0)
                    degisim = ticker.get("percentage", 0) or 0
                    hacim = ticker.get("quoteVolume", 0) or 0
                    # Başarılı fiyatı cache'e yaz
                    with lock:
                        state.setdefault("guncel_fiyatlar", {})[secilen_sembol] = fiyat
                else:
                    # Fallback: Son bilinen fiyat (Proxy/IP kısıtlaması durumu)
                    with lock:
                        fiyat = state.get("guncel_fiyatlar", {}).get(secilen_sembol, 0)
                    if not fiyat:
                        fiyat = secilen_pazar.get("fiyat", 0) if isinstance(secilen_pazar, dict) else 0
                    degisim, hacim = 0, 0

                fonlama = ai_engine.fonlama_orani_getir(exchange, secilen_sembol)

                # --- Multi-Timeframe Analiz (v9: 5 zaman dilimi) ---
                try:
                    mtf = ai_engine.multi_timeframe_analiz(exchange, secilen_sembol)
                    if isinstance(mtf, dict) and isinstance(mtf.get("detay"), dict):
                        d = mtf["detay"]
                        s5 = d.get("5dk", {}).get("sinyal", "?") if isinstance(d.get("5dk"), dict) else "?"
                        s15 = d.get("15dk", {}).get("sinyal", "?") if isinstance(d.get("15dk"), dict) else "?"
                        s1s = d.get("1s", {}).get("sinyal", "?") if isinstance(d.get("1s"), dict) else "?"
                        s1w = d.get("haftalik", {}).get("sinyal", "?") if isinstance(d.get("haftalik"), dict) else "?"
                        s1m = d.get("aylik", {}).get("sinyal", "?") if isinstance(d.get("aylik"), dict) else "?"
                        with lock:
                            state["mtf_konsensus"] = mtf.get("konsensus", "KARARSIZ")
                            state["makro_trend"] = mtf.get("makro_trend", "YATAY")
                            state["makro_risk_carpani"] = mtf.get("risk_carpani", 1.0)
                            log_ekle(f"🔬 Multi-TF: 5dk={s5} | 15dk={s15} | 1s={s1s} → {mtf.get('konsensus', '?')} (RSI Ort: {mtf.get('ortalama_rsi', 50)})", state)
                            if s1w != "?" or s1m != "?":
                                log_ekle(f"📈 Makro TF: Haftalık={s1w} | Aylık={s1m} → Trend: {mtf.get('makro_trend', 'YATAY')} (Risk: x{mtf.get('risk_carpani', 1.0):.2f})", state)
                    else:
                        mtf = {"konsensus": "KARARSIZ", "guc": 0, "risk_carpani": 1.0, "makro_trend": "YATAY"}
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    mtf = {"konsensus": "KARARSIZ", "guc": 0, "risk_carpani": 1.0, "makro_trend": "YATAY"}

                # --- Grid Analizi ---
                grid_trade_yapildi = False
                karar_override = None
                try:
                    df_grid = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "1h", limit=30)
                    grid_bilgi = ai_engine.grid_destek_direnc(df_grid)
                    if grid_bilgi.get("grid_uygun") and not sembol_acik_mi(state.get("aktif_pozisyonlar", {}), secilen_sembol):
                        with lock:
                            log_ekle(f"📏 GRID MODU: {secilen_sembol} yatay seyirde. Destek: ${grid_bilgi.get('destek', 0)}, Direnç: ${grid_bilgi.get('direnc', 0)}", state)
                            if fiyat <= grid_bilgi.get("destek", 0) * 1.01:
                                karar_override = "LONG"
                                log_ekle(f"📏 GRID LONG: Fiyat (${fiyat:.4f}) destek seviyesine yakın.", state)
                                grid_trade_yapildi = True
                            elif fiyat >= grid_bilgi.get("direnc", 0) * 0.99:
                                karar_override = "SHORT"
                                log_ekle(f"📏 GRID SHORT: Fiyat (${fiyat:.4f}) direnç seviyesine yakın.", state)
                                grid_trade_yapildi = True
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    grid_bilgi = {"grid_uygun": False}

                # --- DURUM KONTROLÜ ---
                pozisyonu_kapat = False
                kapat_sinyali_nedeni = ""

                with lock:
                    state["btc_trendi"] = btc_trend
                    state["fonlama_orani"] = fonlama.get("oran", 0)
                    state["fonlama_riski"] = fonlama.get("risk", "Yok")

                    # v7: USDT Dominance kontrolü
                    try:
                        usdt_d = ai_engine.usdt_dominance_getir()
                        state["usdt_d_deger"] = usdt_d.get("deger", 0)
                        state["usdt_d_trend"] = usdt_d.get("trend", "YATAY")
                        if usdt_d.get("etki") == "LONG_AZALT":
                            log_ekle(f"📊 USDT.D YÜKSELİYOR: %{usdt_d.get('deger', 0):.1f} → LONG iştahı azaltılıyor.", state)
                    except (ccxt.BaseError, sqlite3.Error, Exception):
                        pass

                    # V31: Dahili zamanlanmış resetler kaldırıldı — otonom stabilite modu
                    if not state.get("_v31_stabilite_loglandi"):
                        log_ekle("🛡️ V31: Dahili zamanlanmış resetler kaldırıldı, otonom stabilite moduna geçildi.", state)
                        state["_v31_stabilite_loglandi"] = True

                if dur_sinyali.is_set():
                    break

                # --- YAPAY ZEKA TAHMİNİ ---
                with lock:
                    _aktif_tid = sembol_icin_trade_id_bul(state.get("aktif_pozisyonlar", {}), secilen_sembol)
                    poz_durumu = state["aktif_pozisyonlar"][_aktif_tid].get("pozisyon", "YOK") if _aktif_tid else "YOK"

                # Zaman Baskısı
                zaman_baski_carpani = 1.0
                if state.get("baslangic_zamani", 0) > 0 and state.get("hedef_sure_saat", 0) > 0:
                    gecen_saat = (time.time() - state["baslangic_zamani"]) / 3600.0
                    sure_orani = gecen_saat / state["hedef_sure_saat"]
                    hedef_farki_pct = (state.get("hedef_bakiye", 100) - state.get("bakiye", 0)) / max(state.get("hedef_bakiye", 100), 1)

                    if sure_orani >= 0.80 and hedef_farki_pct > 0.20:
                        zaman_baski_carpani = 4.0
                        with lock:
                            state["bot_durumu"] = "💥 BERSERKER Modu!"
                            log_ekle(f"💥 BERSERKER MODU AKTİF! Süre: %{sure_orani * 100:.0f} geçti.", state)
                    elif sure_orani >= 0.70 and hedef_farki_pct > 0.30:
                        zaman_baski_carpani = 3.0
                        with lock:
                            log_ekle(f"🎯 FINAL HUNTER MODU AKTİF! Süre: %{sure_orani * 100:.0f} geçti.", state)
                    elif sure_orani >= 0.50 and hedef_farki_pct > 0.05:
                        zaman_baski_carpani = 2.0
                    elif sure_orani > 0.30 and hedef_farki_pct > 0:
                        zaman_baski_carpani = 1.0 + (sure_orani * hedef_farki_pct * 2.0)

                karar_paketi = {"karar": "BEKLE", "dusunce": kapat_sinyali_nedeni, "aralik_sn": 5}
                mtf_guc = mtf.get("guc", 0) if isinstance(mtf, dict) else 0
                
                # V26: MOLA koruma — süre dolana kadar yeni işlem açılmaz, süre dolunca otomatik devam
                mola_bitis = state.get("mola_bitis_zamani", 0)
                if mola_bitis > 0 and time.time() < mola_bitis:
                    kalan_dk = int((mola_bitis - time.time()) / 60)
                    karar_paketi["karar"] = "BEKLE"
                    karar_paketi["dusunce"] = f"🛡️ MOLA aktif. Kalan: {kalan_dk} dakika."
                    pozisyonu_kapat = False
                elif mola_bitis > 0 and time.time() >= mola_bitis:
                    # Mola süresi doldu — otomatik devam
                    state["mola_bitis_zamani"] = 0
                    state["bot_durumu"] = "Çalışıyor"
                    state["gunluk_pik_kar"] = 0.0  # Günlük pik sıfırla
                    # V31 FIX: Mola sonrası bakiyeyi yeni sıfır noktası olarak kabul et
                    # Eski zararın tekrar mola tetiklemesini engeller
                    state['gun_baslangic_bakiye'] = state['bakiye'] + aktif_margin_toplami(state.get('aktif_pozisyonlar', {}))
                    log_ekle(f"✅ MOLA BİTTİ: Bot otonom taramaya geri döndü. Yeni gün başlangıç bakiyesi: ${state['gun_baslangic_bakiye']:.2f}", state, is_breakout=True)
                    threading.Thread(
                        target=send_telegram_msg,
                        args=("✅ Mola bitti! Bot otonom taramaya geri döndü.",),
                        daemon=True,
                    ).start()
                
                if mola_bitis > 0 and time.time() < mola_bitis:
                    pass  # BEKLE zaten ayarlandı, elif/else'e düş
                elif not pozisyonu_kapat:
                    if not isinstance(secilen_pazar, dict) or not secilen_pazar:
                        karar_paketi = {"karar": "BEKLE", "dusunce": "Pazar verisi alınamadı, bekleniyor.", "aralik_sn": 30, "guven_skoru": 0, "expected_growth": 0, "tavsiye_kaldirac": 10, "tavsiye_oran": 0.10, "ozet": "Veri yok"}
                    else:
                        skor = ai_engine.kompozit_skor_hesapla(secilen_pazar, secilen_sma)
                        guven_base, _, _, _ = ai_engine.ai_metrikler(secilen_pazar, skor, zaman_baski_carpani)
                        
                        # V29: Ensemble için mum verisini çek (bütün AI modlarında kullanılacak)
                        _ensemble_df = None
                        try:
                            _ensemble_df = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "15m", limit=30)
                        except Exception:
                            pass
                        
                        of_data = None
                        of_min_conf = getattr(cfg, "ORDERFLOW_MIN_CONFIDENCE", 75)
                        if guven_base >= of_min_conf:
                            with lock:
                                log_ekle(f"📊 {secilen_sembol} Güven (%{guven_base:.1f}) > %{of_min_conf}. Emir defteri (Order Book) on-demand analiz ediliyor...", state)
                            
                            # V28 HYBRID: WebSocket Orderbook stream for high confidence > 80%
                            if guven_base > 80:
                                state["ws_ob_sembol"] = secilen_sembol
                                pre_ob = state.get("guncel_orderbooks", {}).get(secilen_sembol)
                                if pre_ob:
                                    of_data = ai_engine.analiz_emir_akisi(exchange, secilen_sembol, pre_fetched_ob=pre_ob)
                                else:
                                    of_data = ai_engine.analiz_emir_akisi(exchange, secilen_sembol)
                            else:
                                if state.get("ws_ob_sembol") == secilen_sembol:
                                    state["ws_ob_sembol"] = None
                                of_data = ai_engine.analiz_emir_akisi(exchange, secilen_sembol)
                            
                            # v24/V28: Binance Rate Limit used_weight update (Weight-Aware Throttling)
                            try:
                                headers = exchange.last_response_headers
                                if headers and hasattr(headers, "get") and headers.get('x-mbx-used-weight-1m'):
                                    used_w = int(headers.get('x-mbx-used-weight-1m'))
                                    with lock:
                                        state["used_weight_1m"] = used_w
                                        if used_w > 4500:
                                            state["limit_uyari_kritik"] = True
                                            log_ekle(f"⚠️ API Yükü Kritik (used_weight): {used_w}/6000. Sistem soğumaya alınıyor.", state)
                                            threading.Thread(target=send_telegram_msg, args=("⚠️ API Yükü Kritik: Sistem soğumaya alınıyor.",), daemon=True).start()
                                        elif used_w > 2000:
                                            state["limit_uyari"] = True
                                            state["limit_uyari_kritik"] = False
                                            log_ekle(f"⚠️ API Yük Uyarısı (used_weight): {used_w}/6000. Soğuma (Cooling) süresi dinamik olarak artırılacak.", state)
                                        else:
                                            state["limit_uyari"] = False
                                            state["limit_uyari_kritik"] = False
                            except Exception:
                                pass
                                
                            if of_data and of_data.get("is_valid"):
                                with lock:
                                    t_coinler = state.get("taranan_coinler", [])
                                    for idx_c, c in enumerate(t_coinler):
                                        if isinstance(c, dict) and (c.get("Sembol") == secilen_sembol or c.get("sembol", "") == secilen_sembol):
                                            of_str = f"{of_data.get('durum', '')} (x{of_data.get('oran',1.0):.1f})"
                                            t_coinler[idx_c]["order_flow"] = of_str
                                    state["taranan_coinler"] = t_coinler
                        
                        if state.get("ai_modu") == "Local ML":
                            karar_paketi = ai_engine.local_ml_karar(
                                secilen_sembol, secilen_pazar, secilen_sma, poz_durumu,
                                btc_trend, fonlama, zaman_baski_carpani,
                                mod=state.get("mod", ""), mtf_guc=mtf_guc, order_flow=of_data,
                                ensemble_df=_ensemble_df
                            )
                        elif state.get("ai_modu") == "OpenAI LLM" and state.get("openai_key"):
                            karar_paketi = ai_engine.llm_karar(secilen_sembol, secilen_pazar, secilen_sma, state["openai_key"], poz_durumu, btc_trend, fonlama, zaman_baski_carpani)
                        else:
                            karar_paketi = ai_engine.mock_ai_karar(secilen_sembol, secilen_pazar, skor, poz_durumu, btc_trend, fonlama, zaman_baski_carpani, mod=state.get("mod", ""), order_flow=of_data, ensemble_df=_ensemble_df)
                        
                        # V29: Ensemble raporu loglama
                        ens_rapor = karar_paketi.get("ensemble_rapor", "")
                        if ens_rapor:
                            with lock:
                                log_ekle(ens_rapor, state)

                        # V30: LRC Raporlama + Extreme Overextension Cezası
                        try:
                            _lrc_df = _ensemble_df if _ensemble_df is not None else ai_engine.mum_verisi_cek(exchange, secilen_sembol, "15m", limit=100)
                            if _lrc_df is not None and not _lrc_df.empty:
                                _lrc = ai_engine.lrc_analizi_yap(_lrc_df, period=100)
                                if _lrc.get("gecerli") and fiyat > 0:
                                    _lrc_orta = _lrc["lrc_orta"]
                                    _lrc_ust = _lrc["lrc_ust"]
                                    _lrc_alt = _lrc["lrc_alt"]
                                    _lrc_slope = _lrc["lrc_slope"]
                                    _slope_icon = "↗" if _lrc_slope > 0 else "↘" if _lrc_slope < 0 else "→"

                                    with lock:
                                        # Kanal pozisyon raporu
                                        if fiyat < _lrc_alt:
                                            log_ekle(f"📉 [LRC] Fiyat kanal ALTINDA: ${fiyat:.4f} < Alt ${_lrc_alt:.4f} (Eğim: {_slope_icon}{_lrc_slope:.6f}). Geri dönüş bekleniyor.", state)
                                        elif fiyat > _lrc_ust:
                                            log_ekle(f"📈 [LRC] Fiyat kanal ÜSTüNDE: ${fiyat:.4f} > Üst ${_lrc_ust:.4f} (Eğim: {_slope_icon}{_lrc_slope:.6f}). Geri dönüş bekleniyor.", state)
                                        else:
                                            log_ekle(f"📊 [LRC] Fiyat kanal İÇİNDE: Alt ${_lrc_alt:.4f} < ${fiyat:.4f} < Üst ${_lrc_ust:.4f} (Orta: ${_lrc_orta:.4f}, Eğim: {_slope_icon})", state)

                                    # Extreme Overextension: Fiyat kanalın %10'undan fazla dışına taşmışsa
                                    _kanal_genislik = _lrc_ust - _lrc_alt
                                    if _kanal_genislik > 0:
                                        if fiyat < _lrc_alt:
                                            _tasma_pct = ((_lrc_alt - fiyat) / _kanal_genislik) * 100
                                        elif fiyat > _lrc_ust:
                                            _tasma_pct = ((fiyat - _lrc_ust) / _kanal_genislik) * 100
                                        else:
                                            _tasma_pct = 0.0

                                        if _tasma_pct > 10.0:
                                            # FOMO Cezası: Güven skorunu %20 düşür
                                            _mevcut_guven = karar_paketi.get("guven_skoru", 0)
                                            _cezali_guven = _mevcut_guven * 0.80
                                            karar_paketi["guven_skoru"] = _cezali_guven
                                            with lock:
                                                log_ekle(
                                                    f"🚨 [LRC] EXTREME OVEREXTENSION! Fiyat kanalın %{_tasma_pct:.1f} dışında. "
                                                    f"FOMO cezası: Güven %{_mevcut_guven:.0f} → %{_cezali_guven:.0f} (-%20). "
                                                    f"Geri dönüş bekleniyor, agresif giriş engellendi.",
                                                    state
                                                )
                                            # Aşırı taşma durumunda açılış kararlarını engelle
                                            if karar_paketi.get("karar") in ["LONG", "SHORT"] and _tasma_pct > 20.0:
                                                karar_paketi["karar"] = "BEKLE"
                                                karar_paketi["dusunce"] = f"🚨 [LRC FOMO GUARD] Fiyat kanalın %{_tasma_pct:.1f} dışında. Geri dönüş beklenene kadar işlem engellendi. " + karar_paketi.get("dusunce", "")
                        except Exception:
                            pass

                    # v8: Kesin Kar (Sure Profit) Korelasyon Mantığı
                    kesin_kar = state.get("kesin_kar_parametreleri", {})
                    if kesin_kar and isinstance(secilen_pazar, dict):
                        vol = secilen_pazar.get("volatilite", 0)
                        h_artis = secilen_pazar.get("hacim_artis", secilen_pazar.get("hacim_artis_pct", 0))
                        
                        b_vol = kesin_kar.get("ortalama_volatilite", 0)
                        b_hacim = kesin_kar.get("ortalama_hacim_artis", 0)
                        
                        if b_vol > 0 and b_hacim > 0:
                            # Volatilite ve hacim artışı tarihsel kârlı ortalamanın en az %80'iyse
                            if h_artis >= (b_hacim * 0.8) and vol >= (b_vol * 0.8):
                                if karar_paketi.get("karar") in ["LONG", "SHORT"]:
                                    karar_paketi["guven_skoru"] = max(95.0, karar_paketi.get("guven_skoru", 0))
                                    karar_paketi["dusunce"] = f"🌟 KESİN KÂR SENARYOSU! Geçmiş verilere (Vol={vol:.1f}, H.Artış={h_artis:.0f}%) uyuşuyor. " + karar_paketi.get("dusunce", "")

                    # NLP Haber Veto (cfg.ENABLE_NEWS_VETO ile kontrol edilir)
                    if cfg.ENABLE_NEWS_VETO:
                        haber_puanlari = tarama_sonucu.get("haber_puanlari", {})
                        if haber_puanlari:
                            veto_sonuc = ai_engine.haber_vetosu(haber_puanlari, karar_paketi.get("karar", "BEKLE"))
                            if veto_sonuc.get("veto"):
                                with lock:
                                    log_ekle(veto_sonuc.get("neden", ""), state)
                                karar_paketi["karar"] = "BEKLE"
                                karar_paketi["dusunce"] = veto_sonuc.get("neden", "")
                            elif veto_sonuc.get("neden"):
                                with lock:
                                    log_ekle(veto_sonuc["neden"], state)
                    # Bakiye Senkronizasyonu (Manual Injection Guard)
                    # Challenge modunda bu kontrolü ATLA — challenge kendi izole bakiyesiyle çalışır
                    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", cfg.INITIAL_BALANCE))
                    mevcut_bakiye = state.get("bakiye", gun_baslangic) + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                
                    if not (state.get("mod") == "🚀 94-Day Challenge") and gun_baslangic > 0 and ((mevcut_bakiye - gun_baslangic) / gun_baslangic) * 100 >= 100.0:
                        with lock:
                            state["gun_baslangic_bakiye"] = mevcut_bakiye
                            log_ekle(f"🔄 Bakiye Senkronizasyonu: Manuel ekleme tespit edildi. Yeni Gün Başlangıç: ${mevcut_bakiye:.2f}", state)

                    # v8: Dinamik Kâr Kilidi & Zarar Kurtarma (Recovery Mode)
                    # Challenge modunda bu karar mantığı ATLANIR — challenge'in kendi TS'si var
                    gunluk_kar = gunluk_kar_hesapla(state)
                    pik_kar = state.get("gunluk_pik_kar", 0.0)
                    if not (state.get("mod") == "🚀 94-Day Challenge"):
                        if gunluk_kar > pik_kar:
                            state["gunluk_pik_kar"] = gunluk_kar
                            pik_kar = gunluk_kar

                    hedef_pct = getattr(cfg, "DAILY_TARGET_PCT", 10.0)

                    # v10: 94-Day Challenge — Closed-Trade PNL + İzole Trailing Stop
                    is_challenge = state.get("mod") == "🚀 94-Day Challenge"
                    if is_challenge:
                        ch = state.get("challenge_session", {})
                        if isinstance(ch, dict) and ch.get("aktif"):
                            ch_gun_bas = ch.get("gun_baslangic_bakiye", 10.0)
                            ch_bakiye = ch.get("bakiye", ch_gun_bas)

                            # v10: Hedef kontrolünü sadece KAPANMIŞ işlemler üzerinden yap (False Positive engelleme)
                            ch_gun_baslangic_zamani = ch.get("gun_baslangic_zamani", ch.get("baslangic_zamani", 0))
                            try:
                                ch_realized_pnl = data_logger.challenge_pnl_getir(ch_gun_baslangic_zamani)
                            except (ccxt.BaseError, sqlite3.Error, Exception):
                                ch_realized_pnl = 0.0
                            ch_kar_pct = (ch_realized_pnl / ch_gun_bas * 100) if ch_gun_bas > 0 else 0

                            # Günlük pik takibi (realized PNL bazlı)
                            ch_pik = ch.get("gunluk_pik_kar_pct", 0.0)
                            if ch_kar_pct > ch_pik:
                                ch["gunluk_pik_kar_pct"] = ch_kar_pct
                                ch_pik = ch_kar_pct

                            ch_ts_activate = getattr(cfg, "CHALLENGE_TRAILING_STOP_ACTIVATE", 10.0)
                            ch_ts_step = getattr(cfg, "CHALLENGE_TRAILING_STOP_STEP", 2.0)

                            # Trailing stop aktifleştirme ve hedef durumunu güncelleme: %10 hedeften sonra
                            if ch_pik >= ch_ts_activate:
                                ch["target_achieved"] = True
                                yeni_stop = ch_pik - ch_ts_step
                                eski_stop = ch.get("trailing_stop_seviyesi", 0.0)
                                if yeni_stop > eski_stop:
                                    ch["trailing_stop_seviyesi"] = yeni_stop
                                    log_ekle(f"🚀 CHALLENGE TS: Pik %{ch_pik:.1f} → Stop %{yeni_stop:.1f}", state)

                                if ch_kar_pct < ch.get("trailing_stop_seviyesi", 0.0) and ch.get("trailing_stop_seviyesi", 0) > 0:
                                    karar_paketi["karar"] = "BEKLE"
                                    karar_paketi["dusunce"] = f"🚀 CHALLENGE KORU: Realized Kâr %{ch_kar_pct:.1f} < Stop %{ch.get('trailing_stop_seviyesi', 0):.1f}. Yeni işlem yok."
                                    state["bot_durumu"] = "🚀 Challenge Koruma"
                            else:
                                pass  # Henüz %10'a ulaşmadı, normal işlem devam

                            state["challenge_session"] = ch
                    elif pik_kar >= hedef_pct:
                        kilit_seviyesi = hedef_pct * getattr(cfg, "PROFIT_LOCK_RATIO", 0.8)
                        if gunluk_kar < kilit_seviyesi:
                            karar_paketi["karar"] = "BEKLE"
                            karar_paketi["dusunce"] = f"🛡️ GÜVENLİ MOD: Kâr kilidi (%{kilit_seviyesi:.1f}) tetiklendi! Korunuyor."
                            state["bot_durumu"] = "🛡️ Güvenli Mod"
                        else:
                            # HFT devam! Durdurmak yok.
                            pass

                    loss_stop = getattr(cfg, "DAILY_LOSS_STOP", -15.0)
                    if not is_challenge and gunluk_kar <= loss_stop and getattr(cfg, "EMERGENCY_STOP_ENABLED", True):
                        # V26: MOLA SİSTEMİ — tüm pozisyonları kapat, 4 saat bekle, sonra otomatik devam
                        mola_saat = getattr(cfg, "COOLING_OFF_HOURS", 4)
                        mola_bitis = time.time() + (mola_saat * 3600)
                        state["bot_durumu"] = f"🛡️ MOLA VERİLDİ ({mola_saat} Saat)"
                        state["mola_bitis_zamani"] = mola_bitis
                        
                        kapanacak_tids = list(state.get("aktif_pozisyonlar", {}).keys())
                        fiyatlar_cache = state.get("guncel_fiyatlar", {})
                        for e_tid in kapanacak_tids:
                            e_poz = state["aktif_pozisyonlar"].get(e_tid)
                            if e_poz:
                                e_sembol = e_poz.get("sembol", e_tid)
                                e_fiyat = fiyatlar_cache.get(e_sembol, e_poz.get("giris_fiyati", 0))
                                if e_fiyat > 0:
                                    islem_kapat(state, e_tid, e_fiyat, f"🛡️ V26 MOLA: Günlük kayıp %{gunluk_kar:.1f} < %{loss_stop}")
                        karar_paketi["karar"] = "BEKLE"
                        karar_paketi["dusunce"] = f"🛡️ MOLA: Günlük kayıp %{gunluk_kar:.1f}. Tüm pozisyonlar kapatıldı, {mola_saat} saat bekleniyor."
                        log_ekle(f"🛡️ MOLA AKTİF! Günlük kayıp: %{gunluk_kar:.1f}. Tüm pozisyonlar kapatıldı. {mola_saat} saat sonra otomatik devam.", state, is_breakout=True)
                        
                        # V26: Telegram detaylı mola raporu
                        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                        devam_saati = _dt.fromtimestamp(mola_bitis, tz=_tz(offset=_td(hours=3))).strftime("%H:%M")
                        tg_mola_msg = (
                            f"🚨 Piyasada sert dalgalanma! "
                            f"%{abs(gunluk_kar):.1f} kayıp eşiği nedeniyle {mola_saat} saatlik koruma molası başladı. "
                            f"Saat {devam_saati}'de otonom tarama devam edecek."
                        )
                        threading.Thread(
                            target=send_telegram_msg,
                            args=(tg_mola_msg,),
                            daemon=True,
                        ).start()

                    # DCA
                    dca_tid = sembol_icin_trade_id_bul(state.get("aktif_pozisyonlar", {}), secilen_sembol)
                    if dca_tid:
                        poz = state["aktif_pozisyonlar"][dca_tid]
                        dca = ai_engine.dca_hesapla(poz, fiyat, state.get("bakiye", 0))
                        if dca.get("uygun"):
                            with lock:
                                log_ekle(f"💱 DCA ÖNERİ: {secilen_sembol} - {dca.get('neden', '')}", state)
                                ekleme = dca.get("ekleme_margin", 0)
                                if ekleme <= state.get("bakiye", 0):
                                    state["aktif_pozisyonlar"][dca_tid]["islem_margin"] += ekleme
                                    state["aktif_pozisyonlar"][dca_tid]["giris_fiyati"] = dca.get("yeni_ortalama", poz.get("giris_fiyati", 0))
                                    state["aktif_pozisyonlar"][dca_tid]["dca_sayisi"] = dca.get("dca_sayisi", 1)
                                    state["bakiye"] -= ekleme
                                    log_ekle(f"✅ DCA UYGULANDI: ${ekleme:.2f} eklendi.", state)
                                    # Immediate Save: DCA (Bakiye Güncelleme) anında mühürle
                                    try:
                                        tem_s = {k: v for k, v in state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                                        ps.state_kaydet(tem_s)
                                    except Exception:
                                        pass
                else:
                    karar_paketi["karar"] = "KAPAT"

                # Grid override
                if karar_override and grid_trade_yapildi:
                    karar_paketi["karar"] = karar_override

                # --- İŞLEM UYGULAMA ---
                with lock:
                    state["fiyat"] = fiyat
                    state["degisim_24s"] = degisim
                    state["hacim_24s"] = hacim
                    state["ai_guven_skoru"] = karar_paketi.get("guven_skoru", 0.0)
                    state["ai_beklenen_artis"] = karar_paketi.get("expected_growth", 0.0)
                    state["ai_analiz_ozeti"] = karar_paketi.get("ozet", kapat_sinyali_nedeni)

                    toplam_varlik = state["bakiye"] + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                    state["cuzdan_gecmisi"].append({"zaman": datetime.now(timezone.utc).strftime("%H:%M:%S"), "deger": round(toplam_varlik, 2)})
                    if len(state["cuzdan_gecmisi"]) > 200:
                        state["cuzdan_gecmisi"] = state["cuzdan_gecmisi"][-200:]

                    total_kullanilan = aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                    top_v = state["bakiye"] + total_kullanilan
                    risk_pct = (total_kullanilan / top_v) * 100 if top_v > 0 else 0
                    if risk_pct > 15:
                        state["global_risk_seviyesi"] = "🔴 Yüksek Risk"
                    elif risk_pct > 5:
                        state["global_risk_seviyesi"] = "🟡 Orta Risk"
                    else:
                        state["global_risk_seviyesi"] = "🟢 Düşük Risk"

                    if not pozisyonu_kapat:
                        log_ekle(f"🎯 {secilen_sembol} Analizi: {karar_paketi.get('dusunce', '')}", state, is_breakout=is_breakout)
                        sinyal_k = karar_paketi.get("karar", "BEKLE")
                        if sinyal_k in ["LONG", "SHORT"]:
                            log_ekle(f"📝 KARAR: {sinyal_k} - Sebep: {karar_paketi.get('dusunce', '')[:80]}...", state)

                    sinyal = karar_paketi.get("karar", "BEKLE")
                    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                    # v6: MTF Konsensüs Gate — LONG/SHORT sadece MTF onaylıysa geçer
                    if sinyal in ["LONG", "SHORT"] and not sembol_acik_mi(state.get("aktif_pozisyonlar", {}), secilen_sembol):
                        mtf_k = mtf.get("konsensus", "KARARSIZ") if isinstance(mtf, dict) else "KARARSIZ"
                        mtf_gecti = False
                        if sinyal == "LONG" and mtf_k in ["GÜÇLÜ AL", "ZAYIF AL"]:
                            mtf_gecti = True
                        elif sinyal == "SHORT" and mtf_k in ["GÜÇLÜ SAT", "ZAYIF SAT"]:
                            mtf_gecti = True
                        
                        # 🚀 Evolutionary Trainer: MTF gate bypass — maksimum veri toplama
                        if state.get("mod") == "🚀 Evolutionary Trainer":
                            mtf_gecti = True
                            log_ekle(f"🧪 EVO MTF BYPASS: {secilen_sembol} {sinyal} MTF gate atlandı (veri toplama modu).", state)

                        if not mtf_gecti:
                            log_ekle(f"🔬 MTF GATE REDDETTİ: {secilen_sembol} {sinyal} kararı MTF ({mtf_k}) ile çelişiyor. İşlem iptal.", state)
                            sinyal = "BEKLE"  # MTF onaylamıyor, işlem iptal
                        else:
                            log_ekle(f"✅ MTF GATE ONAYLADI: {secilen_sembol} {sinyal} → MTF: {mtf_k}", state)

                    # V25: OrderFlow Likidite Vetosu — emir defteri derinliği işlem büyüklüğünün 5 katından azsa işleme girme
                    if sinyal in ["LONG", "SHORT"] and not sembol_acik_mi(state.get("aktif_pozisyonlar", {}), secilen_sembol):
                        try:
                            of_veto_mult = getattr(cfg, "ORDERFLOW_LIQUIDITY_VETO_MULT", 5)
                            # Tahmini büyüklük: equity * trade_risk * kaldıraç
                            _eq = state.get("bakiye", 0) + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                            _tr = float(state.get("trade_risk_pct", getattr(cfg, "TRADE_RISK_PCT", 10.0))) / 100.0
                            _tahmini_buyukluk = _eq * _tr * karar_paketi.get("tavsiye_kaldirac", 10)
                            
                            # of_data V24'te tanımlı — on-demand çekilmişse kontrol et
                            if of_data and of_data.get("is_valid"):
                                ob_toplam = of_data.get("alici_hacim", 0) + of_data.get("satici_hacim", 0)
                                gerekli_derinlik = _tahmini_buyukluk * of_veto_mult
                                if ob_toplam > 0 and ob_toplam < gerekli_derinlik:
                                    log_ekle(f"🛡️ V25 LİKİDİTE VETO: {secilen_sembol} emir defteri derinliği (${ob_toplam:,.0f}) < gereken (${gerekli_derinlik:,.0f} = işlem x{of_veto_mult}). İŞLEM ENGELLENDİ.", state)
                                    sinyal = "BEKLE"
                        except Exception:
                            pass

                    if sinyal in ["LONG", "SHORT"] and not sembol_acik_mi(state.get("aktif_pozisyonlar", {}), secilen_sembol):
                        # v9: Challenge mod kaldıraç/risk override
                        if state.get("mod") == "🚀 94-Day Challenge":
                            ch_data = state.get("challenge_session", {})
                            ch_bakiye = ch_data.get("bakiye", 10.0) if isinstance(ch_data, dict) else 10.0
                            tavsiye_oran = getattr(cfg, "CHALLENGE_RISK_PER_TRADE", 0.20)
                            ch_min_lev = getattr(cfg, "CHALLENGE_MIN_LEVERAGE", 20)
                            ch_max_lev = getattr(cfg, "CHALLENGE_MAX_LEVERAGE", 50)
                            if ch_bakiye < 50:
                                tavsiye_kaldirac = ch_max_lev
                            elif ch_bakiye < 500:
                                tavsiye_kaldirac = max(ch_min_lev, int(ch_max_lev * 0.7))
                            else:
                                tavsiye_kaldirac = ch_min_lev
                            log_ekle(f"🚀 CHALLENGE: Kaldıraç={tavsiye_kaldirac}x, Risk=%{tavsiye_oran*100:.0f}, Bakiye=${ch_bakiye:.2f}", state)
                        else:
                            tavsiye_kaldirac = karar_paketi.get("tavsiye_kaldirac", 10)
                            tavsiye_oran = karar_paketi.get("tavsiye_oran", 0.10)

                        # v9: Makro Trend Risk Çarpanı
                        makro_risk = state.get("makro_risk_carpani", 1.0)
                        if sinyal == "LONG" and makro_risk < 1.0:
                            tavsiye_oran = tavsiye_oran * makro_risk
                            tavsiye_kaldirac = max(1, int(tavsiye_kaldirac * makro_risk))
                            log_ekle(f"📈 MAKRO FİLTRE: Aylık/Haftalık trend düşüşte → LONG risk x{makro_risk:.2f} azaltıldı. Oran: {tavsiye_oran:.2f}, Kaldıraç: {tavsiye_kaldirac}x", state)
                        elif sinyal == "SHORT" and makro_risk < 1.0:
                            # Düşüş trendi SHORT'a avantaj → %25 artır
                            tavsiye_oran = min(tavsiye_oran * 1.25, 0.50)
                            log_ekle(f"📈 MAKRO FİLTRE: Düşüş trendi SHORT lehine → Oran: {tavsiye_oran:.2f}", state)

                        # v7: USDT.D LONG baskılama
                        if sinyal == "LONG" and state.get("usdt_d_trend") == "YUKARI":
                            tavsiye_oran = tavsiye_oran * 0.7  # %30 azalt
                            tavsiye_kaldirac = max(1, int(tavsiye_kaldirac * 0.7))
                            log_ekle(f"📊 USDT.D BASKILAMA: LONG oran/kaldıraç %30 azaltıldı. Oran: {tavsiye_oran:.2f}, Kaldıraç: {tavsiye_kaldirac}x", state)

                        # V25: Martingale kaldırıldı — sabit 1x çarpan
                        mart_carpan = 1.0

                        risk_limit = 0.40 if zaman_baski_carpani >= 4.0 else 0.30 if zaman_baski_carpani >= 3.0 else 0.20
                        kullanilabilir_max = min(tavsiye_oran, risk_limit - (risk_pct / 100.0))
                        if kullanilabilir_max > 0:
                            # v10: Challenge modda margin'ı challenge bakiyesinden hesapla
                            if state.get("mod") == "🚀 94-Day Challenge":
                                ch_data_ac = state.get("challenge_session", {})
                                ch_bakiye_ac = ch_data_ac.get("bakiye", 10.0) if isinstance(ch_data_ac, dict) else 10.0
                                margin = ch_bakiye_ac * kullanilabilir_max * mart_carpan
                                margin = min(margin, ch_bakiye_ac * 0.5)
                            else:
                                # V29: Confidence-Based Sizing (Free Will)
                                if getattr(cfg, "CONFIDENCE_BASED_SIZING", False):
                                    _guven_fw = karar_paketi.get("guven_skoru", 0)
                                    aktif_kullanilan_margin = sum(p.get("islem_margin", 0) for p in state.get("aktif_pozisyonlar", {}).values())
                                    mevcut_bakiye = state["bakiye"]
                                    toplam_equity = mevcut_bakiye + aktif_kullanilan_margin
                                    
                                    if _guven_fw >= 98:
                                        fw_oran = 0.50   # %50 Wallet — çok yüksek güven
                                    elif _guven_fw >= 90:
                                        fw_oran = 0.25   # %25 Wallet
                                    else:
                                        fw_oran = 0.15   # %15 Wallet (minimum)
                                    
                                    margin = toplam_equity * fw_oran * mart_carpan
                                    
                                    # Cüzdan güvenlik limitleri
                                    c_max_wallet_risk = float(state.get("max_wallet_risk_pct", getattr(cfg, "MAX_WALLET_RISK_PCT", 100.0))) / 100.0
                                    kullanilabilir_hedef_kasa = toplam_equity * c_max_wallet_risk
                                    kalan_risk_limiti = max(0.0, kullanilabilir_hedef_kasa - aktif_kullanilan_margin)
                                    margin = min(margin, kalan_risk_limiti)
                                    margin = min(margin, mevcut_bakiye)
                                    margin = min(margin, mevcut_bakiye * 0.5)  # Hard cap
                                    
                                    log_ekle(f"💡 FREE WILL: Güven %{_guven_fw:.0f} → Wallet %{fw_oran*100:.0f}. Margin: ${margin:.2f}", state)
                                else:
                                    # Eski V19/V25 Gelişmiş Risk Yönetimi (fallback)
                                    c_max_wallet_risk = float(state.get("max_wallet_risk_pct", getattr(cfg, "MAX_WALLET_RISK_PCT", 100.0))) / 100.0
                                    c_trade_risk = float(state.get("trade_risk_pct", getattr(cfg, "TRADE_RISK_PCT", 10.0))) / 100.0
                                    
                                    aktif_kullanilan_margin = sum(p.get("islem_margin", 0) for p in state.get("aktif_pozisyonlar", {}).values())
                                    mevcut_bakiye = state["bakiye"]
                                    toplam_equity = mevcut_bakiye + aktif_kullanilan_margin
                                    kullanilabilir_hedef_kasa = toplam_equity * c_max_wallet_risk
                                    
                                    margin_hedef = toplam_equity * c_trade_risk * mart_carpan
                                    kalan_risk_limiti = max(0.0, kullanilabilir_hedef_kasa - aktif_kullanilan_margin)
                                    margin = min(margin_hedef, kalan_risk_limiti)
                                    margin = min(margin, mevcut_bakiye)
                                    margin = min(margin, mevcut_bakiye * 0.5)
                                    
                            buyukluk_usdt = margin * tavsiye_kaldirac
                            
                            # V29: Slippage Guard (Market Impact Simulator)
                            if getattr(cfg, "SLIPPAGE_GUARD_ENABLED", False) and sinyal in ["LONG", "SHORT"]:
                                try:
                                    ob_depth = getattr(cfg, "SLIPPAGE_OB_DEPTH", 5)
                                    max_impact = getattr(cfg, "SLIPPAGE_MAX_IMPACT_PCT", 10.0)
                                    ob = exchange.fetch_order_book(secilen_sembol, limit=ob_depth)
                                    if ob:
                                        if sinyal == "LONG":
                                            kademe_toplam = sum(float(ask[1]) * float(ask[0]) for ask in ob.get("asks", [])[:ob_depth])
                                        else:
                                            kademe_toplam = sum(float(bid[1]) * float(bid[0]) for bid in ob.get("bids", [])[:ob_depth])
                                        
                                        if kademe_toplam > 0:
                                            etki_pct = (buyukluk_usdt / kademe_toplam) * 100
                                            if etki_pct > max_impact:
                                                log_ekle(f"🛡️ SLIPPAGE GUARD: {secilen_sembol} işlem (${buyukluk_usdt:,.0f}) ilk {ob_depth} kademe likiditesinin (${kademe_toplam:,.0f}) %{etki_pct:.1f}'ini kaydırır. Max: %{max_impact}. İŞLEM ENGELLENDİ.", state)
                                                sinyal = "BEKLE"
                                            else:
                                                log_ekle(f"✅ SLIPPAGE OK: Etki %{etki_pct:.1f} < Max %{max_impact} (Derinlik: ${kademe_toplam:,.0f})", state)
                                except Exception:
                                    pass  # Slippage guard hatası işlemi engellemez
                            
                            # V31 FIX: Slippage Guard veya diğer veto'lar sinyal='BEKLE' yaptıysa
                            # hayalet pozisyon açılmasını engelle — bu iterasyonu atla
                            if sinyal == "BEKLE":
                                log_ekle(f"🛡️ GÜVENLIK KAPISI: {secilen_sembol} sinyal BEKLE'ye döndü, pozisyon açılmayacak.", state)
                                continue
                            
                            # V26/V28 (Eski): Dinamik Pozisyon Boyutu — güven skoruna göre limit değişir
                            # V29: Confidence-Based Sizing aktifse bu blok çalışmaz (margin zaten hesaplandı)
                            if not getattr(cfg, "CONFIDENCE_BASED_SIZING", False):
                                _guven = karar_paketi.get("guven_skoru", 0)
                                if _guven >= 95:
                                    max_poz_pct = 0.30
                                elif _guven >= 85:
                                    max_poz_pct = 0.20
                                else:
                                    max_poz_pct = 0.15
                                
                                toplam_equity_v26 = state["bakiye"] + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                                max_buyukluk = toplam_equity_v26 * max_poz_pct
                                if buyukluk_usdt > max_buyukluk:
                                    log_ekle(f"🛡️ V26 DİNAMİK POZ: Güven %{_guven:.0f} → Limit %{max_poz_pct*100:.0f}. ${buyukluk_usdt:.0f} > Max ${max_buyukluk:.0f}. Sınırlandırılıyor.", state)
                                    buyukluk_usdt = max_buyukluk
                                    margin = buyukluk_usdt / tavsiye_kaldirac if tavsiye_kaldirac > 0 else margin

                            # v10: Challenge açılış komisyonu
                            if is_challenge:
                                komisyon_oran = getattr(cfg, "CHALLENGE_COMMISSION_RATE", 0.001)
                                acilis_komisyon = buyukluk_usdt * komisyon_oran
                                ch_dt = state.get("challenge_session", {})
                                if isinstance(ch_dt, dict) and ch_dt.get("aktif"):
                                    ch_dt["bakiye"] = ch_dt.get("bakiye", 10.0) - margin - acilis_komisyon
                                    state["challenge_session"] = ch_dt
                                    log_ekle(f"🚀 CH AÇ: Margin ${margin:.4f} + Kom ${acilis_komisyon:.4f} düşüldü. CH Bakiye: ${ch_dt['bakiye']:.4f}", state)

                            # v6: ATR tabanlı dinamik stop-loss hesapla
                            try:
                                dsl_fiyat = dinamik_stop_loss_hesapla(exchange, secilen_sembol, sinyal, fiyat, tavsiye_kaldirac)
                            except (ccxt.BaseError, sqlite3.Error, Exception):
                                dsl_fiyat = likidasyon_hesapla(sinyal, fiyat, tavsiye_kaldirac)

                            tid = trade_id_olustur()
                            yeni_poz = {
                                "trade_id": tid,
                                "sembol": secilen_sembol,
                                "pozisyon": sinyal,
                                "coin_miktar": buyukluk_usdt,
                                "giris_fiyati": fiyat,
                                "likidasyon_fiyati": likidasyon_hesapla(sinyal, fiyat, tavsiye_kaldirac),
                                "dinamik_sl_fiyat": dsl_fiyat,  # v6: ATR-based dynamic SL
                                "islem_margin": margin,
                                "islem_kaldirac": tavsiye_kaldirac,
                                "kademeli_tp_yapildi": False,
                                "ts_aktif": False,
                                "trailing_stop_fiyat": 0.0,
                                "acilis_zamani": time.time(),
                                "giris_nedeni": karar_paketi.get("dusunce", "")[:120],
                                "beklenen_hedef": karar_paketi.get("expected_growth", 0.0)
                            }
                            state["aktif_pozisyonlar"][tid] = yeni_poz
                            state["bakiye"] -= margin
                            
                            # Immediate Save: Açılan pozisyon sonrasında anında atomik kaydet
                            try:
                                temiz_save = {}
                                for save_k, save_v in state.items():
                                    if isinstance(save_v, (str, int, float, bool, list, dict, type(None))):
                                        temiz_save[save_k] = save_v
                                ps.state_kaydet(temiz_save)
                            except Exception:
                                pass

                            sl_mesafe_pct = abs(fiyat - dsl_fiyat) / fiyat * 100 if fiyat > 0 else 0
                            # V31 FIX: Sadece gerçek LONG/SHORT sinyalleri kaydet, BEKLE asla geçmişe yazılmaz
                            if sinyal in ["LONG", "SHORT"]:
                                state["islem_gecmisi"].append({
                                    "zaman": zaman, "sembol": secilen_sembol, "sinyal": f"🟢 AÇ: {sinyal}",
                                    "fiyat": round(fiyat, 4), "kaldirac": f"{tavsiye_kaldirac}x", "poz_buyukluk": round(buyukluk_usdt, 2),
                                    "bakiye_usdt": round(state["bakiye"] + margin, 2), "kar_zarar": "—", "ai_notu": karar_paketi.get("dusunce", "")
                                })
                            log_ekle(f"💰 {tavsiye_kaldirac}x {sinyal} POZİSYON AÇILDI: {secilen_sembol}. Giriş: {fiyat:.4f} | Dinamik SL: ${dsl_fiyat:.4f} (%{sl_mesafe_pct:.1f})", state, is_breakout)
                            # V23: Detaylı Telegram açılış bildirimi
                            threading.Thread(
                                target=send_telegram_msg,
                                args=(_tg_trade_acilis_mesaji(
                                    secilen_sembol, sinyal, tavsiye_kaldirac, fiyat, margin, tid
                                ),),
                                daemon=True,
                            ).start()
                        else:
                            log_ekle(f"🛡️ {secilen_sembol} Fırsatı Boş Geçildi: Global Risk Limiti Dolu.", state)

                    elif sinyal == "KAPAT" and sembol_acik_mi(state.get("aktif_pozisyonlar", {}), secilen_sembol):
                        kapat_tid = sembol_icin_trade_id_bul(state.get("aktif_pozisyonlar", {}), secilen_sembol)
                        if kapat_tid:
                            islem_kapat_with_retry(state, kapat_tid, fiyat, karar_paketi.get("dusunce", ""), exchange)

            # V23: Yüksek güvenli fırsat bildirimi (her tarama döngüsü sonunda)
            try:
                with lock:
                    taranan = state.get("taranan_coinler", [])
                yuksek_guven = [
                    c for c in taranan
                    if isinstance(c, dict)
                    and float(c.get("guven_skoru", c.get("skor", 0)) or 0) >= 85
                ]
                if yuksek_guven:
                    threading.Thread(
                        target=send_telegram_msg,
                        args=(_tg_firsat_mesaji(yuksek_guven),),
                        daemon=True,
                    ).start()
            except Exception:
                pass

            # Hedef bakiye kontrolü (for döngüsü bitti, while döngüsü içinde)
            with lock:
                if state.get("pik_bakiye", 0) >= state.get("hedef_bakiye", 100):
                    if state.get("mod") == "🚀 94-Day Challenge":
                        pass
                    elif state.get("mod", "") == "💎 Ultra-Scalper":
                        if not state.get("scalper_hedef_loglandi", False):
                            log_ekle("💎 Günlük Hedef Aşıldı - İşlemlere Devam Ediliyor (Ultra-Scalper)", state)
                            state["scalper_hedef_loglandi"] = True
                    else:
                        import data_logger
                        baslangic_zaman_ts = state.get("baslangic_zamani", 0)
                        if baslangic_zaman_ts > 0:
                            gercek_pnl = data_logger.gercek_pnl_getir(baslangic_zaman_ts)
                            hedef_farki = state.get("hedef_bakiye", 100) - state.get("gun_baslangic_bakiye", 0)
                            if (hedef_farki > 0 and gercek_pnl >= hedef_farki * 0.95) or (gercek_pnl >= hedef_farki and hedef_farki > 0):
                                state["bot_durumu"] = "🎯 Hedefi Ulaştı!"
                                state["bot_calisiyor"] = False
                                log_ekle(f"🏆 HEDEF ULAŞILDI! (Gerçek PNL: ${gercek_pnl:.2f}) Bot durduruluyor.", state)
                                islem_gecmisi_kaydet(state.get("islem_gecmisi", []))
                                dur_sinyali.set()
                            else:
                                state["pik_bakiye"] = max(state.get("bakiye", 0), state.get("hedef_bakiye", 100) - 2.0)
                        else:
                            state["bot_durumu"] = "🎯 Hedefi Ulaştı!"
                            state["bot_calisiyor"] = False
                            log_ekle("🏆 HEDEF ULAŞILDI! Bot durduruluyor.", state)
                            islem_gecmisi_kaydet(state.get("islem_gecmisi", []))
                            dur_sinyali.set()


            # Persistent State: Her 60 saniyede bir veya bakiye değiştiğinde (Atomic Save) kaydet
            guncel_bakiye = state.get("bakiye", 0.0)
            bakiye_degisti_mi = abs(guncel_bakiye - son_kayit_bakiye) > 0.01

            if bakiye_degisti_mi or (time.time() - son_kayit_zamani >= 60):
                try:
                    temiz = {}
                    with lock:
                        for k, v in state.items():
                            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                                temiz[k] = v
                    ps.state_kaydet(temiz)
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    pass
                son_kayit_zamani = time.time()
                son_kayit_bakiye = guncel_bakiye

            # --- v11: ZORUNLU COOLING + GC ---
            _dongü_sayaci += 1
            cooling_sn = getattr(cfg, "COOLING_SLEEP_SECONDS", 10)
            
            # v24/V28: Dinamik Soğuma (Order Flow limit uyarısı / Weight-Aware Throttling)
            if state.get("limit_uyari_kritik"):
                cooling_sn = max(cooling_sn * 2, 30)  # V28: Kritik sınırda süreyi 2 katına çıkar (min 30s)
                with lock:
                    state["limit_uyari_kritik"] = False
                    state["limit_uyari"] = False
            elif state.get("limit_uyari"):
                cooling_sn = max(cooling_sn * 1.5, 15)  # Normal uyarıda 1.5x
                with lock:
                    state["limit_uyari"] = False  # Sonraki tur için sıfırla
            gc_interval = getattr(cfg, "GC_COLLECT_INTERVAL", 100)

            # Bellek temizliği: Her N döngüde gc.collect()
            if _dongü_sayaci % gc_interval == 0:
                gc.collect()
                with lock:
                    log_ekle(f"🧹 GC: Bellek temizlendi (döngü #{_dongü_sayaci})", state)

            # --- BEKLEME (EVENT-DRIVEN + COOLING) ---
            bekleme_suresi = cooling_sn  # v11: Zorunlu 10s dinlenme
            
            # 🚀 Evolutionary Trainer: Bekleme süresini çarpanla azalt
            if state.get("mod") == "🚀 Evolutionary Trainer":
                evo_carpan = getattr(cfg, "EVO_WAIT_MULTIPLIER", 0.30)
                bekleme_suresi = max(3, int(bekleme_suresi * evo_carpan))  # En az 3s
                
            with lock:
                state["sonraki_analiz_sn"] = bekleme_suresi

            state.get("analiz_tetikleyici", threading.Event()).clear()
            for _ in range(bekleme_suresi):
                if dur_sinyali.is_set():
                    return
                tetiklendi = state.get("analiz_tetikleyici", threading.Event()).wait(timeout=1.0)
                if tetiklendi:
                    with lock:
                        log_ekle("⚡ SIFIR GECİKME: Anlık Hacim/Fiyat Patlaması tetiklendi!", state, is_breakout=True)
                        state["sonraki_analiz_sn"] = 0
                    break
                with lock:
                    state["sonraki_analiz_sn"] -= 1

        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            err_str = str(e)
            is_auth = "Authentication" in err_str or "API-key" in err_str or "Invalid credentials" in err_str

            if is_auth:
                with lock:
                    if not state.get("auth_error_notified"):
                        log_ekle("❌ API Kimlik Doğrulama Hatası! Geçerli bakiye/veri çekilemiyor.", state)
                        state["auth_error_notified"] = True
                        state["auth_error_msg"] = err_str[:150]
                time.sleep(10)
            elif "ExchangeNotAvailable" in err_str or "NetworkError" in err_str or "RequestTimeout" in err_str:
                with lock:
                    log_ekle("🔄 Bağlantı hatası tespit edildi, exchange yeniden bağlanıyor...", state)
                if not _baglanti_kur():
                    with lock:
                        log_ekle("❌ Yeniden bağlantı başarısız. 30sn bekleniyor.", state)
                    time.sleep(30)
                else:
                    time.sleep(2)
            else:
                # Error Throttling: 30sn'de bir özet log (spam engelleme)
                if state.get("auth_error_notified"):
                    with lock:
                        state["auth_error_notified"] = False
                err_key = err_str[:50]
                _error_counts[err_key] = _error_counts.get(err_key, 0) + 1
                now = time.time()
                if now - _last_error_log_time >= 30:
                    with lock:
                        for ek, ec in _error_counts.items():
                            log_ekle(f"❌ Hata Özeti ({ec}x/30s): {ek}", state)
                    _error_counts.clear()
                    _last_error_log_time = now
                time.sleep(5)

    # Bot durdurulduğunda son kayıt
    try:
        temiz = {}
        with lock:
            for k, v in state.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    temiz[k] = v
        ps.state_kaydet(temiz)
    except (ccxt.BaseError, sqlite3.Error, Exception):
        pass


def korelasyon_rutini(state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
    """
    v9: Otonom Öz-Değerlendirme + Korelasyon Döngüsü
    - Her 5 dakikada: Korelasyon güncelleme
    - Her 24 saatte (veya gün dönümünde): ML model yeniden eğitimi + hot-reload
    """
    son_egitim_zamani = time.time()
    retrain_interval = getattr(cfg, "ML_RETRAIN_INTERVAL_HOURS", 24) * 3600

    while not dur_sinyali.is_set():
        try:
            # --- Korelasyon Güncellemesi (her 5dk) ---
            korelasyonlar = data_logger.en_iyi_korelasyonlari_getir(limit=50)
            if korelasyonlar:
                with lock:
                    state["kesin_kar_parametreleri"] = korelasyonlar
                    log_ekle(f"🧠 Derin Analiz: Geçmiş işlemlere göre Kesin Kâr güncellendi. (A.Vol: %{korelasyonlar.get('ortalama_volatilite', 0):.1f}, Hacim: %{korelasyonlar.get('ortalama_hacim_artis', 0):.0f})", state)

            # --- ML Model Eğitimi (her 24 saat VEYA 10 başarılı işlemde bir) ---
            islem_gecmisi = state.get("islem_gecmisi", [])
            basarili_sayisi = sum(1 for islem in islem_gecmisi if islem.get("pnl", 0) > 0)
            son_egitim_sayaci = state.get("son_egitim_islem_sayaci", 0)
            limit_doldu_mu = (basarili_sayisi - son_egitim_sayaci) >= 10

            if time.time() - son_egitim_zamani >= retrain_interval or limit_doldu_mu:
                with lock:
                    if limit_doldu_mu:
                        state["son_egitim_islem_sayaci"] = basarili_sayisi
                        log_ekle(f"🧠 ML RETRAIN: 10 Başarılı işlem tamamlandı. Eğitim döngüsü başlatılıyor...", state)
                    else:
                        log_ekle("🧠 ML RETRAIN: 24 saatlik eğitim döngüsü başlatılıyor...", state)

                try:
                    sonuc = train_model.run_training()
                    if sonuc.get("basarili"):
                        # Hot-reload: Yeni model ağırlıklarını yükle
                        reload_ok = ai_engine._reload_ml_model()
                        detay = sonuc.get("detay", {})
                        egitim_info = detay.get("egitim", {})
                        with lock:
                            state["son_ml_egitim"] = datetime.now(timezone.utc).isoformat()
                            state["ml_accuracy"] = egitim_info.get("accuracy", 0)
                            log_ekle(
                                f"✅ ML RETRAIN TAMAMLANDI: Accuracy %{egitim_info.get('accuracy', 0):.1f}, "
                                f"{'Model yüklendi ✓' if reload_ok else 'Yükleme bekliyor'}",
                                state, is_breakout=True
                            )
                    else:
                        with lock:
                            log_ekle(f"⚠️ ML RETRAIN BAŞARISIZ: {sonuc.get('neden', '?')}", state)
                except (ccxt.BaseError, sqlite3.Error, Exception) as e:
                    with lock:
                        log_ekle(f"❌ ML RETRAIN HATA: {str(e)[:80]}", state)

                son_egitim_zamani = time.time()

        except (ccxt.BaseError, sqlite3.Error, Exception):
            pass
        
        # 300 saniye (5 dakika) bekle
        dur_sinyali.wait(300)

# ─────────────────────────────────────────────
# Bot Worker (Singleton Manager)
# ─────────────────────────────────────────────
class BotWorker:
    """Arka plan thread'lerini yöneten singleton."""

    def __init__(self):
        self.state = GlobalBotState()
        self._ws_thread = None
        self._engine_thread = None
        self._corr_thread = None
        self.bootstrap()

    def bootstrap(self):
        """Worker başlarken (UI yüklenmeden) state dosyasını okuyup kaldığı yerden devam ettirir."""
        last_mode = ps.get_last_mode()
        self.state.set("use_real_api", last_mode)
        self.state.load_from_persistent()

        if self.is_running:
            mod_str = "Real" if last_mode else "Demo"
            print(f"🔄 [Bootstrap] Auto-resume başlatıldı: {mod_str} modu aktif")
            self.start()

    @property
    def is_running(self) -> bool:
        return self.state.get("bot_calisiyor", False)

    def start(self):
        if self.is_running and getattr(self, "_engine_thread", None) is not None:
            return

        raw = self.state.raw()
        lock = self.state.lock
        dur = raw["dur_sinyali"]

        dur.clear()
        raw["bot_calisiyor"] = True
        raw["bot_durumu"] = "Çalışıyor"
        if raw.get("baslangic_zamani", 0) == 0.0:
            raw["baslangic_zamani"] = time.time()
            
        # Immediate Save: Bot ilk başladığında anında kaydet
        self.state.save_to_persistent()

        # API Entegrasyonu
        if raw.get("use_real_api"):
            cfg.USE_REAL_API = True
            cfg.API_KEY = ps.decode_key(raw.get("api_key_enc", ""))
            cfg.SECRET_KEY = ps.decode_key(raw.get("api_secret_enc", ""))
        else:
            cfg.USE_REAL_API = False

        DataProvider().start_if_needed(raw, lock, dur)

        self._engine_thread = threading.Thread(target=bot_engine, args=(raw, lock, dur), daemon=True)
        self._engine_thread.start()

        self._corr_thread = threading.Thread(target=korelasyon_rutini, args=(raw, lock, dur), daemon=True)
        self._corr_thread.start()

        # V23: Telegram Command Listener
        self._tg_listener_thread = threading.Thread(
            target=telegram_komut_dinleyici,
            args=(raw, lock, dur),
            daemon=True,
        )
        self._tg_listener_thread.start()

    def stop(self):
        raw = self.state.raw()
        raw["dur_sinyali"].set()
        
        # v6 GRACEFUL SHUTDOWN: Tüm açık pozisyonları piyasa emriyle kapat ve PNL logla
        with self.state.lock:
            kapanacaklar = list(raw.get("aktif_pozisyonlar", {}).keys())
            fiyatlar = raw.get("guncel_fiyatlar", {})
            toplam_shutdown_pnl = 0.0
            for tid in kapanacaklar:
                poz = raw["aktif_pozisyonlar"].get(tid)
                if not poz:
                    continue
                s = poz.get("sembol", tid)
                f = fiyatlar.get(s, poz.get("giris_fiyati", 0))
                if f > 0:
                    margin = poz.get("islem_margin", 0)
                    kaldirac = poz.get("islem_kaldirac", 1)
                    pnl = pnl_hesapla(poz.get("pozisyon", "YOK"), poz.get("giris_fiyati", 0), f, margin * kaldirac, kaldirac)
                    toplam_shutdown_pnl += pnl
                    islem_kapat(raw, tid, f, "🚨 BOT DURDURULDU: Kullanıcı İsteği")
            if kapanacaklar:
                log_ekle(f"🚨 GRACEFUL SHUTDOWN: {len(kapanacaklar)} pozisyon kapatıldı. Toplam PNL: {toplam_shutdown_pnl:+.2f} USDT", raw, is_breakout=True)

        raw["bot_calisiyor"] = False
        raw["bot_durumu"] = "Durduruldu"
        self.state.save_to_persistent()

    def switch_mode(self, use_real_api: bool):
        """Demo/Real mod değiştir. Bot duruyorsa state'i yeniden yükler."""
        self.state.save_to_persistent()
        ps.set_last_mode(use_real_api)
        self.state.set("use_real_api", use_real_api)
        self.state.load_from_persistent()
