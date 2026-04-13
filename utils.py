"""
PeroTrade Pro — Pure Math Utilities
====================================
Sıfır dış bağımlılık (ccxt, xgboost, pandas YOK).
Dashboard ve Engine tarafından güvenle import edilebilir.
"""

import config as cfg


def pnl_hesapla(pozisyon: str, giris: float, anlik: float, miktar: float, kaldirac: int) -> float:
    """Tek pozisyon PNL hesaplayıcı."""
    if pozisyon == "YOK" or giris == 0:
        return 0.0
    margin = miktar / kaldirac
    if pozisyon == "LONG":
        pnl_pct = (anlik - giris) / giris
    else:
        pnl_pct = (giris - anlik) / giris
    return margin * pnl_pct * kaldirac


def aktif_margin_toplami(pozisyonlar: dict) -> float:
    """Tüm açık pozisyonların toplam margin'ini hesaplar."""
    return sum(p.get("islem_margin", 0) for p in pozisyonlar.values())


def pnl_hesapla_coklu(pozlar: dict, guncel_fiyatlar: dict) -> float:
    """Birden fazla pozisyonun toplam PNL'ini hesaplar."""
    toplam_pnl = 0.0
    for tid, poz in pozlar.items():
        s = poz.get("sembol", tid)
        anlik = guncel_fiyatlar.get(s, poz.get("giris_fiyati", 0))
        p_pnl = pnl_hesapla(
            poz.get("pozisyon", "YOK"),
            poz.get("giris_fiyati", 0),
            anlik,
            poz.get("islem_margin", 0) * poz.get("islem_kaldirac", 1),
            poz.get("islem_kaldirac", 1),
        )
        toplam_pnl += p_pnl
    return toplam_pnl


def gunluk_kar_hesapla(state: dict) -> float:
    """Günlük kâr/zarar yüzdesi hesaplar (Dashboard & Engine ortak)."""
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", getattr(cfg, "INITIAL_BALANCE", 100.0)))
    if not isinstance(gun_baslangic, (int, float)) or gun_baslangic <= 0:
        gun_baslangic = getattr(cfg, "INITIAL_BALANCE", 100.0)
        if gun_baslangic <= 0:
            return 0.0

    bakiye = state.get("bakiye", gun_baslangic)
    aktif_pozisyonlar = state.get("aktif_pozisyonlar", {})
    guncel_fiyatlar = state.get("guncel_fiyatlar", {})

    # Toplam Equity: Boş bakiye + Kullanılan Margin + Gerçekleşmemiş PNL
    margin_toplami = aktif_margin_toplami(aktif_pozisyonlar)
    unrealized_pnl = pnl_hesapla_coklu(aktif_pozisyonlar, guncel_fiyatlar)
    
    toplam_equity = bakiye + margin_toplami + unrealized_pnl

    return ((toplam_equity - gun_baslangic) / gun_baslangic) * 100


def likidasyon_hesapla(pozisyon: str, giris: float, kaldirac: int) -> float:
    """Tahmini likidasyon fiyatı hesaplar."""
    if pozisyon == "YOK" or giris == 0:
        return 0.0
    if pozisyon == "LONG":
        return giris * (1 - (1 / kaldirac))
    elif pozisyon == "SHORT":
        return giris * (1 + (1 / kaldirac))
    return 0.0
