"""Camada de banco. SQLite local em desenvolvimento, Turso (libSQL) em producao.

O resto do codigo nao sabe em qual dos dois esta: chama `conectar()` e pronto.
E o que torna a troca de hospedagem barata — o dado vive no Turso, nao no disco
do Render.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import config

# Incrementar junto com o PRAGMA user_version no fim de schema.sql.
VERSAO_SCHEMA = 1

_ARQ_SCHEMA = Path(__file__).with_name("schema.sql")
_ARQ_VIEWS = Path(__file__).with_name("views.sql")
_ARQ_SEED = Path(__file__).with_name("seed.sql")

# Turso e remoto e cai de vez em quando. Repeticao SO EM LEITURA — escrita
# nunca, para nao duplicar lancamento.
_ESPERAS_LEITURA = (0.5, 1.0, 2.0, 4.0)
_ERROS_TRANSITORIOS = (
    "hrana: stream not found",
    "unexpected eof",
    "connection reset by peer",
    "server disconnected",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)


def agora() -> str:
    """Instante atual em ISO 8601 UTC. Todo carimbo do sistema passa por aqui."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Compatibilidade com o driver libSQL (Turso)
#
# O `libsql` NAO e o `sqlite3`: devolve tuplas puras, nao tem `row_factory` e o
# cursor nao e iteravel. Como o codigo todo le por nome (`conta["perfil"]`) e
# itera cursores, sem esta camada tudo funciona no SQLite local e quebra em
# producao — exatamente o tipo de falha que so aparece depois do deploy.
# ---------------------------------------------------------------------------

class _Linha(tuple):
    """Tupla com acesso por nome, como o sqlite3.Row.

    O Jinja tenta atributo antes de item, entao `linha.nome_oficial` tambem
    funciona nos templates — o mesmo comportamento do sqlite3.Row.
    """

    def __new__(cls, colunas, valores):
        obj = super().__new__(cls, valores)
        obj._colunas = colunas
        return obj

    def __getitem__(self, chave):
        if isinstance(chave, str):
            try:
                return tuple.__getitem__(self, self._colunas.index(chave))
            except ValueError:
                raise KeyError(chave) from None
        return tuple.__getitem__(self, chave)

    def keys(self):
        return list(self._colunas)


class _Cursor:
    def __init__(self, bruto):
        self._bruto = bruto
        descricao = getattr(bruto, "description", None) or ()
        self._colunas = [coluna[0] for coluna in descricao]

    def _converter(self, linha):
        return None if linha is None else _Linha(self._colunas, linha)

    def fetchone(self):
        return self._converter(self._bruto.fetchone())

    def fetchall(self):
        return [self._converter(linha) for linha in self._bruto.fetchall()]

    def fetchmany(self, tamanho=1):
        return [self._converter(linha) for linha in self._bruto.fetchmany(tamanho)]

    def __iter__(self):
        # O cursor do libsql nao e iteravel; o do sqlite3 e. O codigo depende
        # disso em varios `for linha in conn.execute(...)`.
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._bruto.lastrowid

    @property
    def rowcount(self):
        return self._bruto.rowcount

    @property
    def description(self):
        return self._bruto.description


