"""Baixa o histórico diário de ações da B3 (Yahoo Finance), já ajustado por dividendos e desdobramentos."""
import json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

TICKERS = ["PETR4", "VALE3", "ITUB4", "BBAS3", "BBDC4", "WEGE3", "ABEV3", "BOVA11"]
OUT = Path(__file__).resolve().parent / "data" / "cache"
OUT.mkdir(parents=True, exist_ok=True)
start = int(datetime(2014, 1, 1, tzinfo=timezone.utc).timestamp())
end = int(time.time())
log = []
for t in TICKERS:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{t}.SA?period1={start}&period2={end}"
           "&interval=1d&events=div%2Csplit")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
        q, adj = d["indicators"]["quote"][0], d["indicators"]["adjclose"][0]["adjclose"]
        rows = ["timestamp,open,high,low,close,volume"]
        for i, ts in enumerate(d["timestamp"]):
            o, h, l, c, v, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i], adj[i]
            if None in (o, h, l, c, a) or c == 0:
                continue
            f = a / c  # fator de ajuste (dividendos e desdobramentos)
            day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            rows.append(f"{day}T00:00:00+00:00,{o*f:.6f},{h*f:.6f},{l*f:.6f},{a:.6f},{v or 0}")
        (OUT / f"b3_{t}_1d.csv").write_text("\n".join(rows), encoding="utf-8")
        log.append(f"{t}: {len(rows)-1} dias")
    except Exception as e:
        log.append(f"{t}: ERRO {e}")
    print(log[-1]); time.sleep(1)
(OUT / "b3_log.txt").write_text("\n".join(log), encoding="utf-8")
print("Pronto. Pode fechar esta janela.")
