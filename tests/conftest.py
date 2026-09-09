import sqlite3

import pytest

from app import db


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