class _Conexao:
    """Faz o libsql parecer com o sqlite3 para o resto do codigo."""

    def __init__(self, bruta):
        self._bruta = bruta

    def execute(self, sql, params=()):
        return _Cursor(self._bruta.execute(sql, params))

    def executemany(self, sql, seq):
        return _Cursor(self._bruta.executemany(sql, seq))

    def executescript(self, sql):
        return self._bruta.executescript(sql)

    def commit(self):
        self._bruta.commit()

    def rollback(self):
        self._bruta.rollback()

    def close(self):
        self._bruta.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def conectar():
    """Devolve uma conexao. libSQL se configurado, SQLite local caso contrario."""
    if config.usar_libsql():
        import libsql  # importado so quando usado: nao pesa o boot local

        if config.usando_turso():
            bruta = libsql.connect(
                database=config.TURSO_URL,
                auth_token=config.TURSO_TOKEN,
                autocommit=False,
            )
        else:
            # libsql contra arquivo local: e assim que a suite de testes exercita
            # o driver de producao sem precisar de credencial nenhuma.
            Path(config.CAMINHO_BANCO).parent.mkdir(parents=True, exist_ok=True)
            bruta = libsql.connect(config.CAMINHO_BANCO)
        return _Conexao(bruta)

    Path(config.CAMINHO_BANCO).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.CAMINHO_BANCO, timeout=10)
    conn.row_factory = sqlite3.Row
    # PRAGMA e por conexao, nao por banco: nao adianta deixar no schema.sql.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _e_transitorio(exc: BaseException) -> bool:
    """Percorre a cadeia de causas — o erro real costuma estar no fundo."""
    vistos = 0
    atual: BaseException | None = exc
    while atual is not None and vistos < 10:
        texto = str(atual).lower()
        if any(marca in texto for marca in _ERROS_TRANSITORIOS):
            return True
        atual = atual.__cause__ or atual.__context__
        vistos += 1
    return False


def ler(sql: str, params: tuple = (), *, uma: bool = False):
    """Leitura com repeticao. Nunca use para escrita."""
    esperas = _ESPERAS_LEITURA if config.usando_turso() else ()
    for tentativa in range(len(esperas) + 1):
        try:
            with closing(conectar()) as conn:
                cur = conn.execute(sql, params)
                return cur.fetchone() if uma else cur.fetchall()
        except Exception as exc:  # noqa: BLE001 — reclassificado logo abaixo
            if tentativa == len(esperas) or not _e_transitorio(exc):
                raise
            time.sleep(esperas[tentativa])
    return None if uma else []


def _dividir_script(sql: str) -> list[str]:
    """Quebra o script em comandos. Usado so quando o driver nao tem executescript.

    Funciona porque em schema.sql e seed.sql nenhum comando carrega ';' no meio —
    nao ha trigger nem corpo de funcao. Se um dia houver, isto quebra: prefira
    manter essa propriedade a tentar um parser de SQL aqui.
    """
    limpo = "\n".join(
        linha for linha in sql.splitlines() if not linha.strip().startswith("--"))
    return [c.strip() for c in limpo.split(";") if c.strip()]


def _rodar_script(conn, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for comando in _dividir_script(sql):
        conn.execute(comando)


# Colunas acrescentadas DEPOIS que o banco de producao ja existia. O
# `CREATE TABLE IF NOT EXISTS` nao as adiciona a uma tabela que ja esta la.
_COLUNAS_NOVAS = (
    ("navio", "prefixo", "TEXT"),
    ("escala", "condicao", "TEXT"),
    ("rota_etapa", "condicao", "TEXT"),
    ("evento", "rob_vlsfo", "REAL"),
    ("evento", "rob_mgo", "REAL"),
)


# Tabelas que nasceram depois do primeiro deploy. Idempotentes.
_TABELAS_POSTERIORES = (
    "CREATE TABLE IF NOT EXISTS premissa_pernada ("
    "  chave TEXT PRIMARY KEY,"
    "  horas REAL NOT NULL CHECK (horas >= 0),"
    "  atualizado_por TEXT,"
    "  atualizado_em TEXT NOT NULL)",
)


# Indices que dependem de coluna acrescentada acima. Ficam FORA do schema.sql
# porque aquele script roda antes da migracao — num banco antigo a coluna ainda
# nao existe e o CREATE INDEX derruba o arranque.
_INDICES_POSTERIORES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_navio_prefixo "
    "  ON navio (prefixo) WHERE prefixo IS NOT NULL",
)


