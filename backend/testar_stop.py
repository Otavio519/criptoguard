"""Teste rápido na TESTNET: compra um pouco de BTC fictício, registra o stop na corretora,
confere, cancela e vende de volta. Grava o resultado em data/teste_stop.txt."""
import sys, traceback
from pathlib import Path
from app.config import settings
from app.data import make_exchange
from app.broker import LiveBroker

out = Path(__file__).resolve().parent / "data" / "teste_stop.txt"
log = []
def p(*a):
    line = " ".join(str(x) for x in a); print(line); log.append(line)
try:
    if not settings.use_testnet:
        p("ABORTADO: USE_TESTNET nao esta true. Este teste so roda na testnet."); raise SystemExit(1)
    ex = make_exchange(settings.exchange, settings.api_key, settings.api_secret, True)
    b = LiveBroker(ex, "BTC/USDT")
    p("tipos de ordem aceitos:", (b.market.get("info") or {}).get("orderTypes"))
    price = b.price(); p("preco testnet:", price)
    q0, b0 = b.balances(); p("saldo antes:", q0, "USDT", b0, "BTC")
    qty = round(20 / price, 5)
    filled, avg, fee = b.buy(qty, price); p("COMPRA ok:", filled, "a", avg, "taxa", fee)
    oid = b.place_stop(filled, price * 0.9); p("STOP registrado na corretora, id", oid, "gatilho", round(price*0.9,2))
    st = b.stop_status(oid); p("status do stop:", st)
    b.cancel_stop(oid); p("stop cancelado:", b.stop_status(oid)["status"])
    filled2, avg2, fee2 = b.sell(filled, price); p("VENDA ok:", filled2, "a", avg2)
    p("RESULTADO: SUCESSO")
except SystemExit:
    pass
except Exception:
    p("RESULTADO: FALHA"); p(traceback.format_exc())
out.write_text("\n".join(log), encoding="utf-8")
