"""Servico de viagens, escalas e marcos.

Convencao herdada do account_service.py do Sistema Emissor: cada funcao devolve
`(resultado, erros)`. Lista de erros vazia significa sucesso. Excecao aqui e
falha de programacao — nunca lancamento invalido do comandante.
"""
from __future__ import annotations

import sqlite3
import uuid

from . import dominio
from .db import agora

# As escalas do modelo nascem com ordem 10, 20, 30... para que uma escala extra
# caiba no meio sem renumerar as outras — renumerar sob UNIQUE (viagem_id,
# ordem) e uma fonte de bug que nao vale a pena criar.
PASSO_ORDEM = 10
ORDEM_ABERTURA = 0

PORTO_CICLO = "ALUMAR"  # o porto que abre e fecha a viagem


# ---------------------------------------------------------------------------
# Consultas de apoio
# ---------------------------------------------------------------------------

def marcos_vigentes(conn, escala_id: int) -> dict[str, str]:
    """tipo -> hora_utc dos marcos vigentes da escala. Base da checagem de ordem."""
    linhas = conn.execute(
        "SELECT tipo, hora_utc FROM evento WHERE escala_id = ? AND vigente = 1",
        (escala_id,),
    ).fetchall()
    return {linha[0]: linha[1] for linha in linhas if linha[1]}


def _sailing_de_abertura(conn, navio_id: int):
    """O `sailing` da ultima escala de Alumar deste navio.

    E o evento-ancora que abre a proxima viagem. Nao existe na primeira viagem
    de um navio — nesse caso o chamador cria uma escala de abertura.
    """
    return conn.execute(
        """
        SELECT ev.id
          FROM evento ev
          JOIN escala e  ON e.id = ev.escala_id
          JOIN viagem vg ON vg.id = e.viagem_id
         WHERE vg.navio_id = ?
           AND e.codigo_porto = ?
           AND ev.tipo = 'sailing'
           AND ev.vigente = 1
           AND vg.status <> 'cancelada'
         ORDER BY ev.hora_utc DESC
         LIMIT 1
        """,
        (navio_id, PORTO_CICLO),
    ).fetchone()


def _proximo_numero(conn, navio_id: int) -> str:
    ano = agora()[:4]
    (quantas,) = conn.execute(
        "SELECT COUNT(*) FROM viagem WHERE navio_id = ? AND numero LIKE ?",
        (navio_id, ano + "-%"),
    ).fetchone()
    return "{}-{:03d}".format(ano, quantas + 1)


# ---------------------------------------------------------------------------
# Abertura da viagem
# ---------------------------------------------------------------------------

