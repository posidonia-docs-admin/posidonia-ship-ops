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


def _base_numero(conn, navio_id: int) -> str:
    """PREFIXO + ano com 2 digitos. Ex.: APN26."""
    linha = conn.execute(
        "SELECT prefixo FROM navio WHERE id = ?", (navio_id,)).fetchone()
    prefixo = ((linha["prefixo"] if linha else "") or "").strip().upper()
    if not prefixo:
        # Navio novo ainda sem prefixo cadastrado: nao inventa sigla, usa o id.
        prefixo = "N{}".format(navio_id)
    return "{}{}".format(prefixo, agora()[2:4])


def _no_padrao(numero: str, base: str) -> bool:
    resto = (numero or "")[len(base):]
    return (numero or "").startswith(base) and len(resto) == 3 and resto.isdigit()


def _proximo_numero(conn, navio_id: int) -> str:
    """Codigo da viagem: PREFIXO + ano com 2 digitos + sequencia de 3.

        APN26001, APN26002 ...   Pioneer, 2026
        ACM26001                 Commander
        APT26001                 Pathfinder
        ACR26001                 Courage

    A sequencia reinicia a cada ano e e por navio. Usa MAX e nao COUNT: se uma
    viagem for cancelada, contar daria um numero ja usado.
    """
    base = _base_numero(conn, navio_id)
    (maior,) = conn.execute(
        "SELECT MAX(CAST(substr(numero, ?) AS INTEGER)) FROM viagem "
        " WHERE navio_id = ? AND numero LIKE ?",
        (len(base) + 1, navio_id, base + "%"),
    ).fetchone()
    return "{}{:03d}".format(base, (maior or 0) + 1)


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
        "SELECT ordem, codigo_porto, tipo_escala, sentido, motivo, condicao, observacao "
        "  FROM rota_etapa WHERE rota_modelo_id = ? ORDER BY ordem",
        (rota_modelo_id,),
    ).fetchall()
    if not etapas:
        erros.append("Rota-modelo {} não tem etapas cadastradas.".format(rota_modelo_id))

    if erros:
        return None, erros

    numero = numero or _proximo_numero(conn, navio_id)
    quando = agora()

    cur = conn.execute(
        "INSERT INTO viagem (navio_id, numero, rota_modelo_id, status, "
        "                    aberta_por, aberta_em) "
        "VALUES (?, ?, ?, 'aberta', ?, ?)",
        (navio_id, numero, rota_modelo_id, por, quando),
    )
    viagem_id = cur.lastrowid

    # Toda viagem nasce igual: a primeira etapa e a saida de Alumar. Nao ha
    # caso especial de "primeira viagem do navio" — o Sailing de Alumar e
    # sempre o primeiro lancamento DESTA viagem, nunca resto da anterior.
    for etapa in etapas:
        conn.execute(
            "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
            "                    sentido, motivo, origem, criada_por, criada_em, "
            "                    observacao, condicao) "
            "VALUES (?, ?, ?, ?, ?, ?, 'modelo', ?, ?, ?, ?)",
            (viagem_id, etapa["ordem"] * PASSO_ORDEM, etapa["codigo_porto"],
             etapa["tipo_escala"], etapa["sentido"], etapa["motivo"], por, quando,
             etapa["observacao"], etapa["condicao"]),
        )

    conn.commit()
    return viagem_id, []


