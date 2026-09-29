# -*- coding: utf-8 -*-
"""Importação de viagens passadas pela tela da supervisão.

Dois formatos entram:

  * A PLANILHA no layout "Análise Viagens" das supervisoras (o Template Bianca:
    uma aba por navio, blocos por parada) — lida por ferramentas/historico_planilha.
  * Um CSV PLANO, uma linha por marco, com as mesmas informações da planilha
    postas em colunas (ver MODELO_CSV). É o formato para quem prefere montar
    numa tabela simples.

O caminho é sempre em dois passos: primeiro o arquivo é LIDO e o relatório
aparece (inventário, erros, avisos, o que é novo e o que já existe); só a
confirmação grava. Viagem que já existe no banco não é tocada; viagem em curso
só entra se o navio não tiver outra aberta.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import pathlib
import re
import time
import uuid

from . import config, dominio
from ferramentas import historico_planilha as hp
from ferramentas import importar_historico as imp

MARCOS = {"ARRIVAL": "arrival", "CHEGADA": "arrival", "BERTH": "berth", "ATRACACAO": "berth",
          "UNBERTH": "unberth", "DESATRACACAO": "unberth", "SAILING": "sailing", "SAIDA": "sailing"}
COLUNAS = {
    "navio": ("NAVIO", "EMBARCACAO"), "viagem": ("VIAGEM", "VOY", "CODIGO"),
    "porto": ("PORTO",), "sentido": ("SENTIDO",), "marco": ("MARCO", "ACTION", "ACAO"),
    "data": ("DATA",), "hora": ("HORA",),
    "rob_vlsfo": ("ROB VLSFO", "VLSFO"), "rob_mgo": ("ROB MGO", "MGO"), "fw": ("FW", "AGUA DOCE"),
    "carregado": ("CARREGADO (MT)", "CARREGADO"), "descarregado": ("DESCARREGADO (MT)", "DESCARREGADO"),
    "abastecido_vlsfo": ("ABASTECIDO VLSFO",), "abastecido_mgo": ("ABASTECIDO MGO",),
    "observacao": ("OBSERVACAO", "NOTAS", "OBS"),
}
CABECALHO_MODELO = ["Navio", "Viagem", "Porto", "Sentido", "Marco", "Data", "Hora", "ROB VLSFO",
                    "ROB MGO", "FW", "Carregado (MT)", "Descarregado (MT)", "Abastecido VLSFO",
                    "Abastecido MGO", "Observação"]
EXEMPLO_MODELO = [
    ["Amazon Pathfinder", "APT26018", "Alumar", "", "Sailing", "12/09/2026", "03:10", "1402,5", "110,2", "180", "", "", "", "", "Saída de Alumar"],
    ["Amazon Pathfinder", "APT26018", "Icoaraci", "subida", "Arrival", "13/09/2026", "10:00", "1002,0", "80,0", "", "", "", "400", "40", "Abastecimento de 400 t VLSFO e 40 t MGO"],
    ["Amazon Pathfinder", "APT26018", "Icoaraci", "subida", "Sailing", "13/09/2026", "18:00", "1400,0", "120,0", "", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Fazendinha", "subida", "Arrival", "15/09/2026", "02:15", "1370,1", "108,0", "175", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Fazendinha", "subida", "Sailing", "15/09/2026", "03:00", "", "", "", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Juruti", "", "Arrival", "16/09/2026", "20:00", "1301,7", "99,5", "160", "", "", "", "", "PDA recebida 10/09"],
    ["Amazon Pathfinder", "APT26018", "Juruti", "", "Berth", "16/09/2026", "22:00", "", "", "", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Juruti", "", "Unberth", "18/09/2026", "01:30", "", "", "", "58200", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Juruti", "", "Sailing", "18/09/2026", "03:00", "1274,8", "96,1", "150", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Alumar", "", "Arrival", "21/09/2026", "19:30", "1220,4", "91,0", "140", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Alumar", "", "Berth", "23/09/2026", "08:00", "", "", "", "", "", "", "", ""],
    ["Amazon Pathfinder", "APT26018", "Alumar", "", "Unberth", "25/09/2026", "01:00", "1610,0", "130,1", "120", "", "57900", "", "", "Encerra a viagem"],
    ["Amazon Pathfinder", "APT26018", "Alumar", "", "Sailing", "25/09/2026", "03:00", "", "", "", "", "", "", "", "Abre a viagem seguinte"],
]
PASTA = pathlib.Path(config.DIR_DADOS) / "importacoes"
VALIDADE = 24 * 3600


def modelo_csv() -> str:
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    w.writerow(CABECALHO_MODELO)
    for linha in EXEMPLO_MODELO:
        w.writerow(linha)
    return "﻿" + saida.getvalue()


# ---------------------------------------------------------------------------
# Guarda do arquivo entre os dois passos
# ---------------------------------------------------------------------------

def guardar(nome: str, conteudo: bytes) -> str:
    PASTA.mkdir(parents=True, exist_ok=True)
    agora = time.time()
    for velho in PASTA.glob("*"):
        try:
            if agora - velho.stat().st_mtime > VALIDADE:
                velho.unlink()
        except OSError:
            pass
    ext = ".xlsx" if nome.lower().endswith((".xlsx", ".xlsm")) else ".csv"
    chave = uuid.uuid4().hex
    (PASTA / (chave + ext)).write_bytes(conteudo)
    return chave + ext


def caminho_de(chave: str) -> pathlib.Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}\.(csv|xlsx)", chave or ""):
        return None
    p = PASTA / chave
    return p if p.exists() else None


def descartar(chave: str) -> None:
    p = caminho_de(chave)
    if p:
        try:
            p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _mapas(conn) -> dict:
    navios = {}
    for r in conn.execute("SELECT alias, navio_id FROM navio_alias"):
        navios[r[0]] = r[1]
    for r in conn.execute("SELECT id, nome_oficial, prefixo FROM navio"):
        navios[dominio.normalizar(r[1])] = r[0]
        if r[2]:
            navios["PREFIXO:" + r[2]] = r[0]
    portos = {r[0]: r[1] for r in conn.execute("SELECT alias, codigo_porto FROM porto_alias")}
    for r in conn.execute("SELECT codigo, nome FROM porto"):
        portos[dominio.normalizar(r[1])] = r[0]
        portos[r[0]] = r[0]
    return {"navios": navios, "portos": portos}


def _numero(texto):
    t = (texto or "").strip().replace(".", "").replace(",", ".") if "," in (texto or "") else (texto or "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _quando(data: str, hora: str):
    data, hora = (data or "").strip(), (hora or "").strip()
    if not data:
        return None, "exata"
    d = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            d = dt.datetime.strptime(data, fmt)
            break
        except ValueError:
            continue
    if d is None:
        return None, "exata"
    if hora:
        try:
            h = dt.datetime.strptime(hora[:5], "%H:%M")
            return d.replace(hour=h.hour, minute=h.minute), "exata"
        except ValueError:
            return None, "exata"
    return d, "apenas_data"


def ler_csv(caminho, mapas: dict) -> tuple[dict[int, list[hp.Viagem]], list[hp.Problema]]:
    """O CSV plano (uma linha por marco) no mesmo modelo que a planilha produz."""
    texto = pathlib.Path(caminho).read_bytes().decode("utf-8-sig", errors="replace")
    amostra = texto[:2000]
    sep = ";" if amostra.count(";") >= amostra.count(",") else ","
    leitor = csv.reader(io.StringIO(texto), delimiter=sep)
    problemas: list[hp.Problema] = []
    aba = "CSV"
    try:
        cabecalho = next(leitor)
    except StopIteration:
        return {}, [hp.Problema("erro", aba, None, 1, "arquivo vazio")]

    indice = {}
    for i, nome in enumerate(cabecalho):
        n = dominio.normalizar(nome)
        for chave, aceitos in COLUNAS.items():
            if n in aceitos and chave not in indice:
                indice[chave] = i
    for obrigatoria in ("navio", "viagem", "porto", "marco", "data"):
        if obrigatoria not in indice:
            problemas.append(hp.Problema("erro", aba, None, 1,
                                         "falta a coluna '{}' no cabeçalho".format(obrigatoria)))
    if any(p.nivel == "erro" for p in problemas):
        return {}, problemas

    def campo(linha, chave):
        i = indice.get(chave)
        return linha[i].strip() if i is not None and i < len(linha) else ""

    viagens: dict[tuple, hp.Viagem] = {}
    ordem: list[tuple] = []
    for n, linha in enumerate(leitor, start=2):
        if not any(c.strip() for c in linha):
            continue
        navio_txt = dominio.normalizar(campo(linha, "navio"))
        codigo = campo(linha, "viagem").upper()
        navio_id = mapas["navios"].get(navio_txt)
        if navio_id is None and hp.RE_CODIGO.match(codigo):
            navio_id = mapas["navios"].get("PREFIXO:" + codigo[:3])
        if navio_id is None:
            problemas.append(hp.Problema("erro", aba, codigo or None, n,
                                         "navio não reconhecido: {!r}".format(campo(linha, "navio"))))
            continue
        if not hp.RE_CODIGO.match(codigo):
            problemas.append(hp.Problema("erro", aba, codigo or None, n,
                                         "código de viagem fora do padrão (ex.: APT26018): {!r}".format(codigo)))
            continue
        porto = mapas["portos"].get(dominio.normalizar(campo(linha, "porto")))
        if porto is None:
            problemas.append(hp.Problema("erro", aba, codigo, n,
                                         "porto não reconhecido: {!r}".format(campo(linha, "porto"))))
            continue
        marco = MARCOS.get(dominio.normalizar(campo(linha, "marco")))
        if marco is None:
            problemas.append(hp.Problema("erro", aba, codigo, n,
                                         "marco não reconhecido (Arrival, Berth, Unberth ou Sailing): {!r}".format(
                                             campo(linha, "marco"))))
            continue
        quando, precisao = _quando(campo(linha, "data"), campo(linha, "hora"))
        if quando is None:
            problemas.append(hp.Problema("erro", aba, codigo, n, "data ou hora ilegível: {!r} {!r}".format(
                campo(linha, "data"), campo(linha, "hora"))))
            continue
        if precisao == "apenas_data":
            problemas.append(hp.Problema("aviso", aba, codigo, n, "marco sem hora: gravado como 'apenas data'"))

        chave = (navio_id, codigo)
        if chave not in viagens:
            viagens[chave] = hp._nova_viagem(navio_id, codigo, aba, n)
            ordem.append(chave)
        v = viagens[chave]
        sentido = dominio.normalizar(campo(linha, "sentido")).lower()

        # A escala desta linha, pela ordem natural da viagem.
        if porto == "ALUMAR":
            enc = v.escalas["encerramento"]
            if marco == "sailing" and "sailing" not in v.escalas["abertura"].marcos and not enc.marcos:
                escala = v.escalas["abertura"]
            elif marco == "sailing":
                v.saida_registrada = hp.Marco(quando, n, precisao, "sailing")
                escala = None
            else:
                escala = enc
        elif porto == "JURUTI":
            escala = v.escalas["juruti"]
        elif porto == "FAZENDINHA":
            if sentido == "descida" or (not sentido and v.escalas["juruti"].marcos):
                escala = v.escalas["faz_descida"]
            else:
                escala = v.escalas["faz_subida"]
        elif porto == "BARRA_NORTE" and sentido != "subida":
            escala = v.escalas["barra_norte"]
        else:
            escala = next((e for e in v.extras if e.porto == porto and marco not in e.marcos), None)
            if escala is None:
                escala = hp.Escala("extra", linha=n, porto=porto,
                                   motivo="espera_mare" if porto == "BARRA_NORTE" else "bunker")
                v.extras.append(escala)

        if escala is not None:
            if marco in escala.marcos:
                problemas.append(hp.Problema("erro", aba, codigo, n, "{} tem dois {}".format(escala.rotulo, marco)))
                continue
            m = hp.Marco(quando, n, precisao, marco)
            m.rob_vlsfo, m.rob_mgo, m.fw = (_numero(campo(linha, "rob_vlsfo")), _numero(campo(linha, "rob_mgo")),
                                            _numero(campo(linha, "fw")))
            escala.marcos[marco] = m
            escala.linha = escala.linha or n
            obs = campo(linha, "observacao")
            if obs:
                escala.notas.append(obs)
            carregado, descarregado = _numero(campo(linha, "carregado")), _numero(campo(linha, "descarregado"))
            if carregado is not None and escala.chave == "juruti":
                escala.tonelagem = carregado
            if descarregado is not None and escala.chave == "encerramento":
                escala.tonelagem = descarregado
            ab_v, ab_m = _numero(campo(linha, "abastecido_vlsfo")), _numero(campo(linha, "abastecido_mgo"))
            if ab_v is not None or ab_m is not None:
                escala.abastecimento = (ab_v, ab_m, "CSV linha {}".format(n))

    por_navio: dict[int, list[hp.Viagem]] = {}
    for chave in ordem:
        por_navio.setdefault(chave[0], []).append(viagens[chave])
    for navio_id, lista in por_navio.items():
        ultima = lista[-1]
        ultima.aberta = ("unberth" not in ultima.escalas["encerramento"].marcos
                         and ultima.saida_registrada is None)
        hp._aplicar_decisoes(lista, aba, problemas)
        hp._validar(lista, aba, navio_id, problemas)
    return por_navio, problemas


def ler(caminho, conn) -> tuple[dict[int, list[hp.Viagem]], list[hp.Problema]]:
    caminho = pathlib.Path(caminho)
    if caminho.suffix.lower() in (".xlsx", ".xlsm"):
        return hp.ler(caminho)
    return ler_csv(caminho, _mapas(conn))


# ---------------------------------------------------------------------------
# O plano e a gravação
# ---------------------------------------------------------------------------

def plano(conn, viagens_por_navio) -> list[dict]:
    """Uma linha por viagem lida: nova, já existe, ou conflito (em curso com outra aberta)."""
    existentes = {(r[0], r[1]): r[2] for r in conn.execute("SELECT navio_id, numero, status FROM viagem")}
    abertas = {r[0] for r in conn.execute("SELECT navio_id FROM viagem WHERE status = 'aberta'")}
    nomes = {r[0]: r[1] for r in conn.execute("SELECT id, nome_oficial FROM navio")}
    saida = []
    for navio_id, viagens in sorted(viagens_por_navio.items()):
        for v in viagens:
            marcos = sum(1 for _ in v.todos_os_marcos())
            if (navio_id, v.codigo) in existentes:
                situacao, motivo = "existe", "já está no sistema ({}) — não é tocada".format(
                    existentes[(navio_id, v.codigo)])
            elif v.aberta and navio_id in abertas:
                situacao, motivo = "conflito", "está em curso no arquivo, mas o navio já tem viagem aberta"
            else:
                situacao, motivo = "nova", "em curso" if v.aberta else "encerrada"
            saida.append({"navio": nomes.get(navio_id, navio_id).title(), "codigo": v.codigo,
                          "marcos": marcos, "extras": len(v.extras), "situacao": situacao, "motivo": motivo,
                          "viagem": v})
    return saida


def importar(conn, viagens_por_navio, *, por: str) -> dict:
    """Grava só as viagens NOVAS do plano. Devolve contagens."""
    itens = plano(conn, viagens_por_navio)
    imp._garantir_conta(conn)
    gravadas, puladas = [], []
    for item in itens:
        if item["situacao"] != "nova":
            puladas.append(item["codigo"])
            continue
        imp.gravar_viagem(conn, item["viagem"], forcar_encerrada=not item["viagem"].aberta, por=por)
        gravadas.append(item["codigo"])
    conn.commit()
    return {"gravadas": gravadas, "puladas": puladas}