# Tabelas cujo CHECK precisa acompanhar uma lista que cresceu.
#
# O SQLite grava o CHECK na DEFINICAO da tabela: `CREATE TABLE IF NOT EXISTS`
# nao o atualiza e nao existe ALTER para ele. Um valor novo num enum derruba o
# arranque de qualquer banco que ja exista — foi o que aconteceu quando
# `encerramento` entrou em tipo_escala.
#
# A entrada e (tabela, marca): se a marca NAO aparece no CREATE guardado, a
# tabela e reconstruida. Rodar isso de novo com a marca presente nao faz nada.
#
# Licao: enum que cresce e caro em CHECK. Se essa lista comecar a crescer,
# troque o CHECK por chave estrangeira para uma tabela de referencia — ai
# acrescentar valor e um INSERT, nao uma reconstrucao.
_TABELAS_RECRIAR = (
    # marco_exigido PRIMEIRO: sem os marcos do tipo novo, uma escala sem nada
    # exigido se declara COMPLETA (all([]) e verdadeiro) — e o pior e que o
    # `INSERT OR IGNORE` do seed ENGOLE a violacao de CHECK sem uma palavra.
    ("marco_exigido", "encerramento"),
    ("rota_etapa", "encerramento"),
    ("escala", "encerramento"),
)


def _comando_de_criacao(tabela: str) -> str:
    """O CREATE TABLE desta tabela, como esta hoje em schema.sql."""
    alvo = "CREATE TABLE IF NOT EXISTS {} (".format(tabela)
    for comando in _dividir_script(_ARQ_SCHEMA.read_text(encoding="utf-8")):
        if alvo in comando:
            return comando
    raise RuntimeError("schema.sql nao tem o CREATE de {}".format(tabela))


def _indices_de(tabela: str) -> list[str]:
    marca = " ON {} (".format(tabela)
    return [c for c in _dividir_script(_ARQ_SCHEMA.read_text(encoding="utf-8"))
            if c.upper().startswith("CREATE") and "INDEX" in c.upper() and marca in c]


# Tabelas que sao pura CONFIGURACAO: o seed as reenche. Nao ha dado a preservar,
# entao a reconstrucao delas e apagar e recriar.
_TABELAS_SEM_DADO = ("marco_exigido", "rota_etapa")


def _pragma(conn, comando: str) -> None:
    """Executa um PRAGMA que pode nao existir no destino.

    O Turso remoto tem uma LISTA DE COMANDOS PERMITIDOS e recusa varios PRAGMAs
    com SQL_PARSE_ERROR. Essa lista NAO e a do libsql local — um teste local
    passa e o deploy quebra. Por isso: nada aqui pode DEPENDER de um PRAGMA.
    """
    try:
        conn.execute(comando)
    except Exception:  # noqa: BLE001 — indisponivel e um caso previsto, nao erro
        pass


def _apagar_views(conn) -> None:
    """Apaga todas as views. Elas sao recriadas logo em seguida, por views.sql.

    Enquanto existirem, travam a reconstrucao de tabela: o SQLite valida TODA
    view durante um ALTER TABLE ... RENAME e falha se alguma apontar para uma
    tabela que acabou de ser apagada. Foi assim que um deploy quebrou com
    "error in view escala_marcos: no such table: main.escala".

    View e derivada — apagar nao custa dado nenhum.
    """
    for linha in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
        conn.execute("DROP VIEW IF EXISTS {}".format(linha[0]))


