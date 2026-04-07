"""
Persistent State Manager — Streamlit Cloud Optimized
=====================================================
Bot durumunu persistent_state.json dosyasında saklar.
Streamlit Cloud'da dosya sistemi geçici (ephemeral) olduğundan,
st.cache_resource ile Global In-Memory yedek tutar.

Güvenlik Katmanları:
  1. Güvenli Okuma  → Dosya yoksa / bozuksa varsayılan demo değerlerini döndürür.
  2. Hata Yakalama  → json.load & json.dump geniş try-except ile korunur.
  3. NoneType Guard → Yüklenen her anahtar var mı kontrolü, yoksa default atanır.
  4. Dosya Kilidi   → Yazma izolasyonu: PermissionError / IOError yakalanır.
"""

import json
import os
import sqlite3
import ccxt
import sys
import base64
import tempfile
import shutil
import time
from datetime import datetime, timezone
import settings_manager
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

# Load environment initially
try:
    load_dotenv()
except (ccxt.BaseError, sqlite3.Error, Exception):
    pass

# ──────────────────────────────────────────────
# STREAMLIT CLOUD IN-MEMORY YEDEK
# ──────────────────────────────────────────────
try:
    import streamlit as st

    @st.cache_resource
    def _get_cloud_memory():
        return {}
except ImportError:
    st = None

_local_memory = {}


def get_memory():
    """Streamlit varsa cache_resource'tan, yoksa modül-seviye dict'ten döner."""
    if st is not None:
        try:
            return _get_cloud_memory()
        except (ccxt.BaseError, sqlite3.Error, Exception):
            pass
    return _local_memory


