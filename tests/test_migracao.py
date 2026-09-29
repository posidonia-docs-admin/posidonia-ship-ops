"""Subir um banco que JA EXISTE, criado antes de uma coluna nova.

Os demais testes sempre partem de banco vazio, entao nunca exercitam este
caminho — que e exatamente o de producao: o banco do Turso ja existia quando a
coluna `prefixo` foi criada. Foi assim que o arranque quebrou uma vez.
"""

import pathlib
import sqlite3

import pytest

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


def test_escalas_antigas_herdam_a_condicao(tmp_path):
    """A viagem que estava aberta em producao ficaria sem condicao para sempre."""
    import sqlite3
    from app import viagens

    caminho = str(tmp_path / "sem-condicao.db")
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    db.inicializar(conn)

    viagem_id, _ = viagens.abrir_viagem(conn, 1)
    conn.execute("UPDATE escala SET condicao = NULL WHERE viagem_id = ?", (viagem_id,))
    conn.commit()

    db.inicializar(conn)          # o arranque seguinte

    linhas = conn.execute(
        "SELECT codigo_porto, condicao FROM escala WHERE viagem_id = ? ORDER BY ordem",
        (viagem_id,)).fetchall()
    assert [l["condicao"] for l in linhas] == [
        "ballast", "ballast", "loading", "laden", "laden", "discharging"]
    conn.close()


def _banco_com_check_antigo(caminho):
    """Um banco como o de PRODUCAO antes da mudanca: tabelas com o CHECK antigo
    E AS VIEWS JA CRIADAS.

    As views sao o que faltava aqui: sem elas o teste passava e o deploy
    quebrava, porque o SQLite valida toda view durante um ALTER TABLE RENAME.
    """
    import sqlite3

    sql = db._ARQ_SCHEMA.read_text(encoding="utf-8").replace(
        "'operacional', 'fundeio', 'passagem', 'abertura', 'encerramento'",
        "'operacional', 'fundeio', 'passagem', 'abertura'")
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(sql)
    conn.executescript(db._ARQ_VIEWS.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def test_check_antigo_e_reconstruido(tmp_path):
    """SQLite grava o CHECK na definicao da tabela e nao ha ALTER para ele:
    sem reconstruir, um valor novo no enum derruba o arranque em producao."""
    conn = _banco_com_check_antigo(str(tmp_path / "check-antigo.db"))
    guardado = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'escala'").fetchone()[0]
    assert "encerramento" not in guardado          # o ponto de partida

    db.inicializar(conn)                            # o arranque do app

    guardado = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'escala'").fetchone()[0]
    assert "encerramento" in guardado
    assert conn.execute(
        "SELECT COUNT(*) FROM rota_etapa").fetchone()[0] == 6
    conn.close()


def test_reconstrucao_preserva_as_linhas_e_a_referencia(tmp_path):
    """Reconstruir a escala nao pode perder evento nem soltar a chave estrangeira."""
    from app import viagens

    conn = _banco_com_check_antigo(str(tmp_path / "com-dados.db"))
    # popula com a estrutura antiga: sem 'encerramento', a rota tem 5 etapas
    conn.executescript(db._ARQ_SEED.read_text(encoding="utf-8").replace(
        "'encerramento'", "'operacional'"))
    conn.commit()
    viagem_id, _ = viagens.abrir_viagem(conn, 1)
    escala_id = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? ORDER BY ordem LIMIT 1",
        (viagem_id,)).fetchone()[0]
    # a primeira escala e a saida de Alumar: so aceita sailing
    _, erros = viagens.lancar_marco(conn, escala_id, tipo="sailing",
                                    hora_local="2026-03-01T08:00",
                                    nome_responsavel="Cmt.", registrado_por="teste")
    assert erros == [], erros

    db.inicializar(conn)

    assert conn.execute("SELECT COUNT(*) FROM evento").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM escala WHERE id = ?", (escala_id,)).fetchone()[0] == 1
    # a chave estrangeira de evento continua apontando para 'escala'
    evento_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'evento'").fetchone()[0]
    assert "REFERENCES escala(id)" in evento_sql
    assert "escala_antiga" not in evento_sql
    conn.close()


def test_marcos_do_tipo_novo_chegam_num_banco_antigo(tmp_path):
    """O INSERT OR IGNORE do seed ENGOLE a violacao de CHECK sem avisar.

    Sem reconstruir marco_exigido, a escala de encerramento fica sem nenhum
    marco exigido — e `all([])` faz ela se declarar COMPLETA com tudo em branco.
    """
    conn = _banco_com_check_antigo(str(tmp_path / "sem-marcos.db"))
    db.inicializar(conn)

    exigidos = {linha[0] for linha in conn.execute(
        "SELECT tipo_evento FROM marco_exigido WHERE tipo_escala = 'encerramento'")}
    assert exigidos == {"arrival", "berth", "unberth"}
    conn.close()


def test_escala_de_encerramento_nao_nasce_completa(tmp_path):
    from app import viagens

    conn = _banco_com_check_antigo(str(tmp_path / "completa.db"))
    db.inicializar(conn)
    viagem_id, _ = viagens.abrir_viagem(conn, 1)

    faltantes = conn.execute(
        "SELECT COUNT(*) FROM escalas_incompletas i JOIN escala e ON e.id = i.escala_id "
        " WHERE i.viagem_id = ? AND e.tipo_escala = 'encerramento'",
        (viagem_id,)).fetchone()[0]
    assert faltantes == 3
    conn.close()


