"""Camada web: guarda de login, isolamento entre navios e a API da fila local."""

import os
import pathlib
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import contas, db, main

SENHA = "senha-de-teste-123"


@pytest.fixture()
def cliente():
    """Banco em arquivo, limpo a cada teste, com as contas do piloto criadas."""
    caminho = pathlib.Path(os.environ["SHIPOPS_BANCO"])
    if caminho.exists():
        caminho.unlink()
    db.inicializar()
    with closing(db.conectar()) as conn:
        contas.criar_conta(conn, login="navio.pathfinder", senha=SENHA,
                           nome_exibicao="Amazon Pathfinder", perfil="navio", navio_id=1)
        contas.criar_conta(conn, login="navio.pioneer", senha=SENHA,
                           nome_exibicao="Amazon Pioneer", perfil="navio", navio_id=2)
        contas.criar_conta(conn, login="vlo", senha=SENHA,
                           nome_exibicao="Vinicius", perfil="supervisor")
        contas.criar_conta(conn, login="bi", senha=SENHA,
                           nome_exibicao="Analytics", perfil="analytics")
    with TestClient(main.app) as c:
        yield c


def entrar(cliente, login="navio.pathfinder"):
    resposta = cliente.post("/login", data={"login": login, "senha": SENHA, "next": "/"},
                            follow_redirects=False)
    assert resposta.status_code == 303
    return resposta


