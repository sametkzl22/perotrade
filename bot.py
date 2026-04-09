"""
PeroTrade Pro — Master Entry Point (Engine-First Architecture)
==============================================================
Sistemin tek giriş noktası. Motor + Dashboard birlikte yönetilir.

Kullanım:
    python3 bot.py              # Motor + Dashboard başlat
    python3 bot.py --headless   # Sadece motor (UI yok)
"""

import signal
import sys
import os
import time
import subprocess
from pathlib import Path

import persistent_state as ps


# ─────────────────────────────────────────────
# Global Shutdown Flag
# ─────────────────────────────────────────────
_running = True


def _signal_handler(sig, frame):
    global _running
    print("\n⛔ Durdurma sinyali alındı. Graceful shutdown başlıyor...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─────────────────────────────────────────────
# IPC Flag Helpers
# ─────────────────────────────────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_APP_DIR, "data")
_STOP_FLAG = os.path.join(_DATA_DIR, "stop_signal.flag")
_START_FLAG = os.path.join(_DATA_DIR, "start_signal.flag")


def _check_stop_signal() -> bool:
    """Dashboard'dan gelen durdurma sinyalini kontrol eder."""
    if os.path.exists(_STOP_FLAG):
        try:
            os.remove(_STOP_FLAG)
        except OSError:
            pass
        return True
    return False


def _check_start_signal() -> bool:
    """Dashboard'dan gelen başlatma sinyalini kontrol eder."""
    if os.path.exists(_START_FLAG):
        try:
            os.remove(_START_FLAG)
        except OSError:
            pass
        return True
    return False


def _cleanup_flags():
    """Eski IPC flag dosyalarını temizle."""
    for f in [_STOP_FLAG, _START_FLAG]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


# ─────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────
def main():
    global _running
    headless = "--headless" in sys.argv or "--no-ui" in sys.argv

    print("=" * 60)
    print("  🤖 PeroTrade Pro — Engine-First Architecture")
    print("  Motor + Dashboard Orkestrasyon Sistemi")
    print("=" * 60)

    os.makedirs(_DATA_DIR, exist_ok=True)
    _cleanup_flags()

    # ── 1. BotWorker Engine başlat ──
    from bot_worker import BotWorker
    worker = BotWorker()
    if not worker.is_running:
        worker.start()
    print("✅ Trading Engine başlatıldı (arka plan thread)")

    # ── 2. Lock dosyası ──
    lock_path = ps.get_lock_file_path()
    Path(lock_path).touch()
    print(f"🔒 Lock: {lock_path}")

    # ── 3. Streamlit Dashboard ──
    streamlit_proc = None
    if not headless:
        try:
            streamlit_proc = subprocess.Popen(
                [
                    sys.executable, "-m", "streamlit", "run",
                    os.path.join(_APP_DIR, "streamlit_app.py"),
                    "--server.headless", "true",
                    "--server.port", "8501",
                    "--browser.gatherUsageStats", "false",
                ],
                cwd=_APP_DIR,
            )
            print(f"✅ Dashboard başlatıldı (PID: {streamlit_proc.pid})")
            print(f"   📊 http://localhost:8501")
        except Exception as e:
            print(f"⚠️ Dashboard başlatılamadı: {e}")
            print("   Motor headless modda çalışmaya devam ediyor.")
            streamlit_proc = None
    else:
        print("📋 Headless mod aktif — Dashboard yok")

    print("=" * 60)
    print("🤖 Sistem 7/24 çalışıyor. Durdurmak için Ctrl+C")
    print("=" * 60)

    # ── 4. Watchdog Döngüsü ──
    while _running:
        try:
            # Engine sağlık kontrolü
            engine_th = getattr(worker, "_engine_thread", None)
            if engine_th and not engine_th.is_alive() and worker.is_running:
                print("⚠️ Engine thread durmuş, yeniden başlatılıyor...")
                worker.start()

            # Dashboard sağlık kontrolü + yeniden başlatma
            if streamlit_proc and streamlit_proc.poll() is not None:
                print("⚠️ Dashboard kapandı. Yeniden başlatılıyor...")
                try:
                    streamlit_proc = subprocess.Popen(
                        [
                            sys.executable, "-m", "streamlit", "run",
                            os.path.join(_APP_DIR, "streamlit_app.py"),
                            "--server.headless", "true",
                            "--server.port", "8501",
                            "--browser.gatherUsageStats", "false",
                        ],
                        cwd=_APP_DIR,
                    )
                    print(f"✅ Dashboard yeniden başlatıldı (PID: {streamlit_proc.pid})")
                except Exception:
                    streamlit_proc = None

            # IPC: Dashboard'dan durdurma sinyali
            if _check_stop_signal():
                if worker.is_running:
                    print("🛑 Dashboard → Engine durduruluyor...")
                    worker.stop()
                    print("✅ Engine durduruldu (Dashboard açık — yeniden başlatılabilir)")

            # IPC: Dashboard'dan başlatma sinyali
            if _check_start_signal():
                if not worker.is_running:
                    print("▶️ Dashboard → Engine başlatılıyor...")
                    worker.start()
                    print("✅ Engine başlatıldı")

            time.sleep(3)

        except KeyboardInterrupt:
            break

    # ── 5. Graceful Shutdown ──
    print("\n" + "=" * 60)
    print("  ⛔ Graceful Shutdown")
    print("=" * 60)

    if worker.is_running:
        print("  🔧 Engine durduruluyor...")
        worker.stop()
        print("  ✅ Engine durduruldu")

    if streamlit_proc and streamlit_proc.poll() is None:
        print("  🖥️ Dashboard durduruluyor...")
        streamlit_proc.terminate()
        try:
            streamlit_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streamlit_proc.kill()
        print("  ✅ Dashboard durduruldu")

    # Lock dosyası temizliği
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except OSError:
        pass

    _cleanup_flags()

    print("=" * 60)
    print("  👋 Sistem güvenle kapatıldı.")
    print("=" * 60)


if __name__ == "__main__":
    main()
