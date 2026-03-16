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
    "use_real_api": False
}


def state_yukle(dosya: str = STATE_FILE) -> dict:
    """JSON dosyasından state yükler. Yoksa default oluşturur."""
    if os.path.exists(dosya):
        try:
            with open(dosya, "r", encoding="utf-8") as f:
                state = json.load(f)
            print(f"✅ Kalıcı hafıza yüklendi: {dosya} (Bakiye: ${state.get('bakiye', 0):.2f})")
            
            # Yeni gün kontrolü (Compounding)
            bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if state.get("son_gun", "") != bugun:
                eski_gun = state.get("son_gun", "İlk Gün")
                # Bileşik faiz: Dünün bakiyesini bugünün başlangıcı yap
                mevcut_bakiye = state.get("bakiye", 10.0)
                state["gun_baslangic_bakiye"] = mevcut_bakiye
                state["son_gun"] = bugun
                state["gun_sayaci"] = state.get("gun_sayaci", 0) + 1
                print(f"📅 Yeni gün başladı! ({eski_gun} → {bugun})")
                print(f"💰 Bileşik Faiz: Bugünün başlangıç bakiyesi = ${mevcut_bakiye:.2f}")
                print(f"🎯 Bugünün hedefi: ${mevcut_bakiye * 1.10:.2f} (+%10)")
                state_kaydet(state, dosya)
            
            return state
        except Exception as e:
            print(f"⚠️ State dosyası okunamadı: {e}. Varsayılan oluşturuluyor.")
    
    # Yeni state oluştur
    state = DEFAULT_STATE.copy()
    state["son_gun"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_kaydet(state, dosya)
    print(f"🆕 Yeni persistent state oluşturuldu: {dosya}")
    return state


def state_kaydet(state: dict, dosya: str = STATE_FILE):
    """State'i JSON dosyasına kaydeder."""
    try:
        # Kaydetmeden önce serializable olmayan objeleri temizle
        kayit = {}
        for k, v in state.items():
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                kayit[k] = v
        
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
