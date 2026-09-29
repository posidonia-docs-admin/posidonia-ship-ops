# -*- coding: utf-8 -*-
"""O Statement of Facts (SOF) de uma parada: os marcos além dos quatro do circuito
e os dados numéricos da operação.

Os quatro marcos do circuito (Arrival, Berth, Unberth, Sailing) seguem em
`evento`: são eles que abrem e fecham viagem, medem pernada e alimentam a
análise. O SOF acrescenta o que a agência registra por fora — NOR, prático a
bordo, primeiro cabo, início e fim de operação, draft survey, cabos soltos —
e os números da operação: calados, taxa de carga, tempo trabalhado e parado,
carga por porão, carga remanescente.

A LISTA do que existe mora em app/listas.py (MARCOS_SOF, DADOS_SOF, PORÕES):
acrescentar um marco ou um dado é acrescentar uma linha lá. Por isso os dados
numéricos ficam num JSON por parada, e não numa coluna por dado.
"""
from __future__ import annotations

import json
import re

from . import dominio, listas
from .db import agora

CHAVES_MARCOS = {chave: (rotulo, onde) for chave, rotulo, onde in listas.MARCOS_SOF}
CHAVES_DADOS = {chave: (rotulo, unidade) for chave, rotulo, unidade in listas.DADOS_SOF}
CHAVES_DADOS.update({"porao_{}".format(i): ("Carga no porão {}".format(i), "MT")
                     for i in range(1, listas.PORÕES + 1)})

ATRACA = ("operacional", "encerramento")


def marcos_para(tipo_escala: str) -> list[tuple[str, str]]:
    """Os marcos do SOF que fazem sentido neste tipo de parada."""
    return [(chave, rotulo) for chave, rotulo, onde in listas.MARCOS_SOF
            if onde == "todas" or (onde == "atraca" and tipo_escala in ATRACA)]


def registrar_marco(conn, escala_id: int, *, tipo: str, hora_local: str | None, offset: str | None,
                    nome_responsavel: str, registrado_por: str,
                    observacao: str | None = None) -> tuple[int | None, list[str]]:
    """Um marco do SOF na parada. Repetir o mesmo marco substitui — o SOF tem um
    horário por evento, e corrigir é o caso comum."""
    erros: list[str] = []
    escala = conn.execute(
        "SELECT e.tipo_escala, e.status, p.offset_padrao FROM escala e "
        "  JOIN porto p ON p.codigo = e.codigo_porto WHERE e.id = ?", (escala_id,)).fetchone()
    if escala is None:
        return None, ["Escala {} não existe.".format(escala_id)]
    if escala["status"] == "cancelada":
        return None, ["Escala cancelada não aceita lançamento."]
    if tipo not in CHAVES_MARCOS:
        return None, ["Marco do SOF desconhecido: {!r}.".format(tipo)]
    if CHAVES_MARCOS[tipo][1] == "atraca" and escala["tipo_escala"] not in ATRACA:
        erros.append("{} só existe em parada que atraca.".format(CHAVES_MARCOS[tipo][0]))
    if not (nome_responsavel or "").strip():
        erros.append("Informe quem preenche.")
    if not (hora_local or "").strip():
        erros.append("Informe a data e a hora.")
    if erros:
        return None, erros
    offset = (offset or escala["offset_padrao"]).strip()
    try:
        hora_utc = dominio.para_utc(hora_local, offset)
    except ValueError:
        return None, ["Data/hora ou fuso inválidos: {!r} {!r}.".format(hora_local, offset)]

    quando = agora()
    conn.execute(
        "INSERT INTO sof_marco (escala_id, tipo, hora_local, offset_utc, hora_utc, registrado_por, "
        "                       registrado_em, nome_responsavel, observacao) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(escala_id, tipo) DO UPDATE SET hora_local = excluded.hora_local, "
        "  offset_utc = excluded.offset_utc, hora_utc = excluded.hora_utc, "
        "  registrado_por = excluded.registrado_por, registrado_em = excluded.registrado_em, "
        "  nome_responsavel = excluded.nome_responsavel, observacao = excluded.observacao",
        (escala_id, tipo, hora_local.strip(), offset, hora_utc, registrado_por, quando,
         nome_responsavel.strip(), (observacao or "").strip() or None))
    conn.commit()
    linha = conn.execute("SELECT id FROM sof_marco WHERE escala_id = ? AND tipo = ?",
                         (escala_id, tipo)).fetchone()
    return linha[0], []