def escala_de(cliente, ordem=20):
    with closing(db.conectar()) as conn:
        return conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.status = 'aberta' AND e.ordem = ? ORDER BY vg.navio_id LIMIT 1",
            (ordem,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Guarda de login
# ---------------------------------------------------------------------------

def test_health_e_publico_e_nao_anuncia_versao(cliente):
    resposta = cliente.get("/api/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_rota_protegida_manda_para_o_login_preservando_o_destino(cliente):
    resposta = cliente.get("/navio", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/login?next=")
    assert "%2Fnavio" in resposta.headers["location"]


def test_senha_errada_nao_entra(cliente):
    resposta = cliente.post("/login", data={"login": "navio.pathfinder", "senha": "x"},
                            follow_redirects=False)
    assert resposta.status_code == 303
    assert "erro=" in resposta.headers["location"]
    assert cliente.cookies.get("shipops_sessao") is None


def test_login_valido_entra_e_abre_a_viagem_sozinho(cliente):
    entrar(cliente)
    resposta = cliente.get("/navio")
    assert resposta.status_code == 200
    assert "AMAZON PATHFINDER" in resposta.text
    # a home abre a viagem se nao houver nenhuma: o comandante nunca fica sem onde lancar
    assert "Juruti" in resposta.text and "Barra Norte" in resposta.text


def test_cookie_adulterado_e_recusado(cliente):
    entrar(cliente)
    cliente.cookies.set("shipops_sessao", "bWVudGlyYQ.YXNzaW5hdHVyYQ")
    resposta = cliente.get("/navio", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/login")


def test_next_nao_permite_redirect_para_fora(cliente):
    resposta = cliente.post(
        "/login",
        data={"login": "navio.pathfinder", "senha": SENHA, "next": "//evil.example"},
        follow_redirects=False)
    assert resposta.headers["location"] == "/"


def test_cabecalhos_de_seguranca(cliente):
    resposta = cliente.get("/login")
    assert "default-src 'self'" in resposta.headers["content-security-policy"]
    assert resposta.headers["x-frame-options"] == "DENY"
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert resposta.headers["cache-control"] == "no-store"


def test_swagger_desligado(cliente):
    for rota in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(rota, follow_redirects=False).status_code in (303, 404)


# ---------------------------------------------------------------------------
# Perfis
# ---------------------------------------------------------------------------

def test_analytics_nao_escreve(cliente):
    entrar(cliente, "bi")
    resposta = cliente.post("/api/marco", json={})
    assert resposta.status_code == 403
    assert "somente leitura" in resposta.text


def test_supervisor_cai_no_painel(cliente):
    entrar(cliente, "vlo")
    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.headers["location"] == "/painel"
    assert "Frota" in cliente.get("/painel").text


# ---------------------------------------------------------------------------
# API da fila local
# ---------------------------------------------------------------------------

def test_lancamento_grava_e_aparece_na_tela(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=20)

    resposta = cliente.post("/api/marco", json={
        "escala_id": escala_id, "tipo": "arrival",
        "hora_local": "2026-03-01T07:00", "offset": "-03:00",
        "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-1",
    })
    assert resposta.status_code == 200 and resposta.json()["ok"] is True
    assert "07:00" in cliente.get("/navio").text
    assert "Cmt. Almeida" in cliente.get("/navio").text


def test_reenvio_do_mesmo_id_nao_duplica(cliente):
    """A fila do celular repete o envio quando o servidor demora a acordar."""
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=20)
    corpo = {"escala_id": escala_id, "tipo": "arrival",
             "hora_local": "2026-03-01T07:00", "offset": "-03:00",
             "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-repetido"}

    primeiro = cliente.post("/api/marco", json=corpo).json()
    segundo = cliente.post("/api/marco", json=corpo).json()
    assert primeiro["evento_id"] == segundo["evento_id"]

    with closing(db.conectar()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evento WHERE escala_id = ?", (escala_id,)
        ).fetchone()[0] == 1


def test_dado_invalido_devolve_422_para_a_fila_parar_de_tentar(cliente):
    """422 e o sinal de 'nao adianta reenviar'. Sem ele a fila repete para sempre."""
    entrar(cliente)
    cliente.get("/navio")
    barra = escala_de(cliente, ordem=40)      # escala de passagem: nao tem berth

    resposta = cliente.post("/api/marco", json={
        "escala_id": barra, "tipo": "berth",
        "hora_local": "2026-03-01T07:00", "offset": "-03:00",
        "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-2"})
    assert resposta.status_code == 422
    assert any("não atraca aqui" in e for e in resposta.json()["erros"])


def test_navio_nao_lanca_em_escala_de_outro_navio(cliente):
    entrar(cliente, "navio.pioneer")
    cliente.get("/navio")                      # abre a viagem do Pioneer
    cliente.get("/logout")
    entrar(cliente, "navio.pathfinder")
    cliente.get("/navio")

    with closing(db.conectar()) as conn:
        alheia = conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.navio_id = 2 LIMIT 1").fetchone()[0]

    resposta = cliente.post("/api/marco", json={
        "escala_id": alheia, "tipo": "arrival", "hora_local": "2026-03-01T07:00",
        "offset": "-03:00", "nome_responsavel": "X", "id_cliente": "cli-3"})
    assert resposta.status_code == 403


def test_correcao_pela_api_exige_motivo(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=20)
    base = {"escala_id": escala_id, "tipo": "arrival", "offset": "-03:00",
            "nome_responsavel": "Cmt. Almeida"}

    cliente.post("/api/marco", json=dict(base, hora_local="2026-03-01T07:00",
                                         id_cliente="c1"))
    sem_motivo = cliente.post("/api/marco", json=dict(base, hora_local="2026-03-01T07:45",
                                                      id_cliente="c2"))
    assert sem_motivo.status_code == 422

    com_motivo = cliente.post("/api/marco", json=dict(
        base, hora_local="2026-03-01T07:45", id_cliente="c3",
        motivo_correcao="conferido no diario"))
    assert com_motivo.status_code == 200
    assert "corrigido" in cliente.get("/navio").text


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------

def test_form_recusa_marco_que_a_escala_nao_tem(cliente):
    entrar(cliente)
    cliente.get("/navio")
    barra = escala_de(cliente, ordem=40)
    assert cliente.get("/navio/marco/{}/berth".format(barra)).status_code == 400
    assert cliente.get("/navio/marco/{}/arrival".format(barra)).status_code == 200


def test_escala_extra_pela_tela(cliente):
    entrar(cliente)
    cliente.get("/navio")
    resposta = cliente.post("/navio/escala-extra", data={
        "codigo_porto": "ICOARACI", "motivo": "bunker",
        "apos_ordem": 10, "tipo_escala": "fundeio"}, follow_redirects=False)
    assert resposta.status_code == 303
    pagina = cliente.get("/navio").text
    assert "Icoaraci" in pagina and "fora do padrão" in pagina


def test_estatico_e_publico(cliente):
    for arquivo in ("/static/estilo.css", "/static/fila.js", "/static/marco.js"):
        assert cliente.get(arquivo).status_code == 200
