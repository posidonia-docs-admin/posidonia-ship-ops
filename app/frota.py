# -*- coding: utf-8 -*-
"""A frota agora: em que pernada cada navio esta, e ha quanto tempo.

Tudo aqui se deduz do ULTIMO marco lancado da viagem em curso. Um Sailing
diz que o navio esta navegando para a proxima parada; um Arrival numa parada
que atraca diz que esta esperando berco; um Berth diz que esta atracado. Nao
ha campo "posicao" a preencher — a posicao e consequencia do que foi lancado,
e e por isso que ela nunca discorda dos marcos.
"""
from __future__ import annotations

from . import dominio


def _sequencia(blocos):
    """[(bloco, marco)] na ordem em que os marcos acontecem na viagem."""
    return [(b, m) for b in blocos for m in b["marcos"]]


def ultimo_lancado(blocos):
    """(bloco, marco) do ultimo marco lancado, pela ORDEM da viagem, ou None.

    Pela ordem e nao pela hora: se o comandante pulou o Sailing de Barra
    Norte e lancou o Arrival em Alumar, o navio esta em Alumar — e o Sailing
    pulado aparece como marco em falta, nao como posicao.
    """
    ultimo = None
    for par in _sequencia(blocos):
        if par[1]["lancado"]:
            ultimo = par
    return ultimo


def marcos_pulados(blocos) -> list[str]:
    """Marcos nao lancados que ja tem um marco POSTERIOR lancado.

    E a diferenca entre "ainda nao aconteceu" e "ficou para tras". A fila
    `escalas_incompletas` conta os dois; aqui so o segundo, que e o que a
    supervisora precisa cobrar.
    """
    seq = _sequencia(blocos)
    lancados = [i for i, (_b, m) in enumerate(seq) if m["lancado"]]
    if not lancados:
        return []          # nada lancado: nada ficou para tras (e seq[:-1] mentiria)
    return ["{} · {}".format(m["curto"], b["escala"]["porto_nome"])
            for b, m in seq[:max(lancados)] if not m["lancado"]]


def pernada_atual(blocos) -> dict:
    """Onde o navio esta agora, em palavras, e o marco que sustenta a frase."""
    par = ultimo_lancado(blocos)
    if par is None:
        return {"titulo": "Aguardando saída de Alumar", "detalhe": "viagem nova",
                "condicao": blocos[0]["escala"]["condicao"] if blocos else None,
                "ultimo": None}

    bloco, marco = par
    escala = bloco["escala"]
    porto = escala["porto_nome"]
    motivo = dominio.ROTULO_MOTIVO.get(escala["motivo"], escala["motivo"])
    tipo = marco["tipo"]

    if tipo == "sailing":
        i = blocos.index(bloco)
        seguinte = blocos[i + 1]["escala"] if i + 1 < len(blocos) else None
        if seguinte is None:
            titulo, detalhe = "Saiu de {}".format(porto), ""
        else:
            titulo = "Navegando {} → {}".format(porto, seguinte["porto_nome"])
            detalhe = seguinte["sentido"] if seguinte["sentido"] != "na" else ""
        condicao = seguinte["condicao"] if seguinte else escala["condicao"]
    elif tipo == "arrival":
        if escala["tipo_escala"] in ("operacional", "encerramento"):
            titulo, detalhe = "Aguardando berço em {}".format(porto), motivo
        elif escala["tipo_escala"] == "fundeio" or escala["motivo"] == "espera_mare":
            titulo, detalhe = "Fundeado em {}".format(porto), motivo
        else:
            titulo, detalhe = "Em {}".format(porto), motivo
        condicao = escala["condicao"]
    elif tipo == "berth":
        detalhe = {"loading": "carregando", "discharging": "descarregando",
                   "bunkering": "abastecendo"}.get(escala["condicao"] or "", motivo)
        titulo, condicao = "Atracado em {}".format(porto), escala["condicao"]
    else:  # unberth
        titulo, detalhe, condicao = "Desatracado em {}".format(porto), "aguardando saída", escala["condicao"]

    return {
        "titulo": titulo, "detalhe": detalhe, "condicao": condicao,
        "ultimo": {"curto": marco["curto"], "porto": porto,
                   "hora_local": marco["lancado"]["hora_local"],
                   "hora_utc": marco["lancado"]["hora_utc"]},
    }