def normalizar_viagens_vazias(conn) -> int:
    """Poe no padrao atual as viagens abertas que ainda nao tem marco nenhum.

    Duas coisas podem estar fora do padrao numa viagem aberta antes de uma
    mudanca: o CODIGO (se nasceu antes de APT26001 existir) e as ESCALAS (se a
    rota-modelo mudou). Sem nenhum evento apontando para elas, refazer as duas
    e seguro. Depois do primeiro lancamento, nunca mais se mexe.
    """
    alvos = conn.execute(
        "SELECT vg.id, vg.numero, vg.navio_id, vg.rota_modelo_id, vg.aberta_por "
        "  FROM viagem vg "
        " WHERE vg.status = 'aberta' "
        "   AND NOT EXISTS (SELECT 1 FROM evento ev "
        "                     JOIN escala e ON e.id = ev.escala_id "
        "                    WHERE e.viagem_id = vg.id)").fetchall()
    tocadas = 0

    for viagem in alvos:
        mudou = False

        base = _base_numero(conn, viagem["navio_id"])
        # Codigo ja no padrao nao se mexe: renumerar um valido o empurraria
        # para a frente a cada arranque.
        if not _no_padrao(viagem["numero"], base):
            novo = _proximo_numero(conn, viagem["navio_id"])
            if novo != viagem["numero"]:
                conn.execute("UPDATE viagem SET numero = ? WHERE id = ?",
                             (novo, viagem["id"]))
                mudou = True

        etapas = conn.execute(
            "SELECT ordem, codigo_porto, tipo_escala, sentido, motivo, condicao, "
            "       observacao FROM rota_etapa WHERE rota_modelo_id = ? ORDER BY ordem",
            (viagem["rota_modelo_id"] or 1,)).fetchall()
        atuais = conn.execute(
            "SELECT ordem, codigo_porto, tipo_escala FROM escala "
            " WHERE viagem_id = ? AND origem = 'modelo' ORDER BY ordem",
            (viagem["id"],)).fetchall()

        esperadas = [(e["ordem"] * PASSO_ORDEM, e["codigo_porto"], e["tipo_escala"])
                     for e in etapas]
        if [tuple(a) for a in atuais] != esperadas:
            conn.execute("DELETE FROM escala WHERE viagem_id = ?", (viagem["id"],))
            quando = agora()
            for etapa in etapas:
                conn.execute(
                    "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
                    "                    sentido, motivo, origem, criada_por, "
                    "                    criada_em, observacao, condicao) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'modelo', ?, ?, ?, ?)",
                    (viagem["id"], etapa["ordem"] * PASSO_ORDEM, etapa["codigo_porto"],
                     etapa["tipo_escala"], etapa["sentido"], etapa["motivo"],
                     viagem["aberta_por"], quando, etapa["observacao"],
                     etapa["condicao"]))
            conn.execute("UPDATE viagem SET evento_abertura_id = NULL, "
                         "evento_encerramento_id = NULL WHERE id = ?", (viagem["id"],))
            mudou = True

        if mudou:
            tocadas += 1

    if tocadas:
        conn.commit()
    return tocadas


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
    condicao: str | None = None,
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
    if condicao is not None and condicao not in dominio.CONDICOES:
        erros.append("Condição inválida: {!r}.".format(condicao))
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

    # Presume a condicao pelo motivo (bunker -> bunkering). Presumir e diferente
    # de adivinhar: o valor fica visivel na tela e pode ser trocado.
    condicao = condicao or dominio.CONDICAO_POR_MOTIVO.get(motivo)

    cur = conn.execute(
        "INSERT INTO escala (viagem_id, ordem, codigo_porto, tipo_escala, "
        "                    sentido, motivo, origem, criada_por, criada_em, "
        "                    observacao, condicao) "
        "VALUES (?, ?, ?, ?, ?, ?, 'extra', ?, ?, ?, ?)",
        (viagem_id, nova_ordem, codigo_porto, tipo_escala, sentido, motivo,
         por, agora(), observacao, condicao),
    )
    conn.commit()
    return cur.lastrowid, []


