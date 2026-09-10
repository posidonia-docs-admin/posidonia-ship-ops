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

# "libsql-local": forca o driver do Turso contra arquivo local (so testes).
BACKEND = os.environ.get("SHIPOPS_BACKEND", "").strip().lower()

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


# Conta de administrador criada no arranque. Em producao (Render free) NAO ha
# terminal: sem isto, o sistema sobe e ninguem consegue entrar.
ADMIN_LOGIN = os.environ.get("SHIPOPS_ADMIN_LOGIN", "").strip().lower()
ADMIN_SENHA = os.environ.get("SHIPOPS_ADMIN_SENHA", "")


def usando_turso() -> bool:
    """Turso remoto de verdade."""
    return bool(TURSO_URL)


def usar_libsql() -> bool:
    """Usar o driver libSQL — remoto, ou contra arquivo local para testar.

    SHIPOPS_BACKEND=libsql-local roda a suite inteira contra o MESMO driver de
    producao, sem credencial nenhuma. E o que impede de repetir a falha que so
    apareceu depois do deploy: o libsql devolve tuplas, o sqlite3 devolve Row.
    """
    return bool(TURSO_URL) or BACKEND == "libsql-local"


def em_hospedagem_efemera() -> bool:
    """O Render define RENDER=true. O container e recriado a cada deploy."""
    return os.environ.get("RENDER", "").lower() in ("1", "true", "yes")


def checar_persistencia() -> None:
    """Recusa subir em hospedagem efemera sem banco na nuvem.

    Sem esta trava o app subiria, funcionaria, o comandante lancaria uma viagem
    inteira — e o primeiro redeploy apagaria tudo, em silencio. Falhar no
    arranque e barulhento e barato; perder dado nao tem volta.
    """
    if em_hospedagem_efemera() and not usando_turso():
        raise RuntimeError(
            "TURSO_DATABASE_URL nao definida. Neste ambiente o disco e efemero: "
            "o banco local seria apagado no proximo deploy. Configure o Turso "
            "antes de subir."
        )
