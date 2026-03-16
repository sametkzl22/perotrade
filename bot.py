"""
PeroTrade Pro — Headless Bot v5
===============================
7/24 arka planda çalışan, UI gerektirmeyen otonom trading engine.
Persistent state ile PC yeniden başladığında kaldığı yerden devam eder.

Kullanım:
    python bot.py              # Headless mod (UI yok)
    streamlit run streamlit_app.py  # Dashboard ile
"""

import time
import signal
import sys
from datetime import datetime, timezone

import ccxt

import config as cfg
import ai_engine
import persistent_state as ps

# ─────────────────────────────────────────────
# Global stop flag
# ─────────────────────────────────────────────
running = True

def signal_handler(sig, frame):
    global running
    print("\n⛔ Durdurma sinyali alındı. Pozisyonlar korunarak çıkılıyor...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ─────────────────────────────────────────────
# Exchange Oluşturma (API destekli)
# ─────────────────────────────────────────────
def exchange_olustur() -> ccxt.Exchange:
    params = {"enableRateLimit": True}
    
    if cfg.USE_REAL_API and cfg.API_KEY and cfg.SECRET_KEY:
        params["apiKey"] = cfg.API_KEY
        params["secret"] = cfg.SECRET_KEY
        params["options"] = {"defaultType": "future"}
        print("🔑 Binance Futures API bağlantısı (GERÇEK İŞLEM)")
    else:
        print("📄 Paper Trading modu (API anahtarı yok)")
    
    exchange = getattr(ccxt, cfg.EXCHANGE_NAME)(params)
    return exchange


# ─────────────────────────────────────────────
# PNL Hesaplama  
# ─────────────────────────────────────────────
def pnl_hesapla(tip, giris, guncel, kaldirac):
    if tip == "LONG":
        return ((guncel - giris) / giris) * kaldirac * (giris * kaldirac)
    else:
        return ((giris - guncel) / giris) * kaldirac * (giris * kaldirac)


# ─────────────────────────────────────────────
# Likidasyon Fiyatı
# ─────────────────────────────────────────────
def likidasyon_hesapla(tip, fiyat, kaldirac):
    marj = 1.0 / kaldirac
    if tip == "LONG":
        return fiyat * (1 - marj * 0.90)
    else:
        return fiyat * (1 + marj * 0.90)


# ─────────────────────────────────────────────
# Ana Bot Döngüsü (Headless)
# ─────────────────────────────────────────────
def main():
    global running
    
    print("=" * 60)
    print("  🤖 PeroTrade Pro — Headless Bot v5")
    print("  7/24 Otonom Algoritmik Trading Sistemi")
    print("=" * 60)
    
    # Persistent state yükle
    state = ps.state_yukle()
    
    # Exchange bağlantısı
    exchange = exchange_olustur()
    print(f"  Exchange  : {cfg.EXCHANGE_NAME}")
    print(f"  Bakiye    : ${state['bakiye']:.2f}")
    print(f"  Gün #     : {state.get('gun_sayaci', 0)}")
    hedef = ps.bilesik_faiz_hedef(state)
    print(f"  Bugün Hedef: ${hedef:.2f} (+%{cfg.DAILY_TARGET_PCT})")
    print("=" * 60)
    
    dongusayaci = 0
    
    while running:
        dongusayaci += 1
        try:
            # ─── Yeni Gün Kontrolü ───
            bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if state.get("son_gun", "") != bugun:
                # Gün sonu raporu
                print("\n" + ps.gun_sonu_raporu(state))
                # Bileşik faiz: Yeni günün bakiyesini ayarla
                state["gun_baslangic_bakiye"] = state["bakiye"]
                state["son_gun"] = bugun
                state["gun_sayaci"] = state.get("gun_sayaci", 0) + 1
                ps.state_kaydet(state)
                print(f"\n📅 YENİ GÜN! Bileşik bakiye: ${state['bakiye']:.2f}")
            
            # ─── Günlük Risk Barometresi ───
            gunluk_kar = ps.gunluk_kar_pct(state)
            if gunluk_kar >= cfg.DAILY_PROFIT_LOCK:
                print(f"🛡️ GÜVENLİ MOD: Günlük kâr %{gunluk_kar:.1f} (>=%{cfg.DAILY_PROFIT_LOCK}). Yeni işlem yok.")
                time.sleep(cfg.HEADLESS_CHECK_INTERVAL * 5)
                continue
            elif gunluk_kar <= cfg.DAILY_LOSS_STOP:
                print(f"🚨 PANİK KORUMASI: Günlük kayıp %{gunluk_kar:.1f}. İşlemler askıda.")
                time.sleep(cfg.HEADLESS_CHECK_INTERVAL * 10)
                continue
            
            # ─── BTC Trend ───
            btc_trend = ai_engine.btc_trendi_analiz_et(exchange)
            
            # ─── Coin Tarama (CPU dostu limit) ───
            top_coinler = ai_engine.top_coinleri_tara(exchange, limit=cfg.HEADLESS_COIN_SCAN_LIMIT)
            tarama = ai_engine.anormallik_tara_ve_sec(exchange, top_coinler, cfg.SHORT_MA, cfg.LONG_MA)
            
            secilen = tarama.get("secilen_sembol") or "BTC/USDT"
            pazar = tarama.get("secilen_pazar", {})
            sma = tarama.get("secilen_sma", "BEKLE")
            breakout = tarama.get("secilen_breakout", False)
            rapor = tarama.get("karar_raporu", "")
            haber_p = tarama.get("haber_puanlari", {})
            
            # ─── Fiyat ───
            try:
                ticker = exchange.fetch_ticker(secilen)
                fiyat = ticker.get("last", pazar.get("fiyat", 0))
            except:
                fiyat = pazar.get("fiyat", 0)
            
            if rapor:
                print(f"\n{'─'*50}")
                for satir in rapor.split('\n'):
                    print(f"  📊 {satir}")
            
            # ─── AI Karar ───
            poz_durumu = state["aktif_pozisyonlar"].get(secilen, {}).get("pozisyon", "YOK")
            fonlama = ai_engine.fonlama_orani_getir(exchange, secilen)
            skor = ai_engine.kompozit_skor_hesapla(pazar, sma)
            karar = ai_engine.mock_ai_karar(secilen, pazar, skor, poz_durumu, btc_trend, fonlama)
            
            # ─── NLP Haber Veto ───
            if haber_p:
                veto = ai_engine.haber_vetosu(haber_p, karar["karar"])
                if veto["veto"]:
                    print(f"  {veto['neden']}")
                    karar["karar"] = "BEKLE"
            
            sinyal = karar["karar"]
            print(f"  🎯 [{secilen}] Sinyal: {sinyal} | Skor: {skor:.1f} | BTC: {btc_trend}")
            print(f"     {karar['dusunce'][:100]}")
            
            # ─── İşlem Aç ───
            if sinyal in ["LONG", "SHORT"] and secilen not in state["aktif_pozisyonlar"]:
                oran = karar.get("tavsiye_oran", 0.10)
                klvr = karar.get("tavsiye_kaldirac", 10)
                margin = state["bakiye"] * min(oran, cfg.MAX_RISK_PER_TRADE)
                
                if margin > 0.5:
                    state["aktif_pozisyonlar"][secilen] = {
                        "pozisyon": sinyal,
                        "giris_fiyati": fiyat,
                        "islem_margin": margin,
                        "islem_kaldirac": klvr,
                        "likidasyon_fiyati": likidasyon_hesapla(sinyal, fiyat, klvr),
                        "acilis_zamani": time.time(),
                        "dca_sayisi": 0,
                        "giris_nedeni": karar["dusunce"][:120]
                    }
                    state["bakiye"] -= margin
                    state["toplam_islem_sayisi"] = state.get("toplam_islem_sayisi", 0) + 1
                    print(f"  💰 {klvr}x {sinyal} AÇILDI: {secilen} @ ${fiyat:.4f} (Margin: ${margin:.2f})")
            
            # ─── İşlem Kapat ───
            elif sinyal == "KAPAT" and secilen in state["aktif_pozisyonlar"]:
                poz = state["aktif_pozisyonlar"][secilen]
                pnl = pnl_hesapla(poz["pozisyon"], poz["giris_fiyati"], fiyat, poz["islem_kaldirac"])
                state["bakiye"] += poz["islem_margin"] + pnl
                state["toplam_kar"] = state.get("toplam_kar", 0) + pnl
                del state["aktif_pozisyonlar"][secilen]
                print(f"  {'🟢' if pnl >= 0 else '🔴'} KAPATILDI: {secilen} | PNL: {pnl:+.4f} USDT")
            
            # ─── DCA Kontrolü ───
            for sym, poz in list(state["aktif_pozisyonlar"].items()):
                try:
                    t = exchange.fetch_ticker(sym)
                    g_fiyat = t.get("last", poz["giris_fiyati"])
                except:
                    g_fiyat = poz["giris_fiyati"]
                
                dca = ai_engine.dca_hesapla(poz, g_fiyat, state["bakiye"])
                if dca["uygun"]:
                    ekleme = dca["ekleme_margin"]
                    if ekleme <= state["bakiye"]:
                        state["aktif_pozisyonlar"][sym]["islem_margin"] += ekleme
                        state["aktif_pozisyonlar"][sym]["giris_fiyati"] = dca["yeni_ortalama"]
                        state["aktif_pozisyonlar"][sym]["dca_sayisi"] = dca.get("dca_sayisi", 1)
                        state["bakiye"] -= ekleme
                        print(f"  💱 DCA: {sym} | {dca['neden']}")
            
            # ─── Peak & Drawdown ───
            if state["bakiye"] > state.get("pik_bakiye", 0):
                state["pik_bakiye"] = state["bakiye"]
            dd = ((state["pik_bakiye"] - state["bakiye"]) / state["pik_bakiye"] * 100) if state["pik_bakiye"] > 0 else 0
            if dd > state.get("max_drawdown", 0):
                state["max_drawdown"] = dd
            
            # ─── Her 5 döngüde kaydet (CPU dostu) ───
            if dongusayaci % 5 == 0:
                ps.state_kaydet(state)
                print(f"  💾 State kaydedildi (Bakiye: ${state['bakiye']:.2f}, Gün Kârı: %{gunluk_kar:+.1f})")
            
            # ─── Bekleme ───
            wait = cfg.HEADLESS_CHECK_INTERVAL
            if breakout:
                wait = 5  # Breakout'ta hızlı analiz
                print(f"  🔥 BREAKOUT! Hızlı analiz: {wait}s")
            
            time.sleep(wait)
            
        except Exception as e:
            print(f"  ❌ Hata: {e}")
            time.sleep(10)
    
    # ─── Temiz Çıkış ───
    print("\n" + ps.gun_sonu_raporu(state))
    ps.state_kaydet(state)
    print(f"\n💾 State kaydedildi. Bakiye: ${state['bakiye']:.2f}")
    print("👋 Bot güvenle durduruldu. Tekrar başlatmak için: python bot.py")


if __name__ == "__main__":
    main()
