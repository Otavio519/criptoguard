import app.main as m
from fastapi.testclient import TestClient


class FakeEx:
    def __init__(self, ok=True): self.ok = ok
    def fetch_balance(self):
        if not self.ok: raise Exception("Invalid Api-Key ID")
        return {"total": {"USDT": 10000.0}}


def test_chaves(tmp_path, monkeypatch):
    env = tmp_path / ".env"; env.write_text("MODE=paper\nAPI_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(m, "ENV_FILE", env)
    saiu = []
    monkeypatch.setattr(m.os, "_exit", lambda c: saiu.append(c))
    c = TestClient(m.app)
    assert "Chaves da Binance" in c.get("/chaves").text
    monkeypatch.setattr(m, "make_exchange", lambda *a, **k: FakeEx(False))
    r = c.post("/api/config/chaves", json={"api_key": "A" * 64, "api_secret": "B" * 64})
    assert r.status_code == 400 and "recusou" in r.json()["detail"]
    assert "API_KEY=\n" in env.read_text()
    r = c.post("/api/config/chaves", json={"api_key": "A" * 64, "api_secret": "A" * 64})
    assert r.status_code == 400
    monkeypatch.setattr(m, "make_exchange", lambda *a, **k: FakeEx(True))
    r = c.post("/api/config/chaves", json={"api_key": "A" * 64, "api_secret": "B" * 64})
    assert r.status_code == 200 and r.json()["usdt"] == 10000.0
    t = env.read_text()
    assert f"API_KEY={'A'*64}" in t and "MODE=live" in t and "USE_TESTNET=true" in t and "LIVE_CONFIRM=EU ACEITO" in t
