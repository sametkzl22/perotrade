"""
Persistent State Manager
========================
Bot durumunu persistent_state.json dosyasında saklar.
PC yeniden başladığında bot kaldığı yerden devam eder.
Bileşik faiz (compounding) mantığını yönetir.
"""

import json
import os
import sys
import base64
from datetime import datetime, timezone

def get_app_path():
    """PyInstaller EXE uyumluluğu: Çalışma dizinini bulur."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

STATE_FILE = os.path.join(get_app_path(), "persistent_state.json")

def encode_key(key: str) -> str:
    """API anahtarlarını basit base64 ile şifreler (gizler)."""
    if not key: return ""
    return base64.b64encode(key.encode('utf-8')).decode('utf-8')

def decode_key(encoded_key: str) -> str:
    """Base64 şifreli API anahtarını çözer."""
    if not encoded_key: return ""
    try:
        return base64.b64decode(encoded_key.encode('utf-8')).decode('utf-8')
    except:
        return ""

DEFAULT_STATE = {
    "bakiye": 10.0,
    "baslangic_bakiye": 10.0,
    "hedef_bakiye": 100.0,
    "gun_baslangic_bakiye": 10.0,  # O günün başlangıç bakiyesi (compounding için)
    "gunluk_hedef_pct": 10.0,
    "son_gun": "",  # YYYY-MM-DD formatında son aktif gün
    "toplam_islem_sayisi": 0,
    "toplam_kar": 0.0,
    "aktif_pozisyonlar": {},
    "islem_gecmisi": [],
    "cuzdan_gecmisi": [],
    "max_drawdown": 0.0,
    "pik_bakiye": 10.0,
    "gun_sayaci": 0,  # Kaç gündür çalışıyor
    "api_key_enc": "",    # Base64 şifreli API Key
    "api_secret_enc": "", # Base64 şifreli Secret Key
    "use_real_api": False,
    
    # --- DEMO MODU DEĞİŞKENLERİ ---
    "Demo_Bakiye": 100.0,
    "demo_aktif_pozisyonlar": {},
    "demo_islem_gecmisi": [],
    "demo_baslangic_zamani": 0.0,
    "demo_gun_baslangic": 100.0,
    "demo_pik_bakiye": 100.0,
    "demo_cuzdan_gecmisi": [],
    "demo_max_drawdown": 0.0,
}


def state_yukle(dosya: str = STATE_FILE) -> dict:
    """JSON dosyasından state yükler. Yoksa default oluşturur."""
    if os.path.exists(dosya):
        try:
            with open(dosya, "r", encoding="utf-8") as f:
                state = json.load(f)
            
            # --- DEMO MODU YÖNLENDİRMESİ ---
            # Eğer sahte (demo) modundaysak, ana motorun çökmemesi için Demo değerlerini genel anahtarlara aktar.
            if not state.get("use_real_api", False):
                state["bakiye"] = state.get("Demo_Bakiye", 100.0)
                state["baslangic_bakiye"] = 100.0 # Demo baslangic
                state["gun_baslangic_bakiye"] = state.get("demo_gun_baslangic", 100.0)
                state["aktif_pozisyonlar"] = state.get("demo_aktif_pozisyonlar", {})
                state["islem_gecmisi"] = state.get("demo_islem_gecmisi", [])
                state["cuzdan_gecmisi"] = state.get("demo_cuzdan_gecmisi", [])
                state["max_drawdown"] = state.get("demo_max_drawdown", 0.0)
                state["pik_bakiye"] = state.get("demo_pik_bakiye", 100.0)
                state["baslangic_zamani"] = state.get("demo_baslangic_zamani", 0.0)
                print(f"🎮 DEMO Modu Yüklendi! (Sanal Bakiye: ${state.get('bakiye'):.2f})")
            else:
                print(f"💰 REAL Mod Yüklendi! (Bakiye: ${state.get('bakiye', 0):.2f})")
            
            # Yeni gün kontrolü (Compounding)
            bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if state.get("son_gun", "") != bugun:
                eski_gun = state.get("son_gun", "İlk Gün")
                mevcut_bakiye = state.get("bakiye", 100.0)
                state["gun_baslangic_bakiye"] = mevcut_bakiye
                state["son_gun"] = bugun
                state["gun_sayaci"] = state.get("gun_sayaci", 0) + 1
                state_kaydet(state, dosya)
            
            return state
        except Exception as e:
            print(f"⚠️ State dosyası okunamadı: {e}. Varsayılan oluşturuluyor.")
    
    # Yeni state oluştur
    state = DEFAULT_STATE.copy()
    state["son_gun"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_kaydet(state, dosya)
    return state


def state_kaydet(state: dict, dosya: str = STATE_FILE):
    """State'i JSON dosyasına kaydeder. Demo ve Real verileri birbirini ezmemesi için korur."""
    try:
        eski_kayit = {}
        if os.path.exists(dosya):
            with open(dosya, "r", encoding="utf-8") as f:
                eski_kayit = json.load(f)
                
        # Serializables
        kayit = {}
        for k, v in state.items():
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                kayit[k] = v
                
        is_demo = not kayit.get("use_real_api", False)
        
        # DEMO modunda isek, memory'deki "bakiye" vb. aslında SANAL paradır.
        if is_demo:
            kayit["Demo_Bakiye"] = kayit.get("bakiye", 100.0)
            kayit["demo_aktif_pozisyonlar"] = kayit.get("aktif_pozisyonlar", {})
            kayit["demo_islem_gecmisi"] = kayit.get("islem_gecmisi", [])
            kayit["demo_cuzdan_gecmisi"] = kayit.get("cuzdan_gecmisi", [])
            kayit["demo_gun_baslangic"] = kayit.get("gun_baslangic_bakiye", 100.0)
            kayit["demo_pik_bakiye"] = kayit.get("pik_bakiye", 100.0)
            kayit["demo_max_drawdown"] = kayit.get("max_drawdown", 0.0)
            kayit["demo_baslangic_zamani"] = kayit.get("baslangic_zamani", 0.0)
            
            # JSON'daki gerçek (Real) parayı memory'deki sanal parayla ezmemek için dosyadan geri yükle
            for key in ["bakiye", "baslangic_bakiye", "gun_baslangic_bakiye", "aktif_pozisyonlar", "islem_gecmisi", "cuzdan_gecmisi", "max_drawdown", "pik_bakiye"]:
                if key in eski_kayit:
                    kayit[key] = eski_kayit[key]
        else:
            # REAL moddaysak, memory'deki veriler gerçektir. JSON'daki Demo verilerini koru.
            for key in ["Demo_Bakiye", "demo_aktif_pozisyonlar", "demo_islem_gecmisi", "demo_cuzdan_gecmisi", "demo_gun_baslangic", "demo_pik_bakiye", "demo_max_drawdown", "demo_baslangic_zamani"]:
                if key in eski_kayit:
                    kayit[key] = eski_kayit[key]
        
        with open(dosya, "w", encoding="utf-8") as f:
            json.dump(kayit, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ State kaydetme hatası: {e}")


def gunluk_kar_pct(state: dict) -> float:
    """Bugünkü kâr yüzdesini hesaplar."""
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", 10.0))
    if gun_baslangic <= 0:
        return 0.0
    mevcut = state.get("bakiye", 0)
    return ((mevcut - gun_baslangic) / gun_baslangic) * 100


def bilesik_faiz_hedef(state: dict) -> float:
    """Bugünkü bileşik faiz hedefini hesaplar."""
    gun_baslangic = state.get("gun_baslangic_bakiye", state.get("baslangic_bakiye", 10.0))
    hedef_pct = state.get("gunluk_hedef_pct", 10.0)
    return gun_baslangic * (1 + hedef_pct / 100)


def gun_sonu_raporu(state: dict) -> str:
    """Gün sonu özet raporu üretir."""
    kar = gunluk_kar_pct(state)
    gun = state.get("gun_sayaci", 0)
    bakiye = state.get("bakiye", 0)
    baslangic = state.get("baslangic_bakiye", 10.0)
    toplam_buyume = ((bakiye - baslangic) / baslangic * 100) if baslangic > 0 else 0
    
    return (
        f"📊 GÜN #{gun} RAPORU\n"
        f"├─ Başlangıç: ${state.get('gun_baslangic_bakiye', 10):.2f}\n"
        f"├─ Kapanış: ${bakiye:.2f}\n"
        f"├─ Günlük Kâr: %{kar:+.2f}\n"
        f"├─ Toplam Büyüme: %{toplam_buyume:+.2f}\n"
        f"└─ İşlem Sayısı: {state.get('toplam_islem_sayisi', 0)}"
    )
