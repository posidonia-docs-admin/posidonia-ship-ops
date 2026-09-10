"""A camada que faz o driver do Turso parecer com o sqlite3.

Existe porque o libsql devolve TUPLAS PURAS e o cursor nao e iteravel. O codigo
inteiro le por nome e itera cursores, entao sem isto tudo funciona no SQLite
local e estoura em producao — que foi exatamente o que aconteceu.
"""

from app import config, db


def test_linha_le_por_nome_e_por_posicao():
    linha = db._Linha(["login", "perfil"], ("vlo", "admin"))
    assert linha["login"] == "vlo"
    assert linha["perfil"] == "admin"
    assert linha[0] == "vlo"
    assert linha[1] == "admin"
    assert list(linha) == ["vlo", "admin"]
    assert linha.keys() == ["login", "perfil"]


def test_coluna_inexistente_vira_keyerror():
    linha = db._Linha(["a"], (1,))
    try:
        linha["b"]
    except KeyError as exc:
        assert "b" in str(exc)
    else:
        raise AssertionError("deveria ter levantado KeyError")


def test_acesso_por_atributo_funciona_nos_templates(conn):
    """O Jinja tenta atributo antes de item — e o que faz `porto.nome` funcionar."""
    from jinja2 import Template

    linha = conn.execute(
        "SELECT codigo, nome FROM porto WHERE codigo = 'JURUTI'").fetchone()
    assert Template("{{ p.nome }} ({{ p.codigo }})").render(p=linha) == "Juruti (JURUTI)"


def test_cursor_e_iteravel(conn):
    """O codigo faz `for linha in conn.execute(...)` em varios lugares."""
    nomes = [linha["nome_oficial"] for linha in conn.execute(
        "SELECT nome_oficial FROM navio ORDER BY id")]
    assert len(nomes) == 4
    assert nomes[0] == "AMAZON PATHFINDER"


def test_apelido_de_coluna_e_respeitado(conn):
    linha = conn.execute(
        "SELECT nome_oficial AS apelido FROM navio WHERE id = 1").fetchone()
    assert linha["apelido"] == "AMAZON PATHFINDER"


def test_lastrowid_e_rowcount(conn):
    from app.db import agora

    cur = conn.execute(
        "INSERT INTO porto (codigo, nome, uf) VALUES ('TESTE', 'Teste', 'XX')")
    assert isinstance(cur.lastrowid, int)
    alterou = conn.execute("UPDATE porto SET nome = 'Outro' WHERE codigo = 'TESTE'")
    assert alterou.rowcount == 1
    assert agora().endswith("Z")


def test_backend_do_ambiente_e_o_que_esta_em_uso():
    """Deixa explicito no relatorio contra qual driver a suite rodou."""
    assert config.usar_libsql() == (config.BACKEND == "libsql-local" or
                                    bool(config.TURSO_URL))