def remover_escala_extra(conn, escala_id: int) -> tuple[bool, list[str]]:
    """Desfaz uma parada acrescentada por engano. So a EXTRA.

    O que se desfaz e a PARADA, nao o lancamento: o comandante acrescentou
    Icoaraci no fim da lista e ela nao devia estar ali. Lancamento errado tem
    outro caminho, que e a correcao — e continua tendo.

    Escala do modelo nao sai. As seis do circuito definem a viagem: sem Juruti
    nao ha carregamento, sem Alumar nao ha o que a encerre. Apagar uma delas nao
    seria corrigir engano, seria quebrar a viagem.

    Dois desfechos, pela mesma razao de fundo — nunca destruir o que alguem
    afirmou:

    - **Parada vazia sai do banco.** Nao ha o que preservar, e a posicao de
      ordenacao fica livre para ela ser reinserida no mesmo lugar.

    - **Parada com lancamento e CANCELADA.** Some da tela do comandante e das
      filas de cobranca e de conferencia, exatamente como se tivesse sido
      apagada, mas o que foi digitado continua no banco, preso a uma escala
      marcada como cancelada. A supervisao ainda consegue ver que houve.
    """
    escala = conn.execute(
        "SELECT e.id, e.origem, e.status AS escala_status, vg.status AS viagem_status "
        "  FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
        " WHERE e.id = ?", (escala_id,)).fetchone()
    if escala is None:
        return False, ["Parada {} não existe.".format(escala_id)]
    if escala["origem"] != "extra":
        return False, ["Esta parada é do circuito padrão e não pode ser removida."]
    if escala["viagem_status"] != "aberta":
        return False, [
            "A viagem está {} — não aceita remoção.".format(escala["viagem_status"])]
    if escala["escala_status"] == "cancelada":
        return True, []  # ja saiu; repetir o pedido nao e erro

    tem_dado = bool(
        conn.execute("SELECT 1 FROM evento WHERE escala_id = ?", (escala_id,)).fetchone()
        or conn.execute("SELECT 1 FROM abastecimento WHERE escala_id = ?",
                        (escala_id,)).fetchone()
        or conn.execute("SELECT 1 FROM movimento_carga WHERE escala_id = ?",
                        (escala_id,)).fetchone()
    )
    if tem_dado:
        conn.execute("UPDATE escala SET status = 'cancelada' WHERE id = ?", (escala_id,))
    else:
        conn.execute("DELETE FROM escala WHERE id = ?", (escala_id,))
    conn.commit()
    return True, []


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
   rob_vlsfo: float | None = None,
   rob_mgo: float | None = None,
   fw: float | None = None,
   lixo: str | None = None,
   comentarios: str | None = None,
) -> tuple[int | None, list[str]]:
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
       return ja[0], []

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

   t_vlsfo = _numero_positivo(rob_vlsfo, "Combustível a bordo (VLSFO)", erros)
   t_mgo = _numero_positivo(rob_mgo, "Combustível a bordo (MGO)", erros)
   t_fw = _numero_positivo(fw, "Água Doce (FW)", erros)
   
   val_lixo = (str(lixo).strip() if lixo and str(lixo).strip() not in ("", "—") else None)
   val_comentarios = (str(comentarios).strip() if comentarios and str(comentarios).strip() else None)

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
           "                    substitui_evento_id, motivo_correcao, "
           "                    rob_vlsfo, rob_mgo, fw, lixo, comentarios) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
           (id_cliente, escala_id, tipo, hora_local, offset, hora_utc, precisao,
            registrado_por, quando, nome_responsavel.strip(), observacao,
            (vigente_atual[1] + 1) if vigente_atual else 1,
            vigente_atual[0] if vigente_atual else None,
            (motivo_correcao or "").strip() or None,
            t_vlsfo, t_mgo, t_fw, val_lixo, val_comentarios),
       )
   except sqlite3.IntegrityError as exc:
       conn.rollback()
       return None, ["Conflito ao gravar o marco: {}".format(exc)]

   if escala[0] == "abertura" and tipo == "sailing":
       conn.execute(
           "UPDATE viagem SET evento_abertura_id = ? WHERE id = ?",
           (cur.lastrowid, escala[4]),
       )

   if escala[0] == "encerramento" and tipo == "unberth" and vigente_atual is not None:
       conn.execute(
           "UPDATE viagem SET evento_encerramento_id = ? "
           " WHERE id = ? AND evento_encerramento_id = ?",
           (cur.lastrowid, escala[4], vigente_atual[0]),
       )

   conn.commit()

   if escala[0] == "encerramento" and tipo == "unberth":
       encadear_ciclo(conn, escala[4])

   return cur.lastrowid, []


def _numero_positivo(valor, rotulo: str, erros: list[str]):
    """Devolve o numero, ou None. Texto vazio nao e erro — o campo e opcional."""
    if valor is None or str(valor).strip() == "":
        return None
    try:
        numero = float(str(valor).replace(",", "."))
    except ValueError:
        erros.append("{} precisa ser um número.".format(rotulo))
        return None
    if numero < 0:
        erros.append("{} não pode ser negativo.".format(rotulo))
        return None
    return numero


