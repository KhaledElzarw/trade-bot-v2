# tradebot (Binance Spot Testnet)

## Setup

1) Create `.env` (already done):
- `BINANCE_BASE_URL=https://testnet.binance.vision`
- `BINANCE_API_KEY=...`
- `BINANCE_API_SECRET=...`
- `BINANCE_SYMBOL=BTCUSDT`

2) Create venv + install deps:
```bash
cd /home/claw/.openclaw/workspace/tradebot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Smoke test
```bash
source .venv/bin/activate
python bot.py
```

Expected output:
- OK ping + server time
- Authenticated + balances count
- Klines OK + last close
