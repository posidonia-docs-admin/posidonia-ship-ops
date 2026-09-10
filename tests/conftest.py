import os
import pathlib
import sqlite3
import tempfile
import uuid

# Precisa vir ANTES de importar app.config: as constantes sao lidas no import.
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="shipops-testes-"))
os.environ.setdefault("SHIPOPS_BANCO", str(_TMP / "teste.db"))
os.environ.setdefault("SHIPOPS_SECRET_KEY", "chave-longa-apenas-para-os-testes-xyz")

import pytest  # noqa: E402

from app import config, db  # noqa: E402


@pytest.fixture()
def conn():
    """Banco limpo com schema e seed. Um por teste.

    Com SHIPOPS_BACKEND=libsql-local a suite inteira roda contra o MESMO driver
    de producao. Isso existe porque o libsql devolve tuplas e o sqlite3 devolve
    Row — uma diferenca que passou despercebida e so apareceu depois do deploy.
    """
    if config.usar_libsql():
        import libsql

        caminho = _TMP / "unidade-{}.db".format(uuid.uuid4().hex)
        c = db._Conexao(libsql.connect(str(caminho)))
    else:
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")

    db.inicializar(c)
    yield c
    c.close()


@pytest.fixture()
def pathfinder():
    """navio_id do Amazon Pathfinder, o navio do piloto."""
    return 1
