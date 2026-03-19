"""
Bot Worker — Global Singleton + Background Thread Manager
==========================================================
Streamlit UI'dan bağımsız çalışan arka plan işçisi.
@st.cache_resource ile oluşturulur, sekme kapansa bile process
yaşadığı sürece bellekte kalır ve bot 7/24 çalışmaya devam eder.
"""

import threading
import time
import csv
import asyncio
import os
from datetime import datetime, timezone

import ccxt
import ccxt.pro as ccxtpro

import ai_engine
import config as cfg
import persistent_state as ps
import data_logger


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
            loaded = ps.state_yukle(ps.STATE_FILE)
        except Exception as e:
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

    def save_to_persistent(self):
        """Disk'e serialize-safe kaydet."""
        try:
            temiz = {}
            with self._lock:
                for k, v in self._data.items():
                    if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                        temiz[k] = v
            ps.state_kaydet(temiz)
        except Exception as e:
            print(f"⚠️ save_to_persistent hata: {e}")


# ─────────────────────────────────────────────
# Yardımcı Fonksiyonlar
# ─────────────────────────────────────────────
MOD_PRESETLERI = {
    "⚡ Agresif Mod": {"risk": 1.0, "sma_kisa": 7, "sma_uzun": 25, "aralik_carpan": 0.5},
    "🌱 Soft Kar Modu": {"risk": 0.30, "sma_kisa": 14, "sma_uzun": 50, "aralik_carpan": 1.5},
    "💎 Ultra-Scalper": {"risk": 0.10, "sma_kisa": 3, "sma_uzun": 10, "aralik_carpan": 0.05},
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


def aktif_margin_toplami(pozisyonlar: dict) -> float:
    return sum(p.get("islem_margin", 0) for p in pozisyonlar.values())


def pnl_hesapla_coklu(pozlar, guncel_fiyatlar: dict) -> float:
    toplam_pnl = 0.0
    for sembol, poz in pozlar.items():
        anlik = guncel_fiyatlar.get(sembol, poz.get("giris_fiyati", 0))
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
    except Exception:
        pass


def islem_kapat(state, sembol, fiyat, neden, is_breakout=False, is_liq=False):
    poz = state["aktif_pozisyonlar"].get(sembol)
    if not poz:
        return

    eski_poz = poz["pozisyon"]
    margin = poz["islem_margin"]
    kaldirac = poz["islem_kaldirac"]
    aktif_pnl = pnl_hesapla(eski_poz, poz["giris_fiyati"], fiyat, margin * kaldirac, kaldirac)
    poz_giris = poz["giris_fiyati"]  # v7: SQLite için sakla

    reel_getiri = margin + aktif_pnl
    state["bakiye"] += reel_getiri

    del state["aktif_pozisyonlar"][sembol]

    kz_str = f"{aktif_pnl:+.2f} USDT"
    icon = "☠️" if is_liq else "🛡️" if "TS" in neden or "SL" in neden else "🔴"

    zaman = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    state["islem_gecmisi"].append({
        "zaman": zaman, "sembol": sembol, "sinyal": f"{icon} KAPAT: {eski_poz}",
        "fiyat": round(fiyat, 4), "kaldirac": f"{kaldirac}x", "poz_buyukluk": 0,
        "bakiye_usdt": round(state["bakiye"], 2), "kar_zarar": kz_str, "ai_notu": neden
    })
    log_ekle(f"{icon} POZİSYON KAPATILDI: {sembol} {eski_poz}. PNL: {kz_str}", state, is_breakout, is_liq)

    # v7.1: Opsiyonel Martingale Takibi
    if state.get("martingale_aktif", False):
        if aktif_pnl < 0:
            state["martingale_ardisik_kayip"] = state.get("martingale_ardisik_kayip", 0) + 1
            kayip_n = state["martingale_ardisik_kayip"]
            if kayip_n >= 3:
                state["martingale_carpan"] = 1.0  # 3+ kayıp: geri çekil
                log_ekle(f"⚠️ MARTINGALE DURDURULDU: {kayip_n} ardışık kayıp. Çarpan 1.0x'e düştü.", state)
            else:
                state["martingale_carpan"] = min(2 ** kayip_n, 4.0)
                log_ekle(f"🎲 MARTINGALE: {kayip_n}. kayıp. Sonraki margin çarpanı: {state['martingale_carpan']:.0f}x", state)
        else:
            if state.get("martingale_ardisik_kayip", 0) > 0:
                log_ekle(f"✅ MARTINGALE RESET: Kârlı işlem. Çarpan 1.0x'e döndü.", state)
            state["martingale_ardisik_kayip"] = 0
            state["martingale_carpan"] = 1.0
    else:
        state["martingale_ardisik_kayip"] = 0
        state["martingale_carpan"] = 1.0

    # v7: SQLite'a işlem kapanışı kaydet
    try:
        pnl_pct_val = (aktif_pnl / margin * 100) if margin > 0 else 0
        data_logger.islem_kaydet(
            sembol=sembol, tip=eski_poz, giris_fiyati=poz_giris,
            cikis_fiyati=fiyat, pnl=aktif_pnl, pnl_pct=pnl_pct_val,
            kaldirac=kaldirac, margin=margin, neden=neden
        )
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
    except Exception:
        return likidasyon_hesapla(pozisyon_tipi, giris_fiyati, kaldirac)


def islem_kapat_with_retry(state, sembol, fiyat, neden, exchange=None, max_retry=3, slippage_tolerance=0.005, is_breakout=False, is_liq=False):
    """v6: Slippage kontrollü kapama. Fiyat kayarsa retry yapar."""
    for attempt in range(max_retry):
        try:
            guncel_fiyat = fiyat
            if exchange is not None and attempt > 0:
                try:
                    ticker = exchange.fetch_ticker(sembol)
                    if isinstance(ticker, dict) and ticker.get("last"):
                        guncel_fiyat = float(ticker["last"])
                except Exception:
                    pass
            if attempt > 0 and abs(guncel_fiyat - fiyat) / max(fiyat, 0.0001) > slippage_tolerance:
                log_ekle(f"⚠️ SLIPPAGE #{attempt}: {sembol} fiyat kaydı ${fiyat:.4f}→${guncel_fiyat:.4f}. Yeniden deneniyor...", state)
            islem_kapat(state, sembol, guncel_fiyat, neden, is_breakout, is_liq)
            return True
        except Exception as e:
            if attempt < max_retry - 1:
                log_ekle(f"⚠️ RETRY #{attempt+1}: {sembol} kapama hatası: {str(e)[:60]}", state)
                time.sleep(0.5)
            else:
                log_ekle(f"❌ KAPAMA BAŞARISIZ: {sembol} {max_retry} deneme sonrası kapanamadı!", state)
                return False
    return False


# ─────────────────────────────────────────────
# WebSocket Fiyat Dinleyici
# ─────────────────────────────────────────────
def ws_fiyat_dinleyici(state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
    async def dinle():
        try:
            exchange = getattr(ccxtpro, state["exchange_adi"])({"enableRateLimit": True})
        except Exception:
            return

        guncel_fiyatlar = {}

        while not dur_sinyali.is_set():
            try:
                sembol = state.get("aktif_sembol")
                dinlenecekler = list(state.get("aktif_pozisyonlar", {}).keys())
                if sembol and sembol != "Bekleniyor..." and sembol not in dinlenecekler:
                    dinlenecekler.insert(0, sembol)
                
                if dinlenecekler:
                    poz_tasks = [asyncio.create_task(exchange.watch_ticker(s)) for s in dinlenecekler]
                    try:
                        res = await asyncio.wait_for(asyncio.gather(*poz_tasks, return_exceptions=True), timeout=5.0)
                        
                        for i, s in enumerate(dinlenecekler):
                            tck = res[i]
                            if isinstance(tck, dict):
                                guncel_fiyatlar[s] = tck.get("last", guncel_fiyatlar.get(s, 0))
                                if s == sembol:
                                    with lock:
                                        f = tck.get("last", state.get("fiyat", 0))
                                        state["fiyat"] = f
                                        if tck.get("percentage"): state["degisim_24s"] = tck.get("percentage")
                                        if tck.get("quoteVolume"): state["hacim_24s"] = tck.get("quoteVolume")
                                        
                                        sf = state.get("son_fiyat_tick", 0)
                                        if sf > 0 and f != sf:
                                            # v8: tick değişimi
                                            degisim_tick = abs((f - sf) / sf) * 100
                                            if degisim_tick >= 0.3:
                                                state["analiz_tetikleyici"].set()
                                        state["son_fiyat_tick"] = f

                        with lock:
                            state["guncel_fiyatlar"] = guncel_fiyatlar.copy()

                            toplam_margin = aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                            top_pnl_anlik = pnl_hesapla_coklu(state.get("aktif_pozisyonlar", {}), guncel_fiyatlar)
                            anlik_varlik = state["bakiye"] + toplam_margin + top_pnl_anlik
                            max_izin_verilir_risk = anlik_varlik * 0.20

                            if top_pnl_anlik < 0 and abs(top_pnl_anlik) >= max_izin_verilir_risk:
                                acik_syms = list(state.get("aktif_pozisyonlar", {}).keys())
                                for s in acik_syms:
                                    f_s = guncel_fiyatlar.get(s, state["aktif_pozisyonlar"][s]["giris_fiyati"])
                                    islem_kapat(state, s, f_s, "🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam zarar %20'yi aştı.")
                                log_ekle("🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam Bakiye Korundu.", state, is_breakout=True)

                            kapanacak_semboller = []
                            for p_sembol, poz in list(state.get("aktif_pozisyonlar", {}).items()):
                                f_s = guncel_fiyatlar.get(p_sembol, poz.get("giris_fiyati", 0))
                                if f_s == 0:
                                    continue

                                is_long = poz.get("pozisyon") == "LONG"
                                is_short = poz.get("pozisyon") == "SHORT"
                                liq_price = poz.get("likidasyon_fiyati", 0)

                                aktif_pnl_val = pnl_hesapla(poz.get("pozisyon", "YOK"), poz.get("giris_fiyati", 0), f_s,
                                                             poz.get("islem_margin", 0) * poz.get("islem_kaldirac", 1),
                                                             poz.get("islem_kaldirac", 1))
                                pnl_pct = (aktif_pnl_val / poz.get("islem_margin", 1)) * 100 if poz.get("islem_margin", 0) > 0 else 0

                                if (is_long and f_s <= liq_price) or (is_short and f_s >= liq_price):
                                    islem_kapat(state, p_sembol, f_s, "Liquidation", is_liq=True)
                                    if state["bakiye"] <= 0:
                                        state["bot_durumu"] = "💀 İflas"
                                        state["bot_calisiyor"] = False
                                        dur_sinyali.set()
                                # v6: ATR Tabanlı Dinamik Stop-Loss Kontrolü
                                elif poz.get("dinamik_sl_fiyat", 0) > 0:
                                    dsl = poz["dinamik_sl_fiyat"]
                                    dsl_hit = (is_long and f_s <= dsl) or (is_short and f_s >= dsl)
                                    if dsl_hit:
                                        islem_kapat(state, p_sembol, f_s, f"🛡️ DİNAMİK SL TETİKLENDİ: ATR Stop ${dsl:.4f}")
                                else:
                                    is_scalper = state.get("mod") == "💎 Ultra-Scalper"

                                    # v8: 💎 Ultra-Scalper: Kesintisiz %1.5 ROE TP, %0.5 SL, 5dk timeout
                                    if is_scalper:
                                        if pnl_pct >= 1.5:
                                            kapanacak_semboller.append(p_sembol)
                                            poz["kapat_nedeni"] = f"💎 SCALPER TP: %{pnl_pct:.1f} ROE Kâr yakalandı!"
                                            log_ekle(f"💎 SCALPER TP: {p_sembol} %{pnl_pct:.1f} ROE → Kâr alındı, yeni fırsat aranıyor.", state, is_breakout=True)
                                        elif pnl_pct <= -0.5:
                                            kapanacak_semboller.append(p_sembol)
                                            poz["kapat_nedeni"] = f"💎 SCALPER SL: %{pnl_pct:.1f} ROE zararla durduruldu."
                                            log_ekle(f"💎 SCALPER SL: {p_sembol} %{pnl_pct:.1f} ROE → Pozisyon kapatıldı.", state)
                                        elif (time.time() - poz.get("acilis_zamani", 0)) > 300:  # 5 dakika
                                            kapanacak_semboller.append(p_sembol)
                                            poz["kapat_nedeni"] = f"💎 SCALPER TIMEOUT: 5 dakika doldu, hareket yetersiz."
                                            log_ekle(f"💎 SCALPER TIMEOUT: {p_sembol} 5dk süre doldu. Kapatılıyor.", state)
                                    elif is_scalper and pnl_pct >= 0.5 and not poz.get("ts_aktif"):
                                        # Scalper trailing stop: giriş fiyatının %0.3 yakınına çek
                                        poz["ts_aktif"] = True
                                        if is_long:
                                            poz["trailing_stop_fiyat"] = poz["giris_fiyati"] * 0.997
                                        else:
                                            poz["trailing_stop_fiyat"] = poz["giris_fiyati"] * 1.003
                                        log_ekle(f"💎 SCALPER TS: {p_sembol} iz süren stop girişe çok yakın bağlandı.", state)
                                    elif pnl_pct >= 10.0 and not poz.get("kademeli_tp_yapildi", False):
                                        poz["kademeli_tp_yapildi"] = True
                                        real_pnl = aktif_pnl_val / 2
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

                                    if poz.get("ts_aktif"):
                                        ts_hit = False
                                        if is_long:
                                            if pnl_pct >= 5.0 and not poz.get("kademeli_tp_yapildi", False):
                                                yeni_ts = f_s * 0.98
                                                if yeni_ts > poz.get("trailing_stop_fiyat", 0):
                                                    poz["trailing_stop_fiyat"] = yeni_ts
                                            if f_s <= poz.get("trailing_stop_fiyat", 0):
                                                ts_hit = True
                                        else:
                                            if pnl_pct >= 5.0 and not poz.get("kademeli_tp_yapildi", False):
                                                yeni_ts = f_s * 1.02
                                                if yeni_ts < poz.get("trailing_stop_fiyat", float('inf')):
                                                    poz["trailing_stop_fiyat"] = yeni_ts
                                            if f_s >= poz.get("trailing_stop_fiyat", float('inf')):
                                                ts_hit = True
                                        if ts_hit:
                                            kapanacak_semboller.append(p_sembol)

                                    gecen_dk = (time.time() - poz.get("acilis_zamani", time.time())) / 60.0
                                    zaman_limit = 5.0 if state.get("mod") == "💎 Ultra-Scalper" else 60.0
                                    pnl_esik = 0.3 if state.get("mod") == "💎 Ultra-Scalper" else 0.5
                                    if gecen_dk >= zaman_limit and abs(pnl_pct) < pnl_esik:
                                        if p_sembol not in kapanacak_semboller:
                                            kapanacak_semboller.append(p_sembol)
                                            poz["kapat_nedeni"] = f"Zaman Maliyeti: {gecen_dk:.0f}dk'da yetersiz hareket" if state.get("mod") == "💎 Ultra-Scalper" else "Zaman Maliyeti: Yetersiz Volatilite"

                            for ks in kapanacak_semboller:
                                f_ks = guncel_fiyatlar.get(ks, state["aktif_pozisyonlar"].get(ks, {}).get("giris_fiyati", 0))
                                rsn = state["aktif_pozisyonlar"].get(ks, {}).get("kapat_nedeni", "🛡️ TS KAPAT - İz Süren Stop")
                                islem_kapat(state, ks, f_ks, rsn)

                            anlik_v = state["bakiye"] + aktif_margin_toplami(state.get("aktif_pozisyonlar", {})) + pnl_hesapla_coklu(state.get("aktif_pozisyonlar", {}), guncel_fiyatlar)
                            if anlik_v > state.get("pik_bakiye", 0):
                                state["pik_bakiye"] = anlik_v
                            elif state.get("pik_bakiye", 0) > 0:
                                dd = (state["pik_bakiye"] - anlik_v) / state["pik_bakiye"] * 100
                                if dd > state.get("max_drawdown", 0):
                                    state["max_drawdown"] = dd

                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(1)
        try:
            await exchange.close()
        except Exception:
            pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dinle())


# ─────────────────────────────────────────────
# Bot Engine (Ana Karar Döngüsü)
# ─────────────────────────────────────────────
def bot_engine(state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
    try:
        exchange = getattr(ccxt, state.get("exchange_adi", "binance"))({"enableRateLimit": True})
    except Exception as e:
        with lock:
            log_ekle(f"❌ Exchange bağlantı hatası: {e}", state)
        return

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

                # Fiyat Senkronizasyonu: Her zaman Binance'den en güncel fiyatı çek
                try:
                    ticker = exchange.fetch_ticker(secilen_sembol)
                    if isinstance(ticker, dict):
                        fiyat = ticker.get("last", 0) or (secilen_pazar.get("fiyat", 0) if secilen_pazar else 0)
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

                # --- Grid Analizi ---
                grid_trade_yapildi = False
                karar_override = None
                try:
                    df_grid = ai_engine.mum_verisi_cek(exchange, secilen_sembol, "1h", limit=30)
                    grid_bilgi = ai_engine.grid_destek_direnc(df_grid)
                    if grid_bilgi.get("grid_uygun") and secilen_sembol not in state.get("aktif_pozisyonlar", {}):
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
                except Exception:
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
                    except Exception:
                        pass

                    # v8: 24 Saatlik Akıllı Döngü Reset Kontrolü
                    baslangic_z = state.get("baslangic_zamani", 0)
                    if baslangic_z > 0 and (time.time() - baslangic_z) >= 86400:  # 24 saat
                        # Tüm pozisyonları kapat
                        kapanacak_24 = list(state.get("aktif_pozisyonlar", {}).keys())
                        fiyatlar_24 = state.get("guncel_fiyatlar", {})
                        for s24 in kapanacak_24:
                            p24 = state["aktif_pozisyonlar"].get(s24)
                            if p24:
                                f24 = fiyatlar_24.get(s24, p24.get("giris_fiyati", 0))
                                if f24 > 0:
                                    islem_kapat(state, s24, f24, "🔄 24S DÖNGÜ: Otomatik kapama")
                        # Botu Berserker'dan çıkar, tamamen yenile
                        state["gun_baslangic_bakiye"] = state["bakiye"]
                        state["baslangic_bakiye"] = state["bakiye"]
                        state["pik_bakiye"] = state["bakiye"]
                        state["gunluk_pik_kar"] = 0.0
                        state["baslangic_zamani"] = time.time()
                        state["is_breakout"] = False
                        state["martingale_ardisik_kayip"] = 0
                        state["martingale_carpan"] = 1.0
                        state["toplam_islem_sayisi"] = 0
                        state["ai_dusunce_gunlugu"] = []
                        if "islem_gecmisi" in state:
                            state["islem_gecmisi"].clear()
                        state["bot_durumu"] = "Çalışıyor"
                        log_ekle(f"🔄 24S DÖNGÜ TAMAMLANDI: Yeni anapara ${state['bakiye']:.2f}. Tüm istatistikler sıfırlandı, yeni gün başlıyor!", state, is_breakout=True)

                if dur_sinyali.is_set():
                    break

                # --- YAPAY ZEKA TAHMİNİ ---
                with lock:
                    poz_durumu = state.get("aktif_pozisyonlar", {}).get(secilen_sembol, {}).get("pozisyon", "YOK")

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
                if not pozisyonu_kapat:
                    if not isinstance(secilen_pazar, dict) or not secilen_pazar:
                        karar_paketi = {"karar": "BEKLE", "dusunce": "Pazar verisi alınamadı, bekleniyor.", "aralik_sn": 30, "guven_skoru": 0, "expected_growth": 0, "tavsiye_kaldirac": 10, "tavsiye_oran": 0.10, "ozet": "Veri yok"}
                    elif state.get("ai_modu") == "OpenAI LLM" and state.get("openai_key"):
                        karar_paketi = ai_engine.llm_karar(secilen_sembol, secilen_pazar, secilen_sma, state["openai_key"], poz_durumu, btc_trend, fonlama, zaman_baski_carpani)
                        skor = ai_engine.kompozit_skor_hesapla(secilen_pazar, secilen_sma)
                        karar_paketi = ai_engine.mock_ai_karar(secilen_sembol, secilen_pazar, skor, poz_durumu, btc_trend, fonlama, zaman_baski_carpani, mod=state.get("mod", ""))

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
                    # Eğer bakiye aniden %100 veya daha fazla fırlarsa, bu manuel eklemedir, kilitlenmeyi önle.
                    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", cfg.INITIAL_BALANCE))
                    mevcut_bakiye = state.get("bakiye", gun_baslangic) + aktif_margin_toplami(state.get("aktif_pozisyonlar", {}))
                
                    if gun_baslangic > 0 and ((mevcut_bakiye - gun_baslangic) / gun_baslangic) * 100 >= 100.0:
                        with lock:
                            state["gun_baslangic_bakiye"] = mevcut_bakiye
                            log_ekle(f"🔄 Bakiye Senkronizasyonu: Manuel ekleme tespit edildi. Yeni Gün Başlangıç: ${mevcut_bakiye:.2f}", state)

                    # v8: Dinamik Kâr Kilidi & Zarar Kurtarma (Recovery Mode)
                    gunluk_kar = gunluk_kar_hesapla(state)
                    pik_kar = state.get("gunluk_pik_kar", 0.0)
                    if gunluk_kar > pik_kar:
                        state["gunluk_pik_kar"] = gunluk_kar
                        pik_kar = gunluk_kar

                    hedef_pct = getattr(cfg, "DAILY_TARGET_PCT", 10.0)
                    if pik_kar >= hedef_pct:
                        kilit_seviyesi = hedef_pct * getattr(cfg, "PROFIT_LOCK_RATIO", 0.8)
                        if gunluk_kar < kilit_seviyesi:
                            karar_paketi["karar"] = "BEKLE"
                            karar_paketi["dusunce"] = f"🛡️ GÜVENLİ MOD: Kâr kilidi (%{kilit_seviyesi:.1f}) tetiklendi! Korunuyor."
                            state["bot_durumu"] = "🛡️ Güvenli Mod"
                        else:
                            # HFT devam! Durdurmak yok.
                            pass

                    loss_stop = getattr(cfg, "DAILY_LOSS_STOP", -7.5)
                    if gunluk_kar <= loss_stop:
                        state["bot_durumu"] = "🩺 Kurtarma Modu"
                        # Sadece çok güvenli sinyalleri kabul et
                        if karar_paketi["karar"] in ["LONG", "SHORT"] and karar_paketi.get("guven_skoru", 0) < getattr(cfg, "RECOVERY_CONFIDENCE_THRESHOLD", 90):
                            karar_paketi["karar"] = "BEKLE"
                            karar_paketi["dusunce"] = f"🩺 KURTARMA MODU: SKOR {karar_paketi.get('guven_skoru',0):.1f} YETERSİZ (>90 Gerek). İptal."

                    # DCA
                    if secilen_sembol in state.get("aktif_pozisyonlar", {}):
                        poz = state["aktif_pozisyonlar"][secilen_sembol]
                        dca = ai_engine.dca_hesapla(poz, fiyat, state.get("bakiye", 0))
                        if dca.get("uygun"):
                            with lock:
                                log_ekle(f"💱 DCA ÖNERİ: {secilen_sembol} - {dca.get('neden', '')}", state)
                                ekleme = dca.get("ekleme_margin", 0)
                                if ekleme <= state.get("bakiye", 0):
                                    state["aktif_pozisyonlar"][secilen_sembol]["islem_margin"] += ekleme
                                    state["aktif_pozisyonlar"][secilen_sembol]["giris_fiyati"] = dca.get("yeni_ortalama", poz.get("giris_fiyati", 0))
                                    state["aktif_pozisyonlar"][secilen_sembol]["dca_sayisi"] = dca.get("dca_sayisi", 1)
                                    state["bakiye"] -= ekleme
                                    log_ekle(f"✅ DCA UYGULANDI: ${ekleme:.2f} eklendi.", state)
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
                    if sinyal in ["LONG", "SHORT"] and secilen_sembol not in state.get("aktif_pozisyonlar", {}):
                        mtf_k = mtf.get("konsensus", "KARARSIZ") if isinstance(mtf, dict) else "KARARSIZ"
                        mtf_gecti = False
                        if sinyal == "LONG" and mtf_k in ["GÜÇLÜ AL", "ZAYIF AL"]:
                            mtf_gecti = True
                        elif sinyal == "SHORT" and mtf_k in ["GÜÇLÜ SAT", "ZAYIF SAT"]:
                            mtf_gecti = True
                        
                        if not mtf_gecti:
                            log_ekle(f"🔬 MTF GATE REDDETTİ: {secilen_sembol} {sinyal} kararı MTF ({mtf_k}) ile çelişiyor. İşlem iptal.", state)
                            sinyal = "BEKLE"  # MTF onaylamıyor, işlem iptal
                        else:
                            log_ekle(f"✅ MTF GATE ONAYLADI: {secilen_sembol} {sinyal} → MTF: {mtf_k}", state)

                    if sinyal in ["LONG", "SHORT"] and secilen_sembol not in state.get("aktif_pozisyonlar", {}):
                        tavsiye_kaldirac = karar_paketi.get("tavsiye_kaldirac", 10)
                        tavsiye_oran = karar_paketi.get("tavsiye_oran", 0.10)

                        # v7: USDT.D LONG baskılama
                        if sinyal == "LONG" and state.get("usdt_d_trend") == "YUKARI":
                            tavsiye_oran = tavsiye_oran * 0.7  # %30 azalt
                            tavsiye_kaldirac = max(1, int(tavsiye_kaldirac * 0.7))
                            log_ekle(f"📊 USDT.D BASKILAMA: LONG oran/kaldıraç %30 azaltıldı. Oran: {tavsiye_oran:.2f}, Kaldıraç: {tavsiye_kaldirac}x", state)

                        # v8: Recovery Mode & Martingale çarpanı uygula
                        mart_carpan = state.get("martingale_carpan", 1.0)
                        if state.get("bot_durumu", "") == "🩺 Kurtarma Modu":
                            # Kurtarma modunda %90+ güvenliyiz, batan kasayı toparlamak için riski 2'ye katla
                            mart_carpan = max(2.0, mart_carpan)

                        risk_limit = 0.40 if zaman_baski_carpani >= 4.0 else 0.30 if zaman_baski_carpani >= 3.0 else 0.20
                        kullanilabilir_max = min(tavsiye_oran, risk_limit - (risk_pct / 100.0))
                        if kullanilabilir_max > 0:
                            margin = state["bakiye"] * kullanilabilir_max * mart_carpan
                            # v7: Martingale güvenlik limiti: bakiyenin %50'sini geçemez
                            margin = min(margin, state["bakiye"] * 0.5)
                            buyukluk_usdt = margin * tavsiye_kaldirac

                            # v6: ATR tabanlı dinamik stop-loss hesapla
                            try:
                                dsl_fiyat = dinamik_stop_loss_hesapla(exchange, secilen_sembol, sinyal, fiyat, tavsiye_kaldirac)
                            except Exception:
                                dsl_fiyat = likidasyon_hesapla(sinyal, fiyat, tavsiye_kaldirac)

                            yeni_poz = {
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
                            state["aktif_pozisyonlar"][secilen_sembol] = yeni_poz
                            state["bakiye"] -= margin

                            sl_mesafe_pct = abs(fiyat - dsl_fiyat) / fiyat * 100 if fiyat > 0 else 0
                            state["islem_gecmisi"].append({
                                "zaman": zaman, "sembol": secilen_sembol, "sinyal": f"🟢 AÇ: {sinyal}",
                                "fiyat": round(fiyat, 4), "kaldirac": f"{tavsiye_kaldirac}x", "poz_buyukluk": round(buyukluk_usdt, 2),
                                "bakiye_usdt": round(state["bakiye"] + margin, 2), "kar_zarar": "—", "ai_notu": karar_paketi.get("dusunce", "")
                            })
                            log_ekle(f"💰 {tavsiye_kaldirac}x {sinyal} POZİSYON AÇILDI: {secilen_sembol}. Giriş: {fiyat:.4f} | Dinamik SL: ${dsl_fiyat:.4f} (%{sl_mesafe_pct:.1f})", state, is_breakout)
                        else:
                            log_ekle(f"🛡️ {secilen_sembol} Fırsatı Boş Geçildi: Global Risk Limiti Dolu.", state)

                    elif sinyal == "KAPAT" and secilen_sembol in state.get("aktif_pozisyonlar", {}):
                        islem_kapat_with_retry(state, secilen_sembol, fiyat, karar_paketi.get("dusunce", ""), exchange)

                    if state.get("pik_bakiye", 0) >= state.get("hedef_bakiye", 100):
                        state["bot_durumu"] = "🎯 Hedefi Ulaştı!"
                        state["bot_calisiyor"] = False
                        log_ekle("🏆 HEDEF ULAŞILDI! Bot durduruluyor.", state)
                        islem_gecmisi_kaydet(state.get("islem_gecmisi", []))
                        dur_sinyali.set()
                    break

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
                except Exception:
                    pass
                son_kayit_zamani = time.time()
                son_kayit_bakiye = guncel_bakiye

            # --- BEKLEME (EVENT-DRIVEN) ---
            bekleme_suresi = 1 # Berserker Mode: Bekleme süresi daima 1 saniye
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

        except Exception as e:
            with lock:
                log_ekle(f"❌ Döngü Hatası (devam ediyor): {str(e)[:100]}", state)
                print(f"⚠️ bot_engine döngü hatası: {e}")
            time.sleep(5)

    # Bot durdurulduğunda son kayıt
    try:
        temiz = {}
        with lock:
            for k, v in state.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    temiz[k] = v
        ps.state_kaydet(temiz)
    except Exception:
        pass


def korelasyon_rutini(state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
    """5 dakikada bir çalışarak en iyi geçmiş işlem koşullarını analiz edip önbelleğe alır."""
    while not dur_sinyali.is_set():
        try:
            korelasyonlar = data_logger.en_iyi_korelasyonlari_getir(limit=50)
            if korelasyonlar:
                with lock:
                    state["kesin_kar_parametreleri"] = korelasyonlar
                    log_ekle(f"🧠 Derin Analiz: Geçmiş işlemlere göre Kesin Kâr güncellendi. (A.Vol: %{korelasyonlar.get('ortalama_volatilite', 0):.1f}, Hacim: %{korelasyonlar.get('ortalama_hacim_artis', 0):.0f})", state)
        except Exception:
            pass
        
        # 300 saniye bekle
        dur_sinyali.wait(300)

# ─────────────────────────────────────────────
# Bot Worker (Singleton Manager)
# ─────────────────────────────────────────────
class BotWorker:
    """Arka plan thread'lerini yöneten singleton."""

    def __init__(self):
        self.state = GlobalBotState()
        self.state.load_from_persistent()
        self._ws_thread = None
        self._engine_thread = None
        self._corr_thread = None

    @property
    def is_running(self) -> bool:
        return self.state.get("bot_calisiyor", False)

    def start(self):
        if self.is_running:
            return

        raw = self.state.raw()
        lock = self.state.lock
        dur = raw["dur_sinyali"]

        dur.clear()
        raw["bot_calisiyor"] = True
        raw["bot_durumu"] = "Çalışıyor"
        if raw.get("baslangic_zamani", 0) == 0.0:
            raw["baslangic_zamani"] = time.time()

        # API Entegrasyonu
        if raw.get("use_real_api"):
            cfg.USE_REAL_API = True
            cfg.API_KEY = ps.decode_key(raw.get("api_key_enc", ""))
            cfg.SECRET_KEY = ps.decode_key(raw.get("api_secret_enc", ""))
        else:
            cfg.USE_REAL_API = False

        self._ws_thread = threading.Thread(target=ws_fiyat_dinleyici, args=(raw, lock, dur), daemon=True)
        self._ws_thread.start()

        self._engine_thread = threading.Thread(target=bot_engine, args=(raw, lock, dur), daemon=True)
        self._engine_thread.start()
        
        self._corr_thread = threading.Thread(target=korelasyon_rutini, args=(raw, lock, dur), daemon=True)
        self._corr_thread.start()

    def stop(self):
        raw = self.state.raw()
        raw["dur_sinyali"].set()
        
        # v6 GRACEFUL SHUTDOWN: Tüm açık pozisyonları piyasa emriyle kapat ve PNL logla
        with self.state.lock:
            kapanacaklar = list(raw.get("aktif_pozisyonlar", {}).keys())
            fiyatlar = raw.get("guncel_fiyatlar", {})
            toplam_shutdown_pnl = 0.0
            for s in kapanacaklar:
                poz = raw["aktif_pozisyonlar"].get(s)
                if not poz:
                    continue
                f = fiyatlar.get(s, poz.get("giris_fiyati", 0))
                if f > 0:
                    margin = poz.get("islem_margin", 0)
                    kaldirac = poz.get("islem_kaldirac", 1)
                    pnl = pnl_hesapla(poz.get("pozisyon", "YOK"), poz.get("giris_fiyati", 0), f, margin * kaldirac, kaldirac)
                    toplam_shutdown_pnl += pnl
                    islem_kapat(raw, s, f, "🚨 BOT DURDURULDU: Kullanıcı İsteği")
            if kapanacaklar:
                log_ekle(f"🚨 GRACEFUL SHUTDOWN: {len(kapanacaklar)} pozisyon kapatıldı. Toplam PNL: {toplam_shutdown_pnl:+.2f} USDT", raw, is_breakout=True)

        raw["bot_calisiyor"] = False
        raw["bot_durumu"] = "Durduruldu"
        self.state.save_to_persistent()

    def switch_mode(self, use_real_api: bool):
        """Demo/Real mod değiştir. Bot duruyorsa state'i yeniden yükler."""
        self.state.save_to_persistent()
        self.state.set("use_real_api", use_real_api)
        self.state.load_from_persistent()