# ──────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ──────────────────────────────────────────────
def get_app_path():
    """PyInstaller EXE uyumluluğu: Çalışma dizinini bulur."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_lock_file_path() -> str:
    """Heartbeat lock dosyasının yolunu döndürür.
    Bot çalışırken oluşturulur, iradeli durdurmada silinir.
    Beklenmedik kapanma sonrası dosya yerinde kalır → bootstrap oto-başlatma yapar.
    """
    lock_dir = os.path.join(get_app_path(), "data")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, "active_session.lock")


def set_last_mode(is_real: bool):
    """En son aktif olan modu global last_mode.json dosyasına mühürler."""
    last_mode_file = os.path.join(get_app_path(), "data", "last_mode.json")
    os.makedirs(os.path.dirname(last_mode_file), exist_ok=True)
    try:
        with open(last_mode_file, "w", encoding="utf-8") as f:
            json.dump({"use_real_api": is_real}, f)
    except Exception:
        pass

def get_last_mode() -> bool:
    """En son aktif olan modu okur. Yoksa settings_manager (eski) değerini döner."""
    last_mode_file = os.path.join(get_app_path(), "data", "last_mode.json")
    if not os.path.exists(last_mode_file):
        return settings_manager.is_real_mode_active()
    try:
        with open(last_mode_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("use_real_api", False)
    except Exception:
        return False

def get_state_file() -> str:
    """Moda göre izole dosya yolunu döner."""
    base_dir = get_app_path()
    is_real = get_last_mode()
    folder_name = "real" if is_real else "demo"
    file_name = "real_state.json" if is_real else "demo_state.json"
    
    folder_path = os.path.join(base_dir, "data", folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return os.path.join(folder_path, file_name)

def get_master_key() -> str:
    env_path = os.path.join(get_app_path(), '.env')
    load_dotenv(env_path)
    key = os.environ.get("MASTER_KEY")
    if not key:
        key = Fernet.generate_key().decode('utf-8')
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\nMASTER_KEY={key}\n")
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            print(f"⚠️ Could not write MASTER_KEY to .env: {e}")
        os.environ["MASTER_KEY"] = key
    return key

_fernet = None
def get_fernet():
    global _fernet
    if _fernet is None:
        try:
            _fernet = Fernet(get_master_key().encode('utf-8'))
        except (ccxt.BaseError, sqlite3.Error, Exception) as e:
            print(f"🚨 [GÜVENLİK] Fernet başlatılamadı ({e}). Veri koruması için sistem durduruluyor!")
            sys.exit(1)
    return _fernet

def encode_key(key: str) -> str:
    if not key:
        return ""
    try:
        return get_fernet().encrypt(key.encode('utf-8')).decode('utf-8')
    except (ccxt.BaseError, sqlite3.Error, Exception) as e:
        print(f"⚠️ Encryption error: {e}")
        return base64.b64encode(key.encode('utf-8')).decode('utf-8')


def decode_key(encoded_key: str) -> str:
    if not encoded_key:
        return ""
    try:
        return get_fernet().decrypt(encoded_key.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        warning_msg = "Kritik Hata: Veri şifresi çözülemedi, lütfen MASTER_KEY kontrolü yapın"
        print(f"🚨 [GÜVENLİK İHLALİ] {warning_msg}")
        if st is not None:
            try:
                st.error(warning_msg)
            except Exception:
                pass
        return ""
    except (ccxt.BaseError, sqlite3.Error, Exception) as e:
        print(f"⚠️ Decryption error: {e}")
        try:
            return base64.b64decode(encoded_key.encode('utf-8')).decode('utf-8')
        except (ccxt.BaseError, sqlite3.Error, Exception):
            return ""


# ──────────────────────────────────────────────
# VARSAYILAN STATE
# ──────────────────────────────────────────────
DEFAULT_STATE = {
    "bakiye": 100.0,
    "baslangic_bakiye": 100.0,
    "hedef_bakiye": 1000.0,
    "gun_baslangic_bakiye": 100.0,
    "gunluk_hedef_pct": 10.0,
    "son_gun": "",
    "toplam_islem_sayisi": 0,
    "toplam_kar": 0.0,
    "aktif_pozisyonlar": {},
    "islem_gecmisi": [],
    "cuzdan_gecmisi": [],
    "max_drawdown": 0.0,
    "pik_bakiye": 100.0,
    "gun_sayaci": 0,
    "api_key_enc": "",
    "api_secret_enc": "",
    "use_real_api": False,

    # --- DEMO MODU ---
    "Demo_Bakiye": 100.0,
    "demo_aktif_pozisyonlar": {},
    "demo_islem_gecmisi": [],
    "demo_baslangic_zamani": 0.0,
    "demo_gun_baslangic": 100.0,
    "demo_pik_bakiye": 100.0,
    "demo_cuzdan_gecmisi": [],
    "demo_max_drawdown": 0.0,

    # --- 94-Day Challenge (İzole) ---
    "challenge_session": {
        "aktif": False,
        "baslangic_bakiye": 10.0,
        "bakiye": 10.0,
        "gun_baslangic_bakiye": 10.0,
        "pik_bakiye": 10.0,
        "current_day": 1,
        "target_achieved": False,
        "accumulated_pnl": 0.0,
        "baslangic_zamani": 0.0,
        "gun_baslangic_zamani": 0.0,
        "toplam_islem": 0,
        "gunluk_pik_kar_pct": 0.0,
        "trailing_stop_seviyesi": 0.0,
        "islem_gecmisi": [],
        "cuzdan_gecmisi": [],
        "max_drawdown": 0.0,
    },
}


def _ensure_keys(state: dict) -> dict:
    """
    NoneType Guard — State sözlüğündeki her beklenen anahtarı kontrol eder,
    eksik veya None olan değerlere varsayılanı atar.
    """
    for key, default_val in DEFAULT_STATE.items():
        if key not in state or state[key] is None:
            state[key] = (
                default_val.copy() if isinstance(default_val, (dict, list)) else default_val
            )
        # Tip uyumsuzluğu kontrolü (örn. dict beklenirken str geldi)
        if isinstance(default_val, dict) and not isinstance(state[key], dict):
            state[key] = {}
        elif isinstance(default_val, list) and not isinstance(state[key], list):
            state[key] = []
    return state


# ──────────────────────────────────────────────
# GÜVENLİ OKUMA
# ──────────────────────────────────────────────
def _safe_read_json(dosya: str) -> dict | None:
    """
    JSON dosyasını güvenle okur.
    Dosya yoksa, bozuksa veya erişim reddedilirse None döner.
    """
    if not os.path.exists(dosya):
        return None

    try:
        with open(dosya, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
            if not isinstance(data, dict):
                print("⚠️ State dosyası dict değil, varsayılana dönülüyor.")
                return None
            return data
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"⚠️ State dosyası bozuk (corrupt): {e}")
        return None
    except (PermissionError, IOError, OSError) as e:
        print(f"⚠️ State dosyası okunamadı (izin hatası): {e}")
        return None
    except (ccxt.BaseError, sqlite3.Error, Exception) as e:
        print(f"⚠️ State dosyası okunamadı (bilinmeyen): {e}")
        return None


# ──────────────────────────────────────────────
# GÜVENLİ YAZMA (Atomic Write + İzolasyon)
# ──────────────────────────────────────────────
def _safe_write_json(data: dict, dosya: str) -> bool:
    """
    Atomic write: Önce geçici dosyaya yazar, ardından hedef dosyayla yer değiştirir.
    Böylece yazma ortasında çökme durumunda bile dosya bozulmaz.
    PermissionError / IOError yakalanır.
    Başarılı ise True, değilse False döner.
    """
    try:
        dir_path = os.path.dirname(dosya) or "."
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                json.dump(data, tmp_f, indent=2, ensure_ascii=False)
            shutil.move(tmp_path, dosya)
            return True
        except (ccxt.BaseError, sqlite3.Error, Exception):
            # Geçici dosya temizliği
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except (PermissionError, IOError, OSError) as e:
        print(f"❌ State yazılamadı (izin/dosya hatası): {e}")
        return False
    except (TypeError, ValueError) as e:
        print(f"❌ State JSON serialization hatası: {e}")
        return False
    except (ccxt.BaseError, sqlite3.Error, Exception) as e:
        print(f"❌ State kaydetme hatası: {e}")
        return False


# ──────────────────────────────────────────────
# ANA FONKSİYONLAR
# ──────────────────────────────────────────────
def state_yukle(dosya: str = None) -> dict:
    """
    State yükler (3 katmanlı fallback):
      1. Disk (data/demo/demo_state.json veya data/real/real_state.json)
      2. Bulut Hafızası (st.cache_resource)
      3. Varsayılan default değerler
    """
    if dosya is None:
        dosya = get_state_file()

    memory = get_memory()

    # ─── KATMAN 1: Disk ───
    state = _safe_read_json(dosya)

    if state is not None:
        state = _ensure_keys(state)

        # Mod bilgisi last_mode.json'dan (tek doğru kaynak)
        is_real = get_last_mode()
        state["use_real_api"] = is_real

        if not is_real:
            print(f"🎮 DEMO Modu Yüklendi! (Sanal Bakiye: ${state.get('bakiye', 0):.2f})")
        else:
            print(f"💰 REAL Mod Yüklendi! (Bakiye: ${state.get('bakiye', 0):.2f})")

        # v7: 24 Saatlik Döngü -> baslangic_zamani ile sıkı kontrol (86400s)
        baslangic_z = state.get("baslangic_zamani", 0)
        simdi = time.time()
        
        # İlk başlangıç için zaman belirle
        if baslangic_z == 0:
            state["baslangic_zamani"] = simdi
            state["son_gun"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # v8 24 Saat dolduysa Temiz Reset & Compounding
        elif (simdi - baslangic_z) >= 86400:
            print(f"🔄 24S Döngü Doldu (Load Anı). Kâr/Zarar base bakiyeye eklendi.")
            state["gun_baslangic_bakiye"] = state.get("bakiye", 100.0)
            state["baslangic_bakiye"] = state.get("bakiye", 100.0)
            state["pik_bakiye"] = state.get("bakiye", 100.0)
            state["baslangic_zamani"] = simdi
            state["son_gun"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            state["gun_sayaci"] = state.get("gun_sayaci", 0) + 1
            state["gunluk_pik_kar"] = 0.0
            state["is_breakout"] = False  # Berserker'dan çık
            state["martingale_ardisik_kayip"] = 0
            state["martingale_carpan"] = 1.0
            state["islem_gecmisi"] = []
            state["toplam_islem_sayisi"] = 0

            # v9/v10: Challenge mod 24h bileşik reset
            ch = state.get("challenge_session", {})
            if isinstance(ch, dict) and ch.get("aktif"):
                ch_bakiye = ch.get("bakiye", 10.0)
                ch["gun_baslangic_bakiye"] = ch_bakiye
                ch["pik_bakiye"] = max(ch.get("pik_bakiye", ch_bakiye), ch_bakiye)
                ch["current_day"] = ch.get("current_day", 1) + 1
                ch["gun_baslangic_zamani"] = simdi
                ch["target_achieved"] = False
                ch["gunluk_pik_kar_pct"] = 0.0
                ch["trailing_stop_seviyesi"] = 0.0
                ch["islem_gecmisi"] = []
                state["challenge_session"] = ch

            state_kaydet(state, dosya)
        else:
            # Dışarıdan Bakiye Ekleme Kontrolü
            mevcut = state.get("bakiye", 0.0)
            baslangic = state.get("gun_baslangic_bakiye", 0.0)
            if baslangic > 0 and ((mevcut - baslangic) / baslangic) >= 0.50:
                print(f"🔄 Manuel bakiye artışı algılandı! (Gün başlangıç bakiyesi artık gün ortasında DÜŞÜRÜLMÜYOR/EŞİTLENMİYOR).")
                # Hedefe erken varılmasını engelliyordu, bu yüzden gün sonuna kadar sabit bırakıldı:
                # state["gun_baslangic_bakiye"] = mevcut
                # state_kaydet(state, dosya)


        memory["last_state"] = state
        return state

    # ─── KATMAN 2: Bulut Hafızası (In-Memory) ───
    if "last_state" in memory and isinstance(memory["last_state"], dict):
        print("☁️ Disk silinmiş ama Bulut Hafızası bulundu! Veriler kurtarıldı.")
        kurtarilan = _ensure_keys(memory["last_state"])
        state_kaydet(kurtarilan, dosya)
        return kurtarilan

    # ─── KATMAN 3: Varsayılan ───
    print("🆕 İlk çalıştırma: Varsayılan demo state oluşturuluyor.")
    state = DEFAULT_STATE.copy()
    state["son_gun"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_kaydet(state, dosya)
    memory["last_state"] = state
    return state


def state_kaydet(state: dict, dosya: str = None):
    """
    State'i diske yazar (atomic).
    Dosya belirtilmemişse aktif moda göre doğru klasörü bulur.
    Her yazma işlemi aynı zamanda Bulut Hafızasına yedeklenir.
    """
    if dosya is None:
        dosya = get_state_file()

    if not isinstance(state, dict):
        print("❌ state_kaydet: Geçersiz state tipi, kaydetme iptal.")
        return

    try:
        # Serializable filtreleme
        kayit = {}
        for k, v in state.items():
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                kayit[k] = v

        kayit = _ensure_keys(kayit)
        kayit["use_real_api"] = get_last_mode()

        # Atomic write (güvenli)
        success = _safe_write_json(kayit, dosya)

        # Bulut Hafızasına her zaman yedekle
        memory = get_memory()
        memory["last_state"] = kayit

        if not success:
            print("⚠️ Diske yazılamadı ama Bulut Hafızasına yedeklendi.")

    except (ccxt.BaseError, sqlite3.Error, Exception) as e:
        # Son savunma hattı
        print(f"❌ state_kaydet kritik hata: {e}")
        try:
            memory = get_memory()
            memory["last_state"] = state
            print("💾 Kritik hata sonrası Bulut Hafızasına yedeklendi.")
        except (ccxt.BaseError, sqlite3.Error, Exception):
            pass


# ──────────────────────────────────────────────
# YARDIMCI HESAPLAMALAR
# ──────────────────────────────────────────────
def gunluk_kar_pct(state: dict) -> float:
    """Bugünkü kâr yüzdesini hesaplar."""
    if not isinstance(state, dict):
        return 0.0
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", 100.0))
    if not isinstance(gun_baslangic, (int, float)) or gun_baslangic <= 0:
        return 0.0
    mevcut = state.get("bakiye", 0)
    if not isinstance(mevcut, (int, float)):
        return 0.0
    return ((mevcut - gun_baslangic) / gun_baslangic) * 100


def bilesik_faiz_hedef(state: dict) -> float:
    """Bugünkü bileşik faiz hedefini hesaplar."""
    if not isinstance(state, dict):
        return 0.0
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", 100.0))
    hedef_pct = state.get("gunluk_hedef_pct", 10.0)
    if not isinstance(gun_baslangic, (int, float)):
        gun_baslangic = 100.0
    if not isinstance(hedef_pct, (int, float)):
        hedef_pct = 10.0
    return gun_baslangic * (1 + hedef_pct / 100)


def gun_sonu_raporu(state: dict) -> str:
    """Gün sonu özet raporu üretir."""
    if not isinstance(state, dict):
        return "⚠️ Geçersiz state — rapor üretilemedi."
    kar = gunluk_kar_pct(state)
    gun = state.get("gun_sayaci", 0) or 0
    bakiye = state.get("bakiye", 0) or 0
    baslangic = state.get("baslangic_bakiye", 100.0) or 100.0
    toplam_buyume = ((bakiye - baslangic) / baslangic * 100) if baslangic > 0 else 0

    return (
        f"📊 GÜN #{gun} RAPORU\n"
        f"├─ Başlangıç: ${state.get('gun_baslangic_bakiye', 100):.2f}\n"
        f"├─ Kapanış: ${bakiye:.2f}\n"
        f"├─ Günlük Kâr: %{kar:+.2f}\n"
        f"├─ Toplam Büyüme: %{toplam_buyume:+.2f}\n"
        f"└─ İşlem Sayısı: {state.get('toplam_islem_sayisi', 0)}"
    )


def challenge_sifirla(state: dict, dosya: str = None) -> dict:
    """
    v10: Challenge verilerini SADECE .json dosyasında sıfırlar.
    ⚠️ trade_logs.db'ye KESİNLİKLE DOKUNMAZ — AI eğitim verileri korunur.
    
    Döndürdüğü state, sıfırlanmış challenge_session içerir.
    """
    import time as _time

    ch_yeni = {
        "aktif": True,
        "baslangic_bakiye": state.get("challenge_session", {}).get("baslangic_bakiye", 10.0),
        "gun_baslangic_bakiye": state.get("challenge_session", {}).get("baslangic_bakiye", 10.0),
        "bakiye": state.get("challenge_session", {}).get("baslangic_bakiye", 10.0),
        "pik_bakiye": state.get("challenge_session", {}).get("baslangic_bakiye", 10.0),
        "current_day": 1,
        "target_achieved": False,
        "accumulated_pnl": 0.0,
        "baslangic_zamani": _time.time(),
        "gun_baslangic_zamani": _time.time(),
        "toplam_islem": 0,
        "gunluk_pik_kar_pct": 0.0,
        "trailing_stop_seviyesi": 0.0,
        "islem_gecmisi": [],
        "cuzdan_gecmisi": [],
        "max_drawdown": 0.0,
    }

    state["challenge_session"] = ch_yeni
    state_kaydet(state, dosya)
    print(f"🔄 Challenge sıfırlandı (sadece .json). trade_logs.db korunuyor.")
    return state
