"""Subir um banco que JA EXISTE, criado antes de uma coluna nova.

Os demais testes sempre partem de banco vazio, entao nunca exercitam este
caminho — que e exatamente o de producao: o banco do Turso ja existia quando a
coluna `prefixo` foi criada. Foi assim que o arranque quebrou uma vez.
"""

import sqlite3

from app import db


def _banco_antigo(caminho):
    """Recria o schema SEM a coluna `prefixo`, como estava antes."""
    sql = db._ARQ_SCHEMA.read_text(encoding="utf-8").replace(
        "    prefixo      TEXT,                          -- APT, APN, ACM, ACR\n", "")
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.executescript(sql)
    conn.commit()
    return conn


def test_banco_antigo_ganha_a_coluna_e_o_indice(tmp_path):
    caminho = str(tmp_path / "antigo.db")
    conn = _banco_antigo(caminho)
    colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(navio)")}
    assert "prefixo" not in colunas          # o ponto de partida

    db.inicializar(conn)                     # o arranque do app

    colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(navio)")}
    assert "prefixo" in colunas
    indices = {linha[1] for linha in conn.execute("PRAGMA index_list(navio)")}
    assert "uq_navio_prefixo" in indices
    conn.close()


def test_seed_preenche_os_prefixos_num_banco_ja_povoado(tmp_path):
    """INSERT OR IGNORE nao toca linha existente — por isso o seed usa UPDATE."""
    caminho = str(tmp_path / "povoado.db")
    conn = _banco_antigo(caminho)
    conn.execute("INSERT INTO navio (id, nome_oficial) VALUES (1, 'AMAZON PATHFINDER')")
    conn.commit()

    db.inicializar(conn)

    prefixo = conn.execute("SELECT prefixo FROM navio WHERE id = 1").fetchone()[0]
    assert prefixo == "APT"
    conn.close()


def test_inicializar_duas_vezes_num_banco_antigo(tmp_path):
    caminho = str(tmp_path / "duas.db")
    conn = _banco_antigo(caminho)
    db.inicializar(conn)
    db.inicializar(conn)
    assert conn.execute("SELECT COUNT(*) FROM navio").fetchone()[0] == 4
    conn.close()
