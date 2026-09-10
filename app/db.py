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


def conectar():
    """Devolve uma conexao. Turso se configurado, SQLite local caso contrario."""
    if config.usando_turso():
        import libsql  # importado so quando usado: nao pesa o boot local

        conn = libsql.connect(
            database=config.TURSO_URL,
            auth_token=config.TURSO_TOKEN,
            autocommit=False,
        )
        return conn

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


def _versao_schema(conn) -> int:
    """PRAGMA nem sempre existe fora do SQLite local. Ausencia = banco novo."""
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
        _rodar_script(conn, _ARQ_SCHEMA.read_text(encoding="utf-8"))
        _rodar_script(conn, _ARQ_SEED.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        if propria:
            conn.close()
