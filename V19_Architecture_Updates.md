# V19 Architecture Updates Plan

This plan details the implementation of the V19 architectural updates based on user preferences.

## Proposed Changes

### Configuration & UI
#### [MODIFY] config.py
- Add `MAX_WALLET_RISK_PCT` (default: 100)
- Add `TRADE_RISK_PCT` (default: 10)

#### [MODIFY] streamlit_app.py
- Add Sliders for `MAX_WALLET_RISK_PCT` (0-100%) and `TRADE_RISK_PCT` (0-50%).
- Ensure `st.empty()` price containers only update when the price value inherently changes to prevent UI freezing.
- Add AI Engine missing warning UI logic if the AI model is mock ('⚠️ ML Modeli Eksik: Mock AI Moduna Geçildi').

### Core Bot Logic
#### [MODIFY] bot_worker.py
- Refactor the margin allocation calculation. The usable balance will be limited by `MAX_WALLET_RISK_PCT`, and individual trade size will be limited by `TRADE_RISK_PCT` against the total available capital, never injecting the full balance.

#### [MODIFY] data_provider.py
- Update `watch_tickers` to include a 3-try reconnection circuit.
- If it fails 3 times, catch `ccxt.NetworkError` or `websockets.exceptions.ConnectionClosed`/`ConnectionResetError`, sleep for 5 minutes (`await asyncio.sleep(300)`), and then restart the retry counter.

### Database & State Security
#### [MODIFY] persistent_state.py
- Integrate `dotenv` to pull `FERNET_KEY`.
- In decryption, if the key is missing or invalid (`InvalidToken`), forcefully log the error and invoke `sys.exit(1)` to prevent bot initialization with corrupted/inaccessible state.

#### [MODIFY] data_logger.py
- Add `isolation_level='EXCLUSIVE'` to `sqlite3.connect` to ensure total atomic locking for thread safety during heavy write operations.

### AI Model Management
#### [MODIFY] ai_engine.py
- Handle `FileNotFoundError` explicitly for `xgb_model.joblib`. If caught, flag the model as `"MOCK"` internally without crashing.

#### [MODIFY] train_model.py
- Implement logic enforcing that if `trade_logs.db` successfully lists over 10 trades, a baseline model should dynamically generate and be saved.

### Project-wide Cleanup
- Review all specified files.
- Replace generic `except Exception as e:` statements with specific errors (`ccxt.NetworkError`, `ccxt.ExchangeError`, `sqlite3.Error`, `asyncio.TimeoutError`) and print explicit type logs to terminal.

## Verification Plan
### Automated Tests
- Run `python scripts/checklist.py .` or other validation tools to ensure the codebase remains clean.
### Manual Verification
- Test UI sliders to ensure values update correctly.
- Disconnect internet manually to trigger 3x auto-reconnect fallback mechanism log.
- Attempt to start with an invalid `FERNET_KEY` and confirm the bot stops gracefully with an explicit error.
