import os
import pathlib
import sqlite3
import tempfile

# Precisa vir ANTES de importar app.config: as constantes sao lidas no import.
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="shipops-testes-"))
os.environ.setdefault("SHIPOPS_BANCO", str(_TMP / "teste.db"))
os.environ.setdefault("SHIPOPS_SECRET_KEY", "chave-longa-apenas-para-os-testes-xyz")

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture()
def conn():
    """Banco em memoria, com schema e seed aplicados. Um por teste."""
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