def registrar_dados(conn, escala_id: int, *, dados: dict, nome_responsavel: str,
                    registrado_por: str) -> tuple[bool, list[str]]:
    """Os numeros do SOF da parada. Mescla com o que ja havia: mandar so o
    calado nao apaga a carga por porao gravada antes."""
    erros: list[str] = []
    escala = conn.execute("SELECT status FROM escala WHERE id = ?", (escala_id,)).fetchone()
    if escala is None:
        return False, ["Escala {} não existe.".format(escala_id)]
    if escala["status"] == "cancelada":
        return False, ["Escala cancelada não aceita lançamento."]
    if not (nome_responsavel or "").strip():
        erros.append("Informe quem preenche.")
    limpos = {}
    for chave, valor in (dados or {}).items():
        if chave not in CHAVES_DADOS:
            erros.append("Dado do SOF desconhecido: {!r}.".format(chave))
            continue
        texto = str(valor).strip().replace(".", "").replace(",", ".") if "," in str(valor) else str(valor).strip()
        if texto == "":
            continue
        try:
            numero = float(texto)
        except ValueError:
            erros.append("{} precisa ser um número.".format(CHAVES_DADOS[chave][0]))
            continue
        if numero < 0:
            erros.append("{} não pode ser negativo.".format(CHAVES_DADOS[chave][0]))
            continue
        limpos[chave] = numero
    if erros:
        return False, erros
    if not limpos:
        return False, ["Informe pelo menos um dado."]

    atual = conn.execute("SELECT dados FROM sof_escala WHERE escala_id = ?", (escala_id,)).fetchone()
    mesclado = json.loads(atual[0]) if atual else {}
    mesclado.update(limpos)
    conn.execute(
        "INSERT INTO sof_escala (escala_id, dados, registrado_por, registrado_em, nome_responsavel) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(escala_id) DO UPDATE SET dados = excluded.dados, "
        "  registrado_por = excluded.registrado_por, registrado_em = excluded.registrado_em, "
        "  nome_responsavel = excluded.nome_responsavel",
        (escala_id, json.dumps(mesclado, ensure_ascii=False), registrado_por, agora(),
         nome_responsavel.strip()))
    conn.commit()
    return True, []


def marcos_da_escala(conn, escala_id: int) -> list[dict]:
    return [{"tipo": r["tipo"], "rotulo": CHAVES_MARCOS.get(r["tipo"], (r["tipo"],))[0],
             "hora_local": r["hora_local"], "hora_utc": r["hora_utc"],
             "nome_responsavel": r["nome_responsavel"], "observacao": r["observacao"]}
            for r in conn.execute(
                "SELECT tipo, hora_local, hora_utc, nome_responsavel, observacao FROM sof_marco "
                " WHERE escala_id = ? ORDER BY hora_utc", (escala_id,))]


def dados_da_escala(conn, escala_id: int) -> list[dict]:
    linha = conn.execute("SELECT dados FROM sof_escala WHERE escala_id = ?", (escala_id,)).fetchone()
    if not linha:
        return []
    dados = json.loads(linha[0])
    saida = []
    for chave, (rotulo, unidade) in CHAVES_DADOS.items():
        if chave in dados:
            saida.append({"chave": chave, "rotulo": rotulo, "unidade": unidade, "valor": dados[chave]})
    return saida


def campos_dados() -> list[dict]:
    """Os campos do formulario, na ordem da lista."""
    return [{"chave": chave, "rotulo": rotulo, "unidade": unidade} for chave, (rotulo, unidade) in CHAVES_DADOS.items()]


def eh_chave_valida(chave: str) -> bool:
    return bool(re.fullmatch(r"[a-z_0-9]+", chave or "")) and chave in CHAVES_DADOS
