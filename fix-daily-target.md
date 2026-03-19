# fix-daily-target

## Goal
Fix the early stop "Hedefe Ulaşıldı" bug by implementing balance sync, flexible target for Ultra-Scalper, DB verification, and Dashboard UI updates.

## Tasks
- [x] Task 1: Fix `initial_balance` sync in `persistent_state.py` → Verify: `gun_baslangic_bakiye` remains locked for the day.
- [x] Task 2: Implement `gunluk_gercek_pnl_getir` in `data_logger.py` → Verify: Queries `trade_logs.db` for today's PNL.
- [x] Task 3: Update `bot_worker.py` for DB verification and Ultra-Scalper bypass → Verify: Bot logs "Günlük Hedef Aşıldı..." in Ultra-Scalper instead of stopping. Includes DB check before stop.
- [x] Task 4: Update `streamlit_app.py` UI for "Kalan Hedef" → Verify: Dashboard shows "Hedef Tamamlandı (Extra Kârda)" properly.

## Done When
- [ ] All 4 conditions specified by the user are met and verified manually and via checklist scripts.
