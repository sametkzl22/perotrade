"""
Data Logger — SQLite Trade & Scan Logging v7
=============================================
Tüm tarama sonuçlarını ve işlem kapanışlarını
trade_logs.db SQLite veritabanında depolar.
Dashboard geçmişe dönük analiz için kullanır.
"""

import sqlite3
import os
import sys
from datetime import datetime, timezone


def _get_db_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "trade_logs.db")


DB_PATH = _get_db_path()


def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    try:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tarama_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT NOT NULL,
                sembol TEXT NOT NULL,
                fiyat REAL,
                skor REAL,
                atr REAL,
                volatilite REAL,
                hacim_artis REAL,
                breakout INTEGER DEFAULT 0,
                mtf_konsensus TEXT,
                karar TEXT
            );
            CREATE TABLE IF NOT EXISTS islem_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT NOT NULL,
                sembol TEXT NOT NULL,
                tip TEXT,
                giris_fiyati REAL,
                cikis_fiyati REAL,
                pnl REAL,
                pnl_pct REAL,
                kaldirac INTEGER,
                margin REAL,
                neden TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tarama_sembol ON tarama_log(sembol);
            CREATE INDEX IF NOT EXISTS idx_tarama_zaman ON tarama_log(zaman);
            CREATE INDEX IF NOT EXISTS idx_islem_sembol ON islem_log(sembol);
            CREATE INDEX IF NOT EXISTS idx_islem_zaman ON islem_log(zaman);
        """)
        conn.close()
    except Exception as e:
        print(f"⚠️ data_logger init hatası: {e}")


# Modül yüklenince tablo oluştur
_init_db()


# ─────────────────────────────────────────────
# Yazma Fonksiyonları
# ─────────────────────────────────────────────
def tarama_kaydet(sembol: str, fiyat: float, skor: float, atr: float = 0,
                  volatilite: float = 0, hacim_artis: float = 0,
                  breakout: bool = False, mtf_konsensus: str = "",
                  karar: str = "BEKLE"):
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO tarama_log
               (zaman, sembol, fiyat, skor, atr, volatilite, hacim_artis, breakout, mtf_konsensus, karar)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), sembol, fiyat, skor,
             atr, volatilite, hacim_artis, 1 if breakout else 0,
             mtf_konsensus, karar)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def islem_kaydet(sembol: str, tip: str, giris_fiyati: float,
                 cikis_fiyati: float, pnl: float, pnl_pct: float,
                 kaldirac: int = 1, margin: float = 0, neden: str = ""):
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO islem_log
               (zaman, sembol, tip, giris_fiyati, cikis_fiyati, pnl, pnl_pct, kaldirac, margin, neden)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), sembol, tip,
             giris_fiyati, cikis_fiyati, pnl, pnl_pct, kaldirac, margin, neden)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────
# Okuma Fonksiyonları (Dashboard)
# ─────────────────────────────────────────────
def basari_orani_getir(son_n: int = 100) -> dict:
    """Son N işlemin başarı oranını döner."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT pnl FROM islem_log ORDER BY id DESC LIMIT ?", (son_n,)
        ).fetchall()
        conn.close()
        if not rows:
            return {"toplam": 0, "karli": 0, "zarari": 0, "oran": 0.0}
        karli = sum(1 for r in rows if r[0] > 0)
        zarari = sum(1 for r in rows if r[0] <= 0)
        return {
            "toplam": len(rows),
            "karli": karli,
            "zarari": zarari,
            "oran": round((karli / len(rows)) * 100, 1) if rows else 0.0
        }
    except Exception:
        return {"toplam": 0, "karli": 0, "zarari": 0, "oran": 0.0}


def son_islemler_getir(limit: int = 50) -> list:
    """Son N işlemi liste olarak döner (dashboard grafik için)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT zaman, sembol, tip, giris_fiyati, cikis_fiyati,
                      pnl, pnl_pct, kaldirac, margin, neden
               FROM islem_log ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
        conn.close()
        return [
            {"zaman": r[0], "sembol": r[1], "tip": r[2],
             "giris": r[3], "cikis": r[4], "pnl": r[5],
             "pnl_pct": r[6], "kaldirac": r[7], "margin": r[8], "neden": r[9]}
            for r in rows
        ]
    except Exception:
        return []


def skor_gecmisi_getir(sembol: str, limit: int = 50) -> list:
    """Belirli coin için skor geçmişi."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT zaman, skor, fiyat, karar FROM tarama_log WHERE sembol=? ORDER BY id DESC LIMIT ?",
            (sembol, limit)
        ).fetchall()
        conn.close()
        return [{"zaman": r[0], "skor": r[1], "fiyat": r[2], "karar": r[3]} for r in rows]
    except Exception:
        return []


def en_iyi_korelasyonlari_getir(limit: int = 50) -> dict:
    """Geçmişteki kârlı işlemlerin ortalama volatilite ve hacim artışı değerlerini döner."""
    try:
        conn = _get_conn()
        # PNL > 0 olan işlemleri çek
        rows = conn.execute(
            "SELECT sembol, zaman FROM islem_log WHERE pnl > 0 ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        
        if not rows:
            conn.close()
            return {}
            
        vol_total = 0.0
        hacim_total = 0.0
        sayac = 0
        
        for sembol, zaman in rows:
            # İşlem zamanından önceki en son tarama kaydını al
            t_row = conn.execute(
                "SELECT volatilite, hacim_artis FROM tarama_log WHERE sembol=? AND zaman <= ? ORDER BY id DESC LIMIT 1",
                (sembol, zaman)
            ).fetchone()
            if t_row:
                vol_total += t_row[0] or 0
                hacim_total += t_row[1] or 0
                sayac += 1
                
        conn.close()
        
        if sayac == 0:
            return {}
            
        return {
            "ortalama_volatilite": round(vol_total / sayac, 2),
            "ortalama_hacim_artis": round(hacim_total / sayac, 2),
            "orneklem_sayisi": sayac
        }
    except Exception:
        return {}


def gercek_pnl_getir(baslangic_zamani_timestamp: float) -> float:
    """Verilen UNIX zaman damgasından (baslangic_zamani) bu yana trade_logs.db'deki realize edilmiş toplam PNL'yi döner."""
    try:
        dt_iso = datetime.fromtimestamp(baslangic_zamani_timestamp, tz=timezone.utc).isoformat()
        conn = _get_conn()
        row = conn.execute(
            "SELECT SUM(pnl) FROM islem_log WHERE zaman >= ?", (dt_iso,)
        ).fetchone()
        conn.close()
        
        if row and row[0] is not None:
            return float(row[0])
        return 0.0
    except Exception as e:
        print(f"⚠️ PNL Doğrulama Hatası: {e}")
        return 0.0