def abrir_viagem(
    conn,
    navio_id: int,
    *,
    numero: str | None = None,
    rota_modelo_id: int = 1,
    por: str | None = None,
) -> tuple[int | None, list[str]]:
    """Abre a viagem e ja cria as escalas do modelo, vazias e na ordem.

    O comandante nunca cadastra escala do circuito padrao — so preenche horario.
    """
    erros: list[str] = []

    navio = conn.execute(
        "SELECT id, ativo FROM navio WHERE id = ?", (navio_id,)
    ).fetchone()
    if navio is None:
        return None, ["Navio {} não cadastrado.".format(navio_id)]
    if not navio[1]:
        erros.append("Navio inativo.")

    aberta = conn.execute(
        "SELECT numero FROM viagem WHERE navio_id = ? AND status = 'aberta'",
        (navio_id,),
    ).fetchone()
    if aberta is not None:
        erros.append(
            "Este navio já tem a viagem {} aberta. "
            "Encerre-a antes de abrir outra.".format(aberta[0])
        )

    etapas = conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala, sentido, motivo, observacao "
        "  FROM rota_etapa WHERE rota_modelo_id = ? ORDER BY ordem",
        (rota_modelo_id,),
    ).fetchall()
    if not etapas:
        erros.append("Rota-modelo {} não tem etapas cadastradas.".format(rota_modelo_id))

    if erros:
        return None, erros

    numero = numero or _proximo_numero(conn, navio_id)
    quando = agora()
    abertura = _sailing_de_abertura(conn, navio_id)

    cur = conn.execute(
        "INSERT INTO viagem (navio_id, numero, rota_modelo_id, status, "
        "                    evento_abertura_id, aberta_por, aberta_em) "
        "VALUES (?, ?, ?, 'aberta', ?, ?, ?)",
        (navio_id, numero, rota_modelo_id,
         abertura[0] if abertura else None, por, quando),
    )
    viagem_id = cur.lastrowid

    # Primeira viagem do navio: nao ha sailing anterior de onde herdar. Cria-se
    # uma escala de abertura, que so pede o sailing.
    if abertura is None:
        conn.execute(
            "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
            "                    sentido, motivo, origem, criada_por, criada_em, observacao) "
            "VALUES (?, ?, ?, 'abertura', 'na', 'abertura', 'abertura', ?, ?, ?)",
            (viagem_id, ORDEM_ABERTURA, PORTO_CICLO, por, quando,
             "Saida que abre a primeira viagem deste navio. So o Sailing."),
        )

    for etapa in etapas:
        conn.execute(
            "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
            "                    sentido, motivo, origem, criada_por, criada_em, observacao) "
            "VALUES (?, ?, ?, ?, ?, ?, 'modelo', ?, ?, ?)",
            (viagem_id, etapa[0] * PASSO_ORDEM, etapa[1], etapa[2],
             etapa[3], etapa[4], por, quando, etapa[5]),
        )

    conn.commit()
    return viagem_id, []


# ---------------------------------------------------------------------------
# Escala extra — o eventual
# ---------------------------------------------------------------------------

