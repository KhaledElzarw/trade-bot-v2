import os
import time
import hmac
import hashlib
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


class BinanceSpotREST:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout_s: int = 10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def _sign(self, params: dict) -> str:
        qs = urlencode(params, doseq=True)
        sig = hmac.new(self.api_secret, qs.encode("utf-8"), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    def _get(self, path: str, params: dict | None = None, signed: bool = False):
        params = params or {}
        url = f"{self.base_url}{path}"
        if signed:
            params.setdefault("timestamp", int(time.time() * 1000))
            params.setdefault("recvWindow", 5000)
            url = url + "?" + self._sign(params)
            r = self.session.get(url, timeout=self.timeout_s)
        else:
            r = self.session.get(url, params=params, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params: dict | None = None, signed: bool = True):
        params = params or {}
        url = f"{self.base_url}{path}"
        if signed:
            params.setdefault("timestamp", int(time.time() * 1000))
            params.setdefault("recvWindow", 5000)
            body = self._sign(params)
        else:
            body = urlencode(params)
        r = self.session.post(url, data=body, timeout=self.timeout_s, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        return r.json()

    # --- basic endpoints ---
    def ping(self):
        return self._get("/api/v3/ping")

    def server_time(self):
        return self._get("/api/v3/time")

    def exchange_info(self, symbol: str | None = None):
        params = {"symbol": symbol} if symbol else {}
        return self._get("/api/v3/exchangeInfo", params=params)

    def klines(self, symbol: str, interval: str = "15m", limit: int = 200):
        return self._get("/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})

    def account(self):
        return self._get("/api/v3/account", signed=True)

    # --- trading (we will keep disabled by default in v0) ---
    def new_order(self, symbol: str, side: str, order_type: str, quantity: str, **kwargs):
        params = {"symbol": symbol, "side": side, "type": order_type, "quantity": quantity}
        params.update(kwargs)
        return self._post("/api/v3/order", params=params, signed=True)


def main():
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

    base_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")
    symbol = os.getenv("BINANCE_SYMBOL", "BTCUSDT")

    client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)

    # Connectivity checks
    client.ping()
    st = client.server_time()
    print(f"OK ping. Server time: {st}")

    # Auth check
    acct = client.account()
    balances = [b for b in acct.get("balances", []) if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
    print(f"Authenticated. Non-zero balances: {len(balances)}")

    # Market data check
    k = client.klines(symbol=symbol, interval="15m", limit=5)
    last = k[-1]
    print(f"Klines OK. {symbol} last close={last[4]} (interval=15m)")


if __name__ == "__main__":
    main()
