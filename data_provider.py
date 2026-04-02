"""
Data Provider — WebSocket and REST API Background Fetcher
==========================================================
Isolated background tasks for fetching live prices and balances.
Uses threading to decouple fetching from Streamlit UI entirely.
"""

import threading
import time
import asyncio
import logging
import ccxt
import ccxt.pro as ccxtpro
import config as cfg
import persistent_state as ps

# Silence noisy websocket logs to prevent terminal spam
logging.getLogger('websockets').setLevel(logging.ERROR)
logging.getLogger('websockets.client').setLevel(logging.ERROR)
logging.getLogger('asyncio').setLevel(logging.ERROR)


class DataProvider:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DataProvider, cls).__new__(cls)
                cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self.state_lock = threading.Lock()
        self.guncel_fiyatlar = {}
        self.bakiye = 0.0
        self.bot_state = None
        self.bot_lock = None
        self.dur_sinyali = None
        self.ws_thread = None
        self.bal_thread = None

    def start_if_needed(self, state: dict, lock: threading.Lock, dur_sinyali: threading.Event):
        """Starts the data provider background loops if not already running."""
        with self.state_lock:
            self.bot_state = state
            self.bot_lock = lock
            self.dur_sinyali = dur_sinyali

            if self.ws_thread is None or not self.ws_thread.is_alive():
                self.ws_thread = threading.Thread(target=self._ws_runner, daemon=True)
                self.ws_thread.start()

            if self.bal_thread is None or not self.bal_thread.is_alive():
                self.bal_thread = threading.Thread(target=self._bal_runner, daemon=True)
                self.bal_thread.start()

            if getattr(self, "ob_thread", None) is None or not self.ob_thread.is_alive():
                self.ob_thread = threading.Thread(target=self._ob_runner, daemon=True)
                self.ob_thread.start()

    def get_latest_prices(self) -> dict:
        """UI can fetch current prices directly."""
        with self.state_lock:
            return self.guncel_fiyatlar.copy()

    def get_balance(self) -> float:
        """UI can fetch current balance directly."""
        with self.state_lock:
            return self.bakiye

    def _exchange_olustur(self, pro=False) -> object:
        exchange_adi = self.bot_state.get("exchange_adi", "binance") if self.bot_state else cfg.EXCHANGE_NAME
        futures_type = getattr(cfg, "FUTURES_TYPE", "future")

        params = {
            "enableRateLimit": True,
            "options": {"defaultType": futures_type},
        }

        if self.bot_state and self.bot_state.get("use_real_api", False):
            api_key = ps.decode_key(self.bot_state.get("api_key_enc", ""))
            api_secret = ps.decode_key(self.bot_state.get("api_secret_enc", ""))
            if api_key and api_secret:
                params["apiKey"] = api_key
                params["secret"] = api_secret

        lib = ccxtpro if pro else ccxt
        return getattr(lib, exchange_adi)(params)

    def _log_ekle(self, mesaj: str, is_breakout=False, is_liq=False):
        """Helper to append log to the local bot state thoughts."""
        if not self.bot_state or not self.bot_lock:
            return
        with self.bot_lock:
            zaman = time.strftime("%H:%M:%S", time.gmtime())
            ai_logs = self.bot_state.get("ai_dusunce_gunlugu", [])
            ai_logs.insert(0, {"time": zaman, "msg": mesaj, "breakout": is_breakout, "liq": is_liq})
            if len(ai_logs) > 60:
                ai_logs.pop()

    def _ws_runner(self):
        """Runs the Async loop for WebSockets inside a standard Thread."""
        from bot_worker import islem_kapat, pnl_hesapla, pnl_hesapla_coklu, aktif_margin_toplami
        
        async def dinle():
            exchange = None
            consecutive_failures = 0
            max_failures = 3

            while not self.dur_sinyali.is_set():
                try:
                    if exchange is not None:
                        try:
                            await exchange.close()
                        except (ccxt.BaseError, sqlite3.Error, Exception):
                            pass
                    exchange = self._exchange_olustur(pro=True)
                    if consecutive_failures > 0:
                        self._log_ekle(f"🔄 WebSocket yeniden bağlandı (deneme #{consecutive_failures})")
                except (ccxt.BaseError, sqlite3.Error, Exception) as e:
                    consecutive_failures += 1
                    self._log_ekle(f"❌ WebSocket bağlantı hatası (#{consecutive_failures}): {str(e)[:80]}")
                    if consecutive_failures >= max_failures:
                        self._log_ekle("🛑 3 başarısız deneme. 5 dk Circuit Breaker devrede (300sn)...")
                        await asyncio.sleep(300)
                        consecutive_failures = 0
                    else:
                        await asyncio.sleep(5)
                    continue

                inner_failed = False
                while not self.dur_sinyali.is_set():
                    try:
                        sembol = self.bot_state.get("aktif_sembol")
                        dinlenecekler = list(set(p.get("sembol", tid) for tid, p in self.bot_state.get("aktif_pozisyonlar", {}).items()))
                        if sembol and sembol != "Bekleniyor..." and sembol not in dinlenecekler:
                            dinlenecekler.insert(0, sembol)
                        
                        if dinlenecekler:
                            try:
                                res = await asyncio.wait_for(exchange.watch_tickers(dinlenecekler), timeout=5.0)
                                temp_prices = {}
                                
                                for s, tck in res.items():
                                    if isinstance(tck, dict):
                                        fiyat = tck.get("last", 0.0)
                                        temp_prices[s] = fiyat
                                        with self.state_lock:
                                            self.guncel_fiyatlar[s] = fiyat
                                            
                                        if s == sembol:
                                            with self.bot_lock:
                                                self.bot_state["fiyat"] = fiyat
                                                if tck.get("percentage"): self.bot_state["degisim_24s"] = tck.get("percentage")
                                                if tck.get("quoteVolume"): self.bot_state["hacim_24s"] = tck.get("quoteVolume")
                                                
                                                sf = self.bot_state.get("son_fiyat_tick", 0)
                                                if sf > 0 and fiyat != sf:
                                                    degisim_tick = abs((fiyat - sf) / sf) * 100
                                                    if degisim_tick >= 0.3:
                                                        self.bot_state["analiz_tetikleyici"].set()
                                                self.bot_state["son_fiyat_tick"] = fiyat

                                with self.bot_lock:
                                    # Copy temp_prices to bot_state for legacy compatibility
                                    self.bot_state.setdefault("guncel_fiyatlar", {}).update(temp_prices)
                                    toplam_margin = aktif_margin_toplami(self.bot_state.get("aktif_pozisyonlar", {}))
                                    top_pnl_anlik = pnl_hesapla_coklu(self.bot_state.get("aktif_pozisyonlar", {}), self.bot_state["guncel_fiyatlar"])
                                    anlik_varlik = self.bot_state["bakiye"] + toplam_margin + top_pnl_anlik
                                    max_izin_verilir_risk = anlik_varlik * 0.20

                                    if top_pnl_anlik < 0 and abs(top_pnl_anlik) >= max_izin_verilir_risk:
                                        acik_tids = list(self.bot_state.get("aktif_pozisyonlar", {}).keys())
                                        for tid in acik_tids:
                                            poz_gs = self.bot_state["aktif_pozisyonlar"].get(tid, {})
                                            s_gs = poz_gs.get("sembol", tid)
                                            f_s = self.bot_state["guncel_fiyatlar"].get(s_gs, poz_gs.get("giris_fiyati", 0))
                                            islem_kapat(self.bot_state, tid, f_s, "🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam zarar %20'yi aştı.")
                                        self._log_ekle("🚨 GLOBAL STOP-LOSS TETİKLENDİ! Toplam Bakiye Korundu.", is_breakout=True)

                                    kapanacak_semboller = []
                                    for p_tid, poz in list(self.bot_state.get("aktif_pozisyonlar", {}).items()):
                                        p_sembol = poz.get("sembol", p_tid)
                                        f_s = self.bot_state["guncel_fiyatlar"].get(p_sembol, poz.get("giris_fiyati", 0))
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
                                            islem_kapat(self.bot_state, p_tid, f_s, "Liquidation", is_liq=True)
                                            if self.bot_state["bakiye"] <= 0:
                                                self.bot_state["bot_durumu"] = "💀 İflas"
                                                self.bot_state["bot_calisiyor"] = False
                                                self.dur_sinyali.set()
                                        elif poz.get("dinamik_sl_fiyat", 0) > 0:
                                            dsl = poz["dinamik_sl_fiyat"]
                                            dsl_hit = (is_long and f_s <= dsl) or (is_short and f_s >= dsl)
                                            if dsl_hit:
                                                islem_kapat(self.bot_state, p_tid, f_s, f"🛡️ DİNAMİK SL TETİKLENDİ: ATR Stop ${dsl:.4f}")
                                        else:
                                            is_scalper = self.bot_state.get("mod") == "💎 Ultra-Scalper"

                                            if is_scalper:
                                                if pnl_pct >= 1.5:
                                                    kapanacak_semboller.append(p_tid)
                                                    poz["kapat_nedeni"] = f"💎 SCALPER TP: %{pnl_pct:.1f} ROE Kâr yakalandı!"
                                                    self._log_ekle(f"💎 SCALPER TP: {p_sembol} %{pnl_pct:.1f} ROE → Kâr alındı.", is_breakout=True)
                                                elif pnl_pct <= -0.5:
                                                    kapanacak_semboller.append(p_tid)
                                                    poz["kapat_nedeni"] = f"💎 SCALPER SL: %{pnl_pct:.1f} ROE zararla durduruldu."
                                                elif (time.time() - poz.get("acilis_zamani", 0)) > 300:
                                                    kapanacak_semboller.append(p_tid)
                                                    poz["kapat_nedeni"] = f"💎 SCALPER TIMEOUT: 5 dakika doldu."
                                            elif is_scalper and pnl_pct >= 0.5 and not poz.get("ts_aktif"):
                                                poz["ts_aktif"] = True
                                                if is_long:
                                                    poz["trailing_stop_fiyat"] = poz["giris_fiyati"] * 0.997
                                                else:
                                                    poz["trailing_stop_fiyat"] = poz["giris_fiyati"] * 1.003
                                            elif pnl_pct >= 10.0 and not poz.get("kademeli_tp_yapildi", False):
                                                poz["kademeli_tp_yapildi"] = True
                                                real_pnl = aktif_pnl_val / 2
                                                ret_margin = poz["islem_margin"] / 2
                                                self.bot_state["bakiye"] += (ret_margin + real_pnl)
                                                poz["islem_margin"] /= 2
                                                poz["coin_miktar"] /= 2
                                                poz["ts_aktif"] = True
                                                poz["trailing_stop_fiyat"] = poz["giris_fiyati"]
                                                z = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                                                self.bot_state["islem_gecmisi"].append({
                                                    "zaman": z, "sembol": p_sembol, "sinyal": "💰 %50 TP",
                                                    "fiyat": round(f_s, 4), "kaldirac": f"{poz['islem_kaldirac']}x",
                                                    "poz_buyukluk": round(poz["coin_miktar"], 2),
                                                    "bakiye_usdt": round(self.bot_state["bakiye"], 2),
                                                    "kar_zarar": f"{real_pnl:+.2f} USDT", "ai_notu": "%10 ROE: %50 Kâr Alındı, TS Başabaş."
                                                })

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
                                                    kapanacak_semboller.append(p_tid)

                                            gecen_dk = (time.time() - poz.get("acilis_zamani", time.time())) / 60.0
                                            zaman_limit = 5.0 if self.bot_state.get("mod") == "💎 Ultra-Scalper" else 60.0
                                            pnl_esik = 0.3 if self.bot_state.get("mod") == "💎 Ultra-Scalper" else 0.5
                                            if gecen_dk >= zaman_limit and abs(pnl_pct) < pnl_esik:
                                                if p_tid not in kapanacak_semboller:
                                                    kapanacak_semboller.append(p_tid)
                                                    poz["kapat_nedeni"] = f"Zaman Maliyeti: Yetersiz Volatilite"

                                    for ks_tid in kapanacak_semboller:
                                        ks_poz = self.bot_state["aktif_pozisyonlar"].get(ks_tid, {})
                                        ks_sembol = ks_poz.get("sembol", ks_tid)
                                        f_ks = self.bot_state["guncel_fiyatlar"].get(ks_sembol, ks_poz.get("giris_fiyati", 0))
                                        rsn = ks_poz.get("kapat_nedeni", "🛡️ TS KAPAT - İz Süren Stop")
                                        islem_kapat(self.bot_state, ks_tid, f_ks, rsn)

                                    anlik_v = self.bot_state["bakiye"] + aktif_margin_toplami(self.bot_state.get("aktif_pozisyonlar", {})) + pnl_hesapla_coklu(self.bot_state.get("aktif_pozisyonlar", {}), self.bot_state["guncel_fiyatlar"])
                                    if anlik_v > self.bot_state.get("pik_bakiye", 0):
                                        self.bot_state["pik_bakiye"] = anlik_v
                                    elif self.bot_state.get("pik_bakiye", 0) > 0:
                                        dd = (self.bot_state["pik_bakiye"] - anlik_v) / self.bot_state["pik_bakiye"] * 100
                                        if dd > self.bot_state.get("max_drawdown", 0):
                                            self.bot_state["max_drawdown"] = dd

                            except asyncio.TimeoutError:
                                pass
                        else:
                            await asyncio.sleep(0.5)
                        
                        consecutive_failures = 0  # Başarılı veri alındıysa hataları sıfırla
                    except Exception as loop_e:
                        self._log_ekle(f"⚠️ WebSocket akış koptu: {str(loop_e)[:80]}")
                        inner_failed = True
                        break

                if inner_failed:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        self._log_ekle("🛑 3 kez akış koptu. 5 dk Circuit Breaker (300sn)...")
                        await asyncio.sleep(300)
                        consecutive_failures = 0
                    else:
                        await asyncio.sleep(2)

                try:
                    await exchange.close()
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(dinle())

    def _ob_runner(self):
        """V28: Background Async loop to stream OrderBook via ccxt.pro WebSocket."""
        async def dinle_ob():
            exchange = None
            consecutive_failures = 0
            while not self.dur_sinyali.is_set():
                try:
                    if exchange is None:
                        exchange = self._exchange_olustur(pro=True)
                except Exception:
                    await asyncio.sleep(5)
                    continue

                inner_failed = False
                while not self.dur_sinyali.is_set():
                    try:
                        tracked_sym = None
                        with self.bot_lock:
                            if self.bot_state:
                                tracked_sym = self.bot_state.get("ws_ob_sembol")
                        
                        if not tracked_sym:
                            await asyncio.sleep(1.0)
                            continue

                        # Watch the stream
                        ob = await asyncio.wait_for(exchange.watch_order_book(tracked_sym, limit=100), timeout=5.0)
                        
                        with self.bot_lock:
                            if self.bot_state:
                                g_ob = self.bot_state.get("guncel_orderbooks", {})
                                g_ob[tracked_sym] = ob
                                self.bot_state["guncel_orderbooks"] = g_ob
                        
                        consecutive_failures = 0
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        inner_failed = True
                        break

                if inner_failed:
                    consecutive_failures += 1
                    try:
                        await exchange.close()
                    except:
                        pass
                    exchange = None
                    if consecutive_failures >= 3:
                        await asyncio.sleep(60)
                        consecutive_failures = 0
                    else:
                        await asyncio.sleep(2)

            if exchange is not None:
                try:
                    await exchange.close()
                except:
                    pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(dinle_ob())

    def _bal_runner(self):
        """Thread that updates REST API balances occasionally."""
        exchange = None
        while not self.dur_sinyali.is_set():
            if self.bot_state.get("use_real_api", False) and self.bot_state.get("api_key_enc", ""):
                if exchange is None:
                    try:
                        exchange = self._exchange_olustur(pro=False)
                    except (ccxt.BaseError, sqlite3.Error, Exception):
                        time.sleep(5)
                        continue
                try:
                    bal = exchange.fetch_balance()
                    free_usdt = float(bal.get('USDT', {}).get('free', 0.0))
                    
                    with self.state_lock:
                        self.bakiye = free_usdt
                        
                    with self.bot_lock:
                        self.bot_state["gercek_bakiye"] = free_usdt
                except ccxt.AuthenticationError:
                    with self.bot_lock:
                        self.bot_state["bot_durumu"] = "API Kimlik Hatası"
                        self.bot_state["bot_calisiyor"] = False
                        self.bot_state["auth_error_notified"] = True
                        self.bot_state["auth_error_msg"] = "API anahtarlarınızın süresi dolmuş veya Futures erişimi kapalıdır."
                        self._log_ekle("🚨 [KRİTİK HATA] Binance API Kimlik Doğrulama Başarısız. Profil>API yönetimi kısmından 'Enable Futures' iznini kontrol edin.")
                    self.dur_sinyali.set()
                    break
                except (ccxt.BaseError, sqlite3.Error, Exception):
                    pass
            time.sleep(10)