def adicionar_escala_extra(
    conn,
    viagem_id: int,
    *,
    codigo_porto: str,
    tipo_escala: str,
    motivo: str,
    apos_ordem: int,
    sentido: str = "na",
    por: str | None = None,
    observacao: str | None = None,
) -> tuple[int | None, list[str]]:
    """Encaixa uma escala nao prevista (bunker em Icoaraci ou Itaqui) na posicao certa.

    Nasce com `origem = 'extra'`, e e isso que permite perguntar depois quantas
    viagens tiveram parada de bunker e quanto custaram em tempo.
    """
    erros: list[str] = []

    viagem = conn.execute(
        "SELECT status FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()
    if viagem is None:
        return None, ["Viagem {} não existe.".format(viagem_id)]
    if viagem[0] != "aberta":
        erros.append(
            "Viagem {} está {} — não aceita escala nova.".format(viagem_id, viagem[0])
        )

    if conn.execute(
        "SELECT 1 FROM porto WHERE codigo = ? AND ativo = 1", (codigo_porto,)
    ).fetchone() is None:
        erros.append("Porto {!r} não cadastrado ou inativo.".format(codigo_porto))
    if tipo_escala not in dominio.TIPOS_ESCALA or tipo_escala == "abertura":
        erros.append("Tipo de escala inválido: {!r}.".format(tipo_escala))
    if motivo not in dominio.MOTIVOS:
        erros.append("Motivo inválido: {!r}.".format(motivo))
    if sentido not in dominio.SENTIDOS:
        erros.append("Sentido inválido: {!r}.".format(sentido))
    if erros:
        return None, erros

    seguinte = conn.execute(
        "SELECT MIN(ordem) FROM escala WHERE viagem_id = ? AND ordem > ?",
        (viagem_id, apos_ordem),
    ).fetchone()[0]
    limite = seguinte if seguinte is not None else apos_ordem + 2 * PASSO_ORDEM
    nova_ordem = (apos_ordem + limite) // 2
    if nova_ordem <= apos_ordem or nova_ordem >= limite:
        return None, [
            "Não há espaço de ordenação entre estas escalas. "
            "Renumere a viagem antes de inserir outra aqui."
        ]

    cur = conn.execute(
        "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
        "                    sentido, motivo, origem, criada_por, criada_em, observacao) "
        "VALUES (?, ?, ?, ?, ?, ?, 'extra', ?, ?, ?)",
        (viagem_id, nova_ordem, codigo_porto, tipo_escala, sentido, motivo,
         por, agora(), observacao),
    )
    conn.commit()
    return cur.lastrowid, []


# ---------------------------------------------------------------------------
# Marcos
# ---------------------------------------------------------------------------

def lancar_marco(
    conn,
    escala_id: int,
    *,
    tipo: str,
    hora_local: str | None,
    nome_responsavel: str,
    registrado_por: str,
    offset: str | None = None,
    id_cliente: str | None = None,
    precisao: str = "exata",
    observacao: str | None = None,
    motivo_correcao: str | None = None,
) -> tuple[int | None, list[str]]:
    """Grava um marco. Se ja houver um vigente do mesmo tipo, e correcao.

    Correcao gera VERSAO nova e aposenta a anterior — nunca sobrescreve. O que
    importa numa contestacao e justamente saber que houve correcao, quando e por
    quem.

    `id_cliente` vem do celular. Reenvio do mesmo id devolve o mesmo evento sem
    erro: e o que torna a fila local inofensiva quando o servidor demora a
    acordar.
    """
    escala = conn.execute(
        "SELECT e.tipo_escala, e.status, p.offset_padrao, e.origem, e.viagem_id, "
        "       e.codigo_porto "
        "  FROM escala e JOIN porto p ON p.codigo = e.codigo_porto "
        " WHERE e.id = ?",
        (escala_id,),
    ).fetchone()
    if escala is None:
        return None, ["Escala {} não existe.".format(escala_id)]
    if escala[1] == "cancelada":
        return None, ["Escala cancelada não aceita lançamento."]

    id_cliente = id_cliente or str(uuid.uuid4())
    ja = conn.execute(
        "SELECT id FROM evento WHERE id_cliente = ?", (id_cliente,)
    ).fetchone()
    if ja is not None:
        return ja[0], []  # reenvio: o mesmo lancamento, nao um novo

    offset = (offset or escala[2]).strip()
    anteriores = marcos_vigentes(conn, escala_id)

    erros = dominio.erros_marco(
        tipo_escala=escala[0],
        tipo_evento=tipo,
        hora_local=hora_local,
        offset=offset,
        nome_responsavel=nome_responsavel,
        precisao=precisao,
        marcos_existentes={k: v for k, v in anteriores.items() if k != tipo},
    )
    if erros:
        return None, erros

    vigente_atual = conn.execute(
        "SELECT id, versao FROM evento WHERE escala_id = ? AND tipo = ? AND vigente = 1",
        (escala_id, tipo),
    ).fetchone()
    if vigente_atual is not None and not (motivo_correcao or "").strip():
        return None, [
            "Já existe {} nesta escala. Para alterar, informe o motivo da "
            "correção.".format(dominio.ROTULO_MARCO[tipo])
        ]

    hora_utc = dominio.para_utc(hora_local, offset) if precisao != "tbc" else None
    quando = agora()

    try:
        if vigente_atual is not None:
            conn.execute(
                "UPDATE evento SET vigente = 0 WHERE id = ?", (vigente_atual[0],)
            )
        cur = conn.execute(
            "INSERT INTO evento (id_cliente, escala_id, tipo, hora_local, offset_utc, "
            "                    hora_utc, precisao, registrado_por, registrado_em, "
            "                    nome_responsavel, observacao, versao, vigente, "
            "                    substitui_evento_id, motivo_correcao) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (id_cliente, escala_id, tipo, hora_local, offset, hora_utc, precisao,
             registrado_por, quando, nome_responsavel.strip(), observacao,
             (vigente_atual[1] + 1) if vigente_atual else 1,
             vigente_atual[0] if vigente_atual else None,
             (motivo_correcao or "").strip() or None),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return None, ["Conflito ao gravar o marco: {}".format(exc)]

    # O sailing da escala de abertura E o evento-ancora da viagem. Sem amarrar
    # aqui, a primeira viagem de um navio fica para sempre sem hora de inicio.
    if escala[3] == "abertura" and tipo == "sailing":
        conn.execute(
            "UPDATE viagem SET evento_abertura_id = ? WHERE id = ?",
            (cur.lastrowid, escala[4]),
        )

    conn.commit()

    # O sailing de Alumar da escala de descarga fecha esta viagem e abre a
    # seguinte. Feito aqui, o comandante nunca precisa pensar em "viagem": ele
    # so ve a proxima parada esperando horario.
    if escala[5] == PORTO_CICLO and escala[3] == "modelo" and tipo == "sailing":
        encadear_ciclo(conn, escala[4])

    return cur.lastrowid, []


# ---------------------------------------------------------------------------
# Encerramento
# ---------------------------------------------------------------------------

def encadear_ciclo(conn, viagem_id: int) -> tuple[int | None, list[str]]:
    """Fecha a viagem e abre a proxima, com as escalas do modelo ja criadas.

    Silencioso de proposito: o marco em si foi gravado com sucesso, e devolver
    o tropeco do encadeamento como erro faria o comandante achar que perdeu o
    lancamento. Se faltar o unberth, a viagem simplesmente nao fecha e a
    pendencia aparece na fila `escalas_incompletas` — que e onde ela tem de
    aparecer.
    """
    linha = conn.execute(
        "SELECT navio_id, status FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()
    if linha is None or linha[1] != "aberta":
        return None, []

    fechou, avisos = encerrar_viagem(conn, viagem_id)
    if not fechou:
        return None, avisos

    nova, erros = abrir_viagem(conn, linha[0], por="sistema")
    return nova, avisos + erros

def encerrar_viagem(conn, viagem_id: int) -> tuple[bool, list[str]]:
    """Fecha a viagem no `unberth` da escala de Alumar.

    O `sailing` da mesma escala fica de fora de proposito: e ele que vai abrir a
    proxima viagem.
    """
    viagem = conn.execute(
        "SELECT status FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()
    if viagem is None:
        return False, ["Viagem {} não existe.".format(viagem_id)]
    if viagem[0] != "aberta":
        return False, ["Viagem já está {}.".format(viagem[0])]

    ancora = conn.execute(
        "SELECT ev.id "
        "  FROM evento ev JOIN escala e ON e.id = ev.escala_id "
        " WHERE e.viagem_id = ? AND e.codigo_porto = ? "
        "   AND e.origem <> 'abertura' "
        "   AND ev.tipo = 'unberth' AND ev.vigente = 1 "
        " ORDER BY e.ordem DESC LIMIT 1",
        (viagem_id, PORTO_CICLO),
    ).fetchone()
    if ancora is None:
        return False, [
            "Falta o Unberth de {} — é ele que encerra a viagem.".format(PORTO_CICLO)
        ]

    faltantes = conn.execute(
        "SELECT COUNT(*) FROM escalas_incompletas WHERE viagem_id = ?", (viagem_id,)
    ).fetchone()[0]

    conn.execute(
        "UPDATE viagem SET status = 'encerrada', evento_encerramento_id = ? WHERE id = ?",
        (ancora[0], viagem_id),
    )
    conn.execute(
        "UPDATE escala SET status = 'encerrada' WHERE viagem_id = ? AND status = 'aberta'",
        (viagem_id,),
    )
    conn.commit()

    avisos = []
    if faltantes:
        avisos.append(
            "Viagem encerrada com {} marco(s) ainda em falta. "
            "Confira a fila de escalas incompletas.".format(faltantes)
        )
    return True, avisos
