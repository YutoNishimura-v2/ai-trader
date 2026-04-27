# HFM demo on Beeks VPS (Windows)

This guide wires the existing **`python -m ai_trader.scripts.run_demo`** path to a
**MetaTrader 5** terminal on your **Windows Server 2022** VPS. It does **not**
execute trades from this Linux repository — you run Python **on the VPS**.

## Security first

1. **Your email contained the VPS Administrator password.** Treat it as **compromised**
   because it was pasted into chat. **Change it on first RDP login** (Windows will prompt).
2. **Never commit** MT5 passwords, investor passwords, or account numbers you care about.
   Use environment variables (see `.env.example`).
3. The **`185.63.220.176:5296`** form is **host:custom RDP port** — use it in your RDP client
   if the downloaded `.rdp` does not already.

## One-time VPS setup

1. **RDP** into the VPS (Administrator; change password immediately).
2. Install **MetaTrader 5** from your broker (HFM) and **log into your demo account**
   once in the terminal (Tools → Options → confirm **Algo Trading** / **Allow automated trading**
   per MT5 version).
3. Install **Python 3.11+** for Windows (from python.org), check **“Add to PATH”**.
4. Copy this repository to the VPS (git clone, zip, etc.).
5. In the repo folder:

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[live]"
   ```

   The `live` extra installs `MetaTrader5` on Windows only.

6. Copy `config/live_demo_hfm.template.yaml` to e.g. `config/live_local.yaml` (gitignored pattern),
   set **`broker.account`** to your **MT5 demo login number** and **`broker.server`** to the
   exact **server string** from MT5 (e.g. `HFMarketsGlobal-Demo` — verify in terminal).

7. Set the MT5 password in the environment (PowerShell example):

   ```powershell
   setx AI_TRADER_MT5_PASSWORD "your_mt5_trading_password"
   ```

   Open a **new** terminal so `setx` takes effect, activate venv, then verify the Python
   API can see the terminal (does not open trades):

   ```bat
   python -m ai_trader.scripts.mt5_connectivity_check --config config\live_local.yaml
   ```

   Then start the runner:

   ```bat
   python -m ai_trader.scripts.run_demo --config config\live_local.yaml
   ```

   For a short smoke test, `max_iterations: 50` is set in the template so the process stops
   after 50 new bars; set to `null` for continuous operation.

## How credentials flow

| YAML key | Purpose |
|----------|---------|
| `broker.account` | MT5 login number |
| `broker.server` | Broker server name |
| `broker.password_env` | Name of env var holding MT5 password (default `AI_TRADER_MT5_PASSWORD`) |
| `broker.mt5_terminal_path_env` | Optional env var with full path to `terminal64.exe` |

`run_demo` passes `path=` to `MetaTrader5.initialize()` when `terminal_path` is set so the
Python API attaches to the correct terminal on multi-install systems.

## Operational notes

- **Symbols**: ensure **XAUUSD** is visible in Market Watch; the bot calls `symbol_select`.
- **Timezone**: strategies use **UTC** session logic; VPS in London is fine.
- **News blackout**: if `news.csv` is missing on the VPS, either copy `data/news/` or set
  `news.csv: null` in your local YAML to disable blackout until files are present.
- **Logs**: `LiveRunner` uses the project logger; run from a console or redirect to a file.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `MetaTrader5 package not available` | You are not on Windows or did not `pip install -e ".[live]"`. |
| `initialize failed` | MT5 not running, wrong `terminal64.exe` path, or demo not logged in. |
| No trades | Strategy returns `None` most bars; reduce filters or confirm session hours. |
| Orders rejected | RiskManager caps; align `starting_balance` with actual equity on demo. |
