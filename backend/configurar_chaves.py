"""Assistente para colar as chaves da testnet no .env e testar a conexão."""
import re
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"


def pedir(nome: str) -> str:
    while True:
        v = input(f"\nCole a {nome} aqui (botao direito do mouse ou Ctrl+V) e aperte Enter:\n> ").strip().strip('"').strip("'")
        v = re.sub(r"\s+", "", v)
        if len(v) >= 20 and re.fullmatch(r"[A-Za-z0-9]+", v):
            return v
        print(f"  Isso nao parece uma chave valida ({len(v)} caracteres). Copie de novo e tente outra vez.")


def main() -> int:
    print("=" * 60)
    print(" CriptoGuard - configurar chaves da TESTNET da Binance")
    print("=" * 60)
    print("Abra a pagina da testnet, copie cada chave e cole aqui.")
    api_key = pedir("CHAVE DA API")
    secret = pedir("CHAVE SECRETA")
    if api_key == secret:
        print("\nAs duas chaves ficaram iguais. Voce colou a mesma duas vezes. Rode de novo.")
        return 1

    s = ENV.read_text(encoding="utf-8-sig")
    for k, v in {"API_KEY": api_key, "API_SECRET": secret, "MODE": "live", "USE_TESTNET": "true",
                 "LIVE_CONFIRM": "EU ACEITO O RISCO DE PERDER DINHEIRO"}.items():
        if re.search(rf"(?m)^{k}=.*$", s):
            s = re.sub(rf"(?m)^{k}=.*$", lambda _m: f"{k}={v}", s)
        else:
            s += f"\n{k}={v}\n"
    ENV.write_text(s, encoding="utf-8")
    print("\nChaves salvas no arquivo .env.")

    print("Testando a conexao com a testnet...")
    try:
        import ccxt
        ex = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                           "options": {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}, "fetchCurrencies": False,
                    "adjustForTimeDifference": True, "recvWindow": 10000}})
        ex.set_sandbox_mode(True)
        bal = ex.fetch_balance()
        usdt = bal["free"].get("USDT", 0)
        btc = bal["free"].get("BTC", 0)
        print(f"\nCONEXAO OK! Saldo ficticio na testnet: {usdt:,.2f} USDT e {btc} BTC")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"\nAs chaves foram salvas, mas a conexao falhou: {e}")
        print("Confira se copiou as chaves da TESTNET (testnet.binance.vision) e nao da conta real.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