def registrar_abastecimento(
    conn,
    escala_id: int,
    *,
    vlsfo,
    mgo,
    nome_responsavel: str,
    registrado_por: str,
    observacao: str | None = None,
) -> tuple[bool, list[str]]:
    """Quanto entrou de combustivel nesta escala, em toneladas.

    Sem isto o consumo nao fecha: se o navio tinha 100 t e amanhece com 600, a
    diferenca so faz sentido sabendo quanto foi abastecido no meio.

    Um registro por escala — reabastecer a mesma escala substitui o valor, que e
    o comportamento util quando o comandante corrige um numero.
    """
    erros: list[str] = []
    escala = conn.execute(
        "SELECT id, status FROM escala WHERE id = ?", (escala_id,)).fetchone()
    if escala is None:
        return False, ["Escala {} não existe.".format(escala_id)]
    if escala["status"] == "cancelada":
        return False, ["Escala cancelada não aceita abastecimento."]

    t_vlsfo = _numero_positivo(vlsfo, "VLSFO", erros)
    t_mgo = _numero_positivo(mgo, "MGO", erros)
    if not (nome_responsavel or "").strip():
        erros.append("Informe quem preencheu.")
    if t_vlsfo is None and t_mgo is None and not erros:
        erros.append("Informe a quantidade de VLSFO ou de MGO.")
    if erros:
        return False, erros

    conn.execute(
        "INSERT INTO abastecimento (escala_id, vlsfo, mgo, registrado_por, "
        "                           registrado_em, nome_responsavel, observacao) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(escala_id) DO UPDATE SET "
        "  vlsfo = excluded.vlsfo, mgo = excluded.mgo, "
        "  registrado_por = excluded.registrado_por, "
        "  registrado_em = excluded.registrado_em, "
        "  nome_responsavel = excluded.nome_responsavel, "
        "  observacao = excluded.observacao",
        (escala_id, t_vlsfo, t_mgo, registrado_por, agora(),
         nome_responsavel.strip(), observacao),
    )
    conn.commit()
    return True, []


def registrar_movimento_carga(
    conn,
    escala_id: int,
    *,
    quantidade,
    nome_responsavel: str,
    registrado_por: str,
    observacao: str | None = None,
) -> tuple[bool, list[str]]:
    """Quanto de carga entrou ou saiu nesta escala, em MT.

    A DIRECAO vem da condicao da escala, nao de um campo que o comandante
    escolhe: `loading` carrega, `discharging` descarrega. Deixar ele escolher
    abriria a porta para um carregamento lancado como descarga — e o saldo a
    bordo derreteria sem ninguem entender por que.

    Um movimento por escala; relancar substitui, que e o util quando ele corrige.
    """
    erros: list[str] = []
    escala = conn.execute(
        "SELECT id, status, condicao FROM escala WHERE id = ?", (escala_id,)).fetchone()
    if escala is None:
        return False, ["Escala {} não existe.".format(escala_id)]
    if escala["status"] == "cancelada":
        return False, ["Escala cancelada não aceita movimento de carga."]
    if escala["condicao"] not in ("loading", "discharging"):
        return False, ["Esta escala não movimenta carga."]

    valor = _numero_positivo(quantidade, "Quantidade", erros)
    if not (nome_responsavel or "").strip():
        erros.append("Informe quem preencheu.")
    if valor is None and not erros:
        erros.append("Informe a quantidade em MT.")
    if erros:
        return False, erros

    carregado = valor if escala["condicao"] == "loading" else None
    descarregado = valor if escala["condicao"] == "discharging" else None

    conn.execute(
        "INSERT INTO movimento_carga (escala_id, carregado, descarregado, "
        "                             registrado_por, registrado_em, "
        "                             nome_responsavel, observacao) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(escala_id) DO UPDATE SET "
        "  carregado = excluded.carregado, descarregado = excluded.descarregado, "
        "  registrado_por = excluded.registrado_por, "
        "  registrado_em = excluded.registrado_em, "
        "  nome_responsavel = excluded.nome_responsavel, "
        "  observacao = excluded.observacao",
        (escala_id, carregado, descarregado, registrado_por, agora(),
         nome_responsavel.strip(), observacao),
    )
    conn.commit()
    return True, []


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
        " WHERE e.viagem_id = ? AND e.tipo_escala = 'encerramento' "
        "   AND ev.tipo = 'unberth' AND ev.vigente = 1 "
        " ORDER BY e.ordem DESC LIMIT 1",
        (viagem_id,),
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