def _recriar_com_check_novo(conn) -> None:
    """Reconstroi tabela cujo CHECK ficou para tras. Idempotente.

    A ORDEM e deliberada: cria a nova com nome TEMPORARIO, copia, apaga a
    velha e so entao renomeia a temporaria para o nome real.

    Renomear a tabela ORIGINAL primeiro faria o SQLite reescrever a chave
    estrangeira de quem a referencia para o nome temporario — e ela ficaria
    apontando para o vazio. A saida usual seria `PRAGMA legacy_alter_table`,
    que o Turso RECUSA. Nesta ordem o problema nao existe: o que se renomeia e
    a tabela temporaria, e nada aponta para ela.
    """
    for tabela, marca in _TABELAS_RECRIAR:
        linha = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (tabela,)).fetchone()
        if linha is None or marca in (linha[0] or ""):
            continue

        _pragma(conn, "PRAGMA foreign_keys = OFF")
        try:
            if tabela in _TABELAS_SEM_DADO:
                conn.execute("DROP TABLE {}".format(tabela))
                conn.execute(_comando_de_criacao(tabela))
            else:
                colunas = [c[1] for c in
                           conn.execute("PRAGMA table_info({})".format(tabela))]
                lista = ", ".join(colunas)
                temporaria = "{}__novo".format(tabela)
                conn.execute("DROP TABLE IF EXISTS {}".format(temporaria))
                conn.execute(_comando_de_criacao(tabela).replace(
                    "IF NOT EXISTS {} (".format(tabela),
                    "IF NOT EXISTS {} (".format(temporaria), 1))
                conn.execute("INSERT INTO {} ({}) SELECT {} FROM {}".format(
                    temporaria, lista, lista, tabela))
                conn.execute("DROP TABLE {}".format(tabela))
                conn.execute("ALTER TABLE {} RENAME TO {}".format(temporaria, tabela))
            for indice in _indices_de(tabela):
                conn.execute(indice)
            conn.commit()
        finally:
            _pragma(conn, "PRAGMA foreign_keys = ON")


def _migrar(conn) -> None:
    """Acrescenta colunas faltantes e seus indices. Idempotente, numa conexao.

    Uma conexao por coluna derrubava o Turso no boot no Sistema Emissor — a
    licao ja foi paga uma vez.
    """
    # As views vem antes de tudo: elas travam a reconstrucao e sao recriadas
    # por views.sql, que roda logo depois desta funcao.
    _apagar_views(conn)
    _recriar_com_check_novo(conn)
    for tabela, coluna, tipo in _COLUNAS_NOVAS:
        existentes = {linha[1] for linha in
                      conn.execute("PRAGMA table_info({})".format(tabela))}
        if coluna not in existentes:
            conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(tabela, coluna, tipo))
    for comando in _INDICES_POSTERIORES:
        conn.execute(comando)
    # Tabelas acrescentadas depois que producao ja existia. O schema.sql tambem
    # as cria, mas por `executescript`; aqui e um `execute` simples, que e o
    # caminho que o Turso remoto comprovadamente aceita.
    for comando in _TABELAS_POSTERIORES:
        conn.execute(comando)


def _versao_schema(conn) -> int:
    """A versao do schema. O PRAGMA nem sempre existe fora do SQLite local;
    ausencia significa banco novo."""
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    except Exception:  # noqa: BLE001
        return 0


def inicializar(conn=None) -> None:
    """Cria o schema e aplica o seed. Idempotente.

    Guarda de versao: se o banco existente for de outra versao do schema, para
    aqui em vez de deixar o CREATE TABLE IF NOT EXISTS passar por cima em
    silencio e dar erro obscuro tres telas adiante.
    """
    propria = conn is None
    conn = conn or conectar()
    try:
        versao = _versao_schema(conn)
        if versao not in (0, VERSAO_SCHEMA):
            raise RuntimeError(
                "Banco na versao de schema {}, o codigo espera {}. "
                "Migre antes de continuar.".format(versao, VERSAO_SCHEMA)
            )
        # A ordem importa e ja quebrou o arranque uma vez:
        #   tabelas -> colunas novas -> views -> seed
        # View e indice que leem coluna nova precisam vir DEPOIS da migracao.
        _rodar_script(conn, _ARQ_SCHEMA.read_text(encoding="utf-8"))
        _migrar(conn)
        _rodar_script(conn, _ARQ_VIEWS.read_text(encoding="utf-8"))
        _rodar_script(conn, _ARQ_SEED.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        if propria:
            conn.close()
