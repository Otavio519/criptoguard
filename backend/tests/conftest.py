"""Isola os testes do seu .env e dos seus dados reais."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="criptoguard-test-")
os.environ["PANEL_PASSWORD"] = ""
os.environ["MODE"] = "paper"
os.environ["AUTO_START"] = "false"
os.environ["REQUIRE_PASSWORD"] = "false"
