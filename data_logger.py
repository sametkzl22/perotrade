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


import sqlite3
import os
import sys
import threading
import queue
from datetime import datetime, timezone
import settings_manager

_initialized_dbs = set()
_write_queue = queue.Queue()

def _get_db_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    
    is_real = settings_manager.is_real_mode_active()
    folder_name = "real" if is_real else "demo"
    file_name = "real_trades.db" if is_real else "demo_trades.db"
    
    os.makedirs(os.path.join(base, "data", folder_name), exist_ok=True)
    return os.path.join(base, "data", folder_name, file_name)


def _get_conn():
    db_path = _get_db_path()
    if db_path not in _initialized_dbs:
        _init_db(db_path)
        _migrate_db(db_path)
        _initialized_dbs.add(db_path)
        
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db(db_path: str):
    try:
        conn = sqlite3.connect(db_path, timeout=5)
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
                neden TEXT,
                etiket TEXT DEFAULT '',
                trade_id TEXT DEFAULT '',
                rsi REAL,
                bollinger_ust REAL,
                bollinger_alt REAL,
                hacim_oran REAL
            );
            CREATE INDEX IF NOT EXISTS idx_tarama_sembol ON tarama_log(sembol);
            CREATE INDEX IF NOT EXISTS idx_tarama_zaman ON tarama_log(zaman);
            CREATE INDEX IF NOT EXISTS idx_islem_sembol ON islem_log(sembol);
            CREATE INDEX IF NOT EXISTS idx_islem_zaman ON islem_log(zaman);
            CREATE INDEX IF NOT EXISTS idx_islem_etiket ON islem_log(etiket);
        """)
        conn.close()
    except Exception as e:
        print(f"⚠️ data_logger init hatası: {e}")


def _migrate_db(db_path: str):
    """Mevcut veritabanına yeni kolonları güvenli ekler."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        migrations = [
            "ALTER TABLE islem_log ADD COLUMN etiket TEXT DEFAULT ''",
            "ALTER TABLE islem_log ADD COLUMN rsi REAL",
            "ALTER TABLE islem_log ADD COLUMN bollinger_ust REAL",
            "ALTER TABLE islem_log ADD COLUMN bollinger_alt REAL",
            "ALTER TABLE islem_log ADD COLUMN hacim_oran REAL",
            "ALTER TABLE islem_log ADD COLUMN trade_id TEXT DEFAULT ''",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass  # Zaten var
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────
# Queue Worker Thread
# ─────────────────────────────────────────────
def _db_writer_worker():
    """Background thread that handles all writes to SQLite sequentially"""
    while True:
        try:
            task = _write_queue.get()
            if task is None:
                break
            
            task_type = task.get("type")
            data = task.get("data")
            
            if task_type == "tarama":
                try:
                    conn = _get_conn()
                    conn.execute(
                        """INSERT INTO tarama_log
                           (zaman, sembol, fiyat, skor, atr, volatilite, hacim_artis, breakout, mtf_konsensus, karar)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (data["zaman"], data["sembol"], data["fiyat"], data["skor"],
                         data["atr"], data["volatilite"], data["hacim_artis"], data["breakout"],
                         data["mtf_konsensus"], data["karar"])
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"⚠️ DB Queue Tarama Insert Error: {e}")
            
            elif task_type == "islem":
                try:
                    conn = _get_conn()
                    conn.execute(
                        """INSERT INTO islem_log
                           (zaman, sembol, tip, giris_fiyati, cikis_fiyati, pnl, pnl_pct,
                            kaldirac, margin, neden, etiket, trade_id, rsi, bollinger_ust, bollinger_alt, hacim_oran)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (data["zaman"], data["sembol"], data["tip"],
                         data["giris_fiyati"], data["cikis_fiyati"], data["pnl"], data["pnl_pct"],
                         data["kaldirac"], data["margin"], data["neden"], data["etiket"], data["trade_id"],
                         data["rsi"], data["bollinger_ust"], data["bollinger_alt"], data["hacim_oran"])
                    )
                    conn.commit()
                    conn.close()
                    print(f"✅ Trade logged to DB via Queue: {data['sembol']} {data['tip']} [tid:{data['trade_id']}] (PNL: ${data['pnl']:.2f})")
                except Exception as e:
                    print(f"⚠️ DB Queue Islem Insert Error: {e}")
            
            _write_queue.task_done()
        except Exception as e:
            print(f"⚠️ DB Worker Exception: {e}")

