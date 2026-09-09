"""Configuracao. Tudo por variavel de ambiente, com default de desenvolvimento.

Regra: nada de segredo com valor default. O Sistema Emissor tem a senha de
producao escrita no repositorio; aqui o app recusa subir sem SECRET_KEY.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# O banco NUNCA no OneDrive: ele corrompe arquivo aberto, e o MAX_PATH do
# Windows estoura no pip install a partir de la.
DIR_DADOS = Path(
    os.environ.get("SHIPOPS_DIR_DADOS")
    or os.environ.get("LOCALAPPDATA", str(Path.home()))
).joinpath("PosidoniaShipOps")

CAMINHO_BANCO = os.environ.get("SHIPOPS_BANCO") or str(DIR_DADOS / "shipops.db")

# Turso (libSQL). A presenca da URL liga a nuvem; sem ela, SQLite local.
TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

# Fuso padrao da frota: Para, Amapa e Maranhao sao todos UTC-3.
OFFSET_PADRAO = os.environ.get("SHIPOPS_OFFSET_PADRAO", "-03:00")

# Quanto um marco pode estar no futuro antes de ser recusado (relogio de bordo
# adiantado e normal; meio dia de diferenca nao e).
TOLERANCIA_FUTURO_HORAS = float(os.environ.get("SHIPOPS_TOLERANCIA_FUTURO_HORAS", "2"))


def secret_key(obrigatoria: bool = True) -> str:
    """Segredo de assinatura do cookie. Sem default — se faltar, o app nao sobe."""
    valor = os.environ.get("SHIPOPS_SECRET_KEY", "").strip()
    if valor:
        return valor
    if obrigatoria:
        raise RuntimeError(
            "SHIPOPS_SECRET_KEY nao definida. Defina uma chave longa e aleatoria "
            "no ambiente antes de subir o app."
        )
    return "chave-apenas-de-teste-nao-use-em-producao"


def usando_turso() -> bool:
    return bool(TURSO_URL)
