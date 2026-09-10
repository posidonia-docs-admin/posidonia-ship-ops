"""Contas de acesso. Uma por navio, mais as da equipe em terra.

A conta e POR NAVIO, nao por pessoa — decisao operacional, por causa da troca de
tripulacao. O rastro individual volta pelo campo `nome_responsavel` do evento,
que e obrigatorio em todo lancamento.
"""
from __future__ import annotations

import sqlite3

from . import auth, dominio
from .db import agora

PERFIS = ("navio", "supervisor", "analytics", "admin")

# Quem so le. O bloqueio e por METODO HTTP, nao rota a rota: tudo que muda e
# POST, entao e barato de aplicar e dificil de esquecer numa rota nova.
PERFIS_SOMENTE_LEITURA = ("analytics",)


def criar_conta(
    conn,
    *,
    login: str,
    senha: str,
    nome_exibicao: str,
    perfil: str,
    navio_id: int | None = None,
) -> tuple[str | None, list[str]]:
    erros: list[str] = []
    login = (login or "").strip().lower()

    if not login:
        erros.append("Informe o login.")
    if perfil not in PERFIS:
        erros.append("Perfil inválido: {!r}.".format(perfil))
    if len(senha or "") < 8:
        erros.append("A senha precisa de pelo menos 8 caracteres.")
    if not (nome_exibicao or "").strip():
        erros.append("Informe o nome de exibição.")

    if perfil == "navio":
        if navio_id is None:
            erros.append("Conta de navio precisa estar vinculada a um navio.")
        elif conn.execute(
            "SELECT 1 FROM navio WHERE id = ? AND ativo = 1", (navio_id,)
        ).fetchone() is None:
            erros.append("Navio {} não cadastrado ou inativo.".format(navio_id))
    else:
        navio_id = None

    if erros:
        return None, erros

    try:
        conn.execute(
            "INSERT INTO conta (login, nome_exibicao, perfil, senha_hash, navio_id, "
            "                   ativo, criada_em) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (login, nome_exibicao.strip(), perfil, auth.hash_senha(senha),
             navio_id, agora()),
        )
    except sqlite3.IntegrityError:
        return None, ["Já existe uma conta com o login {!r}.".format(login)]

    conn.commit()
    return login, []


def autenticar(conn, login: str, senha: str):
    """Devolve a linha da conta, ou None.

    Mensagem de erro unica para login inexistente e senha errada — dizer qual
    dos dois falhou entrega metade da credencial a quem esta tentando adivinhar.
    """
    linha = conn.execute(
        "SELECT login, nome_exibicao, perfil, senha_hash, navio_id, ativo "
        "  FROM conta WHERE login = ?", ((login or "").strip().lower(),),
    ).fetchone()
    if linha is None or not linha["ativo"]:
        # Gasta o mesmo tempo de um hash real, para que a resposta nao denuncie
        # se o login existe.
        auth.verificar_senha(senha or "", "scrypt$16384$8$1$" + "A" * 22 + "$" + "A" * 43)
        return None
    if not auth.verificar_senha(senha or "", linha["senha_hash"]):
        return None
    return linha


def buscar(conn, login: str):
    return conn.execute(
        "SELECT c.login, c.nome_exibicao, c.perfil, c.navio_id, c.ativo, "
        "       n.nome_oficial AS navio_nome "
        "  FROM conta c LEFT JOIN navio n ON n.id = c.navio_id "
        " WHERE c.login = ? AND c.ativo = 1", (login,),
    ).fetchone()


def registrar_acesso(conn, login: str | None, acao: str, ip: str | None = None) -> None:
    conn.execute(
        "INSERT INTO log_acesso (conta, acao, ip, quando) VALUES (?, ?, ?, ?)",
        (login, acao, ip, agora()),
    )
    conn.commit()


def trocar_senha(conn, login: str, senha_nova: str) -> tuple[bool, list[str]]:
    if len(senha_nova or "") < 8:
        return False, ["A senha precisa de pelo menos 8 caracteres."]
    alterou = conn.execute(
        "UPDATE conta SET senha_hash = ? WHERE login = ? AND ativo = 1",
        (auth.hash_senha(senha_nova), login),
    )
    conn.commit()
    if getattr(alterou, "rowcount", 0) == 0:
        return False, ["Conta {!r} não encontrada ou inativa.".format(login)]
    return True, []


def normalizar_login_de_navio(nome_oficial: str) -> str:
    """'AMAZON PATHFINDER' -> 'navio.pathfinder'."""
    partes = dominio.normalizar(nome_oficial).split()
    return "navio." + (partes[-1].lower() if partes else "sem-nome")