# Start the background writer thread
_writer_thread = threading.Thread(target=_db_writer_worker, daemon=True)
_writer_thread.start()

# ─────────────────────────────────────────────
# Yazma Fonksiyonları
# ─────────────────────────────────────────────
def tarama_kaydet(sembol: str, fiyat: float, skor: float, atr: float = 0,
                  volatilite: float = 0, hacim_artis: float = 0,
                  breakout: bool = False, mtf_konsensus: str = "",
                  karar: str = "BEKLE"):
    data = {
        "zaman": datetime.now(timezone.utc).isoformat(),
        "sembol": sembol, "fiyat": fiyat, "skor": skor, 
        "atr": atr, "volatilite": volatilite, "hacim_artis": hacim_artis, 
        "breakout": 1 if breakout else 0, "mtf_konsensus": mtf_konsensus, "karar": karar
    }
    _write_queue.put({"type": "tarama", "data": data})


def islem_kaydet(sembol: str, tip: str, giris_fiyati: float,
                 cikis_fiyati: float, pnl: float, pnl_pct: float,
                 kaldirac: int = 1, margin: float = 0, neden: str = "",
                 etiket: str = "", trade_id: str = "",
                 rsi: float = None, bollinger_ust: float = None,
                 bollinger_alt: float = None, hacim_oran: float = None):
    data = {
        "zaman": datetime.now(timezone.utc).isoformat(),
        "sembol": sembol, "tip": tip, "giris_fiyati": giris_fiyati,
        "cikis_fiyati": cikis_fiyati, "pnl": pnl, "pnl_pct": pnl_pct,
        "kaldirac": kaldirac, "margin": margin, "neden": neden,
        "etiket": etiket, "trade_id": trade_id, "rsi": rsi,
        "bollinger_ust": bollinger_ust, "bollinger_alt": bollinger_alt, "hacim_oran": hacim_oran
    }
    _write_queue.put({"type": "islem", "data": data})


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


def challenge_pnl_getir(baslangic_zamani_timestamp: float) -> float:
    """Challenge modunda realize edilmiş (closed) işlemlerin toplam PNL'sini döner.
    Sadece etiket='CHALLENGE_MODE' olan kayıtları filtreler."""
    try:
        dt_iso = datetime.fromtimestamp(baslangic_zamani_timestamp, tz=timezone.utc).isoformat()
        conn = _get_conn()
        row = conn.execute(
            "SELECT SUM(pnl) FROM islem_log WHERE etiket='CHALLENGE_MODE' AND zaman >= ?",
            (dt_iso,)
        ).fetchone()
        conn.close()
        if row and row[0] is not None:
            return float(row[0])
        return 0.0
    except Exception as e:
        print(f"⚠️ Challenge PNL Doğrulama Hatası: {e}")
        return 0.0


def evo_islemler_getir(limit: int = 200) -> list:
    """Evolutionary Trainer için genişletilmiş işlem verileri (RSI, Bollinger, hacim dahil)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT zaman, sembol, tip, giris_fiyati, cikis_fiyati,
                      pnl, pnl_pct, kaldirac, margin, neden,
                      rsi, bollinger_ust, bollinger_alt, hacim_oran
               FROM islem_log
               WHERE etiket = 'EVO_TRAINER'
               ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
        conn.close()
        return [
            {"zaman": r[0], "sembol": r[1], "tip": r[2],
             "giris": r[3], "cikis": r[4], "pnl": r[5],
             "pnl_pct": r[6], "kaldirac": r[7], "margin": r[8],
             "neden": r[9], "rsi": r[10], "bollinger_ust": r[11],
             "bollinger_alt": r[12], "hacim_oran": r[13]}
            for r in rows
        ]
    except Exception:
        return []