def test_migracao_so_usa_pragma_que_o_turso_aceita():
    """O Turso remoto tem uma LISTA DE COMANDOS PERMITIDOS, e ela NAO e a do
    libsql local: um teste local passa e o deploy quebra.

    `PRAGMA legacy_alter_table` derrubou um deploy de verdade. Este teste e o
    guarda para nao acontecer de novo — cada PRAGMA aqui precisa de evidencia
    de que o servidor aceita.
    """
    import re

    # Evidencia, nesta ordem de confianca:
    #   foreign_keys  — o log do deploy que falhou mostra que ELE passou; a
    #                   excecao veio na linha SEGUINTE (legacy_alter_table).
    #   table_info    — ja rodou em producao nos deploys anteriores.
    #   user_version  — envolvido em try/except; ausencia significa banco novo.
    ACEITOS = {"foreign_keys", "table_info", "user_version"}

    # So o que e EXECUTADO: PRAGMA em SQL comeca logo depois da aspa. Comentario
    # que menciona um PRAGMA (para explicar por que nao usamos) nao conta.
    fonte = pathlib.Path(db.__file__).read_text(encoding="utf-8")
    usados = set(re.findall(r'"PRAGMA (\w+)', fonte))
    assert usados, "o regex parou de casar — conferir antes de confiar no guarda"
    assert usados <= ACEITOS, "PRAGMA sem evidencia: {}".format(usados - ACEITOS)


def test_a_reconstrucao_nao_depende_de_renomear_a_tabela_original(tmp_path):
    """Renomear a original faria o SQLite reescrever a chave estrangeira de
    quem a referencia. A ordem correta cria a nova com nome temporario e so
    renomeia ela — e por isso nao precisa de `legacy_alter_table`."""
    fonte = pathlib.Path(db.__file__).read_text(encoding="utf-8")
    assert '"PRAGMA legacy_alter_table' not in fonte      # citado, nunca executado
    assert 'ALTER TABLE {} RENAME TO {}".format(temporaria, tabela)' in fonte


# ---------------------------------------------------------------------------
# Subir a partir de uma versao JA PUBLICADA
# ---------------------------------------------------------------------------

def _sql_da_versao(commit, caminho):
    """O arquivo como estava naquele commit, ou None se nao der para ler."""
    import subprocess

    raiz = pathlib.Path(db.__file__).resolve().parent.parent
    try:
        r = subprocess.run(["git", "show", "{}:{}".format(commit, caminho)],
                           cwd=raiz, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


# De onde o arranque precisa conseguir subir.
#
# `producao` e uma ETIQUETA do git que aponta para o que esta rodando no
# Render — e o caso que realmente importa, e ela se move sozinha conforme o
# projeto anda. MOVER A ETIQUETA APOS CADA DEPLOY BEM-SUCEDIDO:
#
#     git tag -f -a producao -m "..." <commit>  &&  git push -f origin producao
#
# Os SHAs sao versoes mais antigas que ja estiveram publicadas. Ficam porque
# sao historia real e custam pouco; se um dia a lista incomodar, corte os
# antigos e mantenha `producao`.
VERSOES_PUBLICADAS = ["producao", "7aa95ef", "5196134", "1220afd", "d3bfbba", "5eb88f1"]


@pytest.mark.parametrize("commit", VERSOES_PUBLICADAS)
def test_arranque_a_partir_de_versao_publicada(tmp_path, commit):
    """Tres deploys quebraram porque o banco de teste nao parecia com o real.

    Aqui o banco e montado com o schema, as views e o seed DAQUELA versao — e
    o arranque de hoje roda em cima. E o caminho de producao, nao uma imitacao.
    """
    import sqlite3

    schema = _sql_da_versao(commit, "app/schema.sql")
    if schema is None:
        pytest.skip("historico do git indisponivel para {}".format(commit))

    conn = sqlite3.connect(str(tmp_path / "{}.db".format(commit)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for caminho in ("app/schema.sql", "app/views.sql", "app/seed.sql"):
        sql = _sql_da_versao(commit, caminho)
        if sql:
            conn.executescript(sql)
    conn.commit()

    db.inicializar(conn)          # o arranque de hoje

    assert conn.execute(
        "SELECT COUNT(*) FROM rota_etapa").fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM marco_exigido WHERE tipo_escala = 'encerramento'"
    ).fetchone()[0] == 3
    # as views voltaram todas
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] >= 9
    # e o banco responde de verdade
    conn.execute("SELECT * FROM escala_completa").fetchall()
    conn.execute("SELECT * FROM consumo_combustivel").fetchall()
    conn.execute("SELECT * FROM carga_bordo").fetchall()
    # Tabela que nasceu DEPOIS de producao existir: Viagens e Premissas caem
    # com Internal Server Error se ela nao aparecer num banco antigo. Foi o
    # que aconteceu em 12/set/2026 — os testes so criavam banco do zero.
    from app import pernadas
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "   AND name = 'premissa_pernada'").fetchone()[0] == 1
    for tabela in ("relatorio_salvo", "sof_marco", "sof_escala"):
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
            (tabela,)).fetchone()[0] == 1, tabela
    assert pernadas.premissas(conn) == {}
    conn.close()
