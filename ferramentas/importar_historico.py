# -*- coding: utf-8 -*-
"""Importa o historico das viagens (planilha das supervisoras) para o Corsair.

    python -X utf8 -m ferramentas.importar_historico relatorio
    python -X utf8 -m ferramentas.importar_historico importar --apagar-existente
    python -X utf8 -m ferramentas.importar_historico importar --apagar-existente --env .env

`relatorio` so le e valida: imprime o inventario e todos os problemas, sem tocar
o banco. `importar` grava — e se recusa enquanto houver problema de nivel
"erro". O banco e o que `app.db.conectar()` decide: SQLite local por padrao,
Turso quando TURSO_DATABASE_URL e TURSO_AUTH_TOKEN estiverem no ambiente.

Como o que existe no sistema e teste (decisao 4), `--apagar-existente` apaga
TODAS as viagens, escalas, marcos, conferencias, abastecimentos e cargas antes
de gravar. Sem a opcao, a importacao recusa um banco que ja tenha viagem.

O que a leitura faz com a planilha — e as seis decisoes que ela aplica — esta
documentado em ferramentas/historico_planilha.py.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def carregar_env(caminho: str) -> None:
    """Poe no ambiente as variaveis de um .env ("CHAVE=valor" ou "CHAVE: valor").

    Tem de rodar ANTES de importar app.config, que le o ambiente no import. So o
    importador faz isto — o app em si nunca le .env, e continua nao lendo.
    """
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        sep = "=" if "=" in linha and (":" not in linha or linha.index("=") < linha.index(":")) else ":"
        chave, _, valor = linha.partition(sep)
        os.environ[chave.strip()] = valor.strip().strip('"').strip("'")


if "--env" in sys.argv:
    carregar_env(sys.argv[sys.argv.index("--env") + 1])
os.environ.setdefault("SHIPOPS_SECRET_KEY", "sem-uso-no-importador")

from app import db, dominio, viagens as servico  # noqa: E402
from ferramentas import historico_planilha as hp  # noqa: E402

PLANILHA = Path(__file__).resolve().parent.parent / "Analise Viagens - Histórico.xlsx"
CONTA = "importacao"
RESPONSAVEL = "Histórico (planilha das supervisoras)"
COMENTARIO_CONFERENCIA = "Importação do histórico da planilha das supervisoras, {}".format(
    hp.DATA_DECISOES)


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------

def _fmt(quando) -> str:
    return quando.strftime("%d/%m/%Y") if quando else "—"


def imprimir_relatorio(viagens_por_navio, problemas) -> int:
    print("INVENTÁRIO")
    print("  {:<6} {:>8} {:<22} {:<23} {:>7} {:>8} {:>7} {:>6} {:>6} {:>6}".format(
        "navio", "viagens", "códigos", "período", "marcos", "unberth*", "extras", "bunker", "carga", "canc."))
    for r in hp.resumo(viagens_por_navio):
        print("  {:<6} {:>8} {:<22} {:<23} {:>7} {:>8} {:>7} {:>6} {:>6} {:>6}".format(
            r["prefixo"], r["viagens"], "{} – {}".format(r["primeira"], r["ultima"]),
            "{} → {}".format(_fmt(r["de"]), _fmt(r["ate"])), r["marcos"], r["unberth_copiados"],
            r["extras"], r["abastecimentos"], r["cargas"], r["canceladas"]))
    print("  * Unberth copiado do Sailing (decisão 1, opção B). canc. = escalas canceladas por falta de registro.")
    print()

    for nivel, titulo in (("erro", "ERROS — impedem a importação"),
                          ("aviso", "AVISOS — importa, mas confira"),
                          ("nota", "NOTAS — só para registro")):
        lista = [p for p in problemas if p.nivel == nivel]
        print("{} ({})".format(titulo, len(lista)))
        for p in lista:
            print("  " + str(p))
        print()

    for navio_id, viagens in sorted(viagens_por_navio.items()):
        aberta = next((v for v in viagens if v.aberta), None)
        if aberta:
            ultimo = max((m for _e, _t, m in aberta.todos_os_marcos()), key=lambda m: m.quando, default=None)
            print("Viagem em curso do {}: {} (último marco {})".format(
                hp.PREFIXOS[navio_id], aberta.codigo,
                ultimo.quando.strftime("%d/%m/%Y %H:%M") if ultimo else "nenhum"))
    return sum(1 for p in problemas if p.nivel == "erro")


# ---------------------------------------------------------------------------
# Gravacao
# ---------------------------------------------------------------------------

def _garantir_conta(conn) -> None:
    """A conta que assina as conferencias. Inativa: ninguem entra com ela."""
    conn.execute(
        "INSERT OR IGNORE INTO conta (login, nome_exibicao, perfil, senha_hash, navio_id, "
        "                             ativo, criada_em) VALUES (?, ?, 'analytics', 'bloqueada', NULL, 0, ?)",
        (CONTA, "Importação do histórico", db.agora()))


def apagar_tudo(conn) -> dict:
    contagens = {t: conn.execute("SELECT COUNT(*) FROM {}".format(t)).fetchone()[0]
                 for t in ("viagem", "escala", "evento", "conferencia", "abastecimento", "movimento_carga")}
    conn.execute("UPDATE viagem SET evento_abertura_id = NULL, evento_encerramento_id = NULL")
    for tabela in ("conferencia", "evento", "abastecimento", "movimento_carga", "escala", "viagem"):
        conn.execute("DELETE FROM {}".format(tabela))
    conn.commit()
    return contagens


def _inserir_evento(conn, escala_id: int, tipo: str, marco: hp.Marco, aba: str, codigo: str) -> int:
    hora_utc = dominio.para_utc(marco.iso, hp.OFFSET)
    id_cliente = "hist:{}:{}:L{}:{}".format(aba.replace("AMAZON ", ""), codigo, marco.linha, tipo)
    cur = conn.execute(
        "INSERT INTO evento (id_cliente, escala_id, tipo, hora_local, offset_utc, hora_utc, "
        "                    precisao, registrado_por, registrado_em, nome_responsavel, "
        "                    observacao, versao, vigente) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
        (id_cliente, escala_id, tipo, marco.iso, hp.OFFSET, hora_utc, marco.precisao,
         CONTA, db.agora(), RESPONSAVEL, marco.nota))
    evento_id = cur.lastrowid
    conn.execute(
        "INSERT INTO conferencia (evento_id, conferido_por, conferido_em, resultado, comentario) "
        "VALUES (?, ?, ?, 'conferido', ?)",
        (evento_id, CONTA, db.agora(), COMENTARIO_CONFERENCIA))
    return evento_id


def _observacao(escala: hp.Escala) -> str | None:
    partes = [escala.cancelar] if escala.cancelar else []
    partes += ["[planilha] " + n for n in escala.notas]
    return "\n".join(partes) or None


def _gravar_escala(conn, escala_id: int, escala: hp.Escala, aba: str, codigo: str, viagem_id: int) -> dict:
    """Marcos, notas, abastecimento e carga de uma escala. Devolve tipo -> evento_id."""
    eventos = {}
    if escala.cancelar:
        conn.execute("UPDATE escala SET status = 'cancelada', observacao = ? WHERE id = ?",
                     (_observacao(escala), escala_id))
        return eventos
    for tipo in ("arrival", "berth", "unberth", "sailing"):
        if tipo in escala.marcos:
            eventos[tipo] = _inserir_evento(conn, escala_id, tipo, escala.marcos[tipo], aba, codigo)
    obs = _observacao(escala)
    if obs:
        conn.execute("UPDATE escala SET observacao = ? WHERE id = ?", (obs, escala_id))
    if escala.abastecimento:
        vlsfo, mgo, texto = escala.abastecimento
        _, erros = servico.registrar_abastecimento(
            conn, escala_id, vlsfo=vlsfo, mgo=mgo, nome_responsavel=RESPONSAVEL,
            registrado_por=CONTA, observacao="[planilha] " + texto)
        if erros:
            raise RuntimeError("{} {}: abastecimento recusado: {}".format(codigo, escala.rotulo, erros))
    if escala.tonelagem is not None:
        _, erros = servico.registrar_movimento_carga(
            conn, escala_id, quantidade=escala.tonelagem, nome_responsavel=RESPONSAVEL,
            registrado_por=CONTA, observacao="[planilha] tonelagem do bloco")
        if erros:
            raise RuntimeError("{} {}: carga recusada: {}".format(codigo, escala.rotulo, erros))
    return eventos


def gravar_viagem(conn, v: hp.Viagem) -> int:
    viagem_id, erros = servico.abrir_viagem(conn, v.navio_id, numero=v.codigo, por=CONTA)
    if erros:
        raise RuntimeError("{}: {}".format(v.codigo, erros))
    conn.execute("UPDATE viagem SET observacao = ? WHERE id = ?",
                 ("Importada do histórico ({}), planilha linha {}".format(hp.DATA_DECISOES, v.linha),
                  viagem_id))

    ids = {r["ordem"]: r["id"] for r in conn.execute(
        "SELECT id, ordem FROM escala WHERE viagem_id = ?", (viagem_id,))}
    for chave, ordem in hp.ORDEM_MODELO.items():
        eventos = _gravar_escala(conn, ids[ordem], v.escalas[chave], v.aba, v.codigo, viagem_id)
        if chave == "abertura" and "sailing" in eventos:
            conn.execute("UPDATE viagem SET evento_abertura_id = ? WHERE id = ?",
                         (eventos["sailing"], viagem_id))

    for extra in v.extras:
        escala_id, erros = servico.adicionar_escala_extra(
            conn, viagem_id, codigo_porto=extra.porto, tipo_escala="fundeio",
            motivo=extra.motivo, apos_ordem=hp.ORDEM_MODELO["abertura"], sentido="subida",
            condicao="bunkering" if extra.motivo == "bunker" else "ballast", por=CONTA,
            observacao=("Porto presumido pelas horas desde a saída de Alumar\n" if extra.porto_presumido else ""))
        if erros:
            raise RuntimeError("{}: parada adicional: {}".format(v.codigo, erros))
        _gravar_escala(conn, escala_id, extra, v.aba, v.codigo, viagem_id)

    if not v.aberta:
        fechou, avisos = servico.encerrar_viagem(conn, viagem_id)
        if not fechou:
            raise RuntimeError("{}: não fechou: {}".format(v.codigo, avisos))
    conn.commit()
    return viagem_id


def importar(conn, viagens_por_navio, *, apagar: bool) -> dict:
    existentes = conn.execute("SELECT COUNT(*) FROM viagem").fetchone()[0]
    apagado = {}
    if existentes:
        if not apagar:
            raise RuntimeError(
                "O banco já tem {} viagem(ns). Use --apagar-existente para substituí-las "
                "(o que existe hoje é teste — decisão 4).".format(existentes))
        apagado = apagar_tudo(conn)
    _garantir_conta(conn)
    conn.commit()

    gravadas = 0
    for navio_id, viagens in sorted(viagens_por_navio.items()):
        for v in viagens:
            gravar_viagem(conn, v)
            gravadas += 1
    return {"apagado": apagado, "viagens": gravadas}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acao", choices=("relatorio", "importar"))
    parser.add_argument("--planilha", default=str(PLANILHA))
    parser.add_argument("--apagar-existente", action="store_true")
    parser.add_argument("--mesmo-com-erros", action="store_true",
                        help="grava mesmo com erros de validação (só para ensaio local)")
    parser.add_argument("--env", help="arquivo .env com TURSO_DATABASE_URL e TURSO_AUTH_TOKEN")
    args = parser.parse_args(argv)

    viagens_por_navio, problemas = hp.ler(args.planilha)
    erros = imprimir_relatorio(viagens_por_navio, problemas)
    if args.acao == "relatorio":
        return 1 if erros else 0
    if erros and not args.mesmo_com_erros:
        print("\nHá {} erro(s). Nada foi gravado.".format(erros))
        return 1

    from app import config
    destino = "Turso ({})".format(config.TURSO_URL.split("//")[-1]) if config.usando_turso() else \
        "SQLite local ({})".format(config.CAMINHO_BANCO)
    print("\nGravando em {} ...".format(destino))
    with closing(db.conectar()) as conn:
        db.inicializar(conn)
        resultado = importar(conn, viagens_por_navio, apagar=args.apagar_existente)
        print("Conferência no destino: {} viagens, {} marcos, {} na fila de conferência, abertas: {}".format(
            conn.execute("SELECT COUNT(*) FROM viagem").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM evento").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM escalas_a_conferir").fetchone()[0],
            ", ".join(r[0] for r in conn.execute(
                "SELECT numero FROM viagem WHERE status = 'aberta' ORDER BY navio_id"))))
    if resultado["apagado"]:
        print("Apagado antes: " + ", ".join("{} {}".format(v, k) for k, v in resultado["apagado"].items()))
    print("Gravadas {} viagens.".format(resultado["viagens"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
