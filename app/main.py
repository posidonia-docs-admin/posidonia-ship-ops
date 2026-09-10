"""Aplicacao web. FastAPI + Jinja2, sem build step, sem node, sem CDN."""
from __future__ import annotations

from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, contas, db, dominio, viagens

RAIZ = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(RAIZ / "templates"))
templates.env.globals["ROTULO_MOTIVO"] = dominio.ROTULO_MOTIVO
templates.env.globals["ROTULO_MARCO"] = dominio.ROTULO_MARCO

@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    # Falha aqui e falha cedo: melhor nao subir do que subir sem segredo.
    config.secret_key()
    db.inicializar()
    yield


app = FastAPI(title="Posidonia Ship Ops", lifespan=ciclo_de_vida,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

PUBLICOS = ("/login", "/logout", "/api/health", "/static", "/favicon")
METODOS_LEITURA = ("GET", "HEAD", "OPTIONS")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)




# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------

@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, resposta_seguinte):
    resposta = await resposta_seguinte(request)
    resposta.headers["Content-Security-Policy"] = CSP
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "same-origin"
    resposta.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    if request.url.scheme == "https":
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
    if "text/html" in resposta.headers.get("content-type", ""):
        resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.middleware("http")
async def exigir_login(request: Request, resposta_seguinte):
    """Guarda global: rota nova nasce protegida, sem precisar lembrar de nada."""
    caminho = request.url.path
    if caminho.startswith(PUBLICOS):
        return await resposta_seguinte(request)

    login = auth.verificar_sessao(request.cookies.get(auth.COOKIE_SESSAO, ""))
    conta = None
    if login:
        with closing(db.conectar()) as conn:
            conta = contas.buscar(conn, login)

    if conta is None:
        destino = caminho + (("?" + request.url.query) if request.url.query else "")
        resposta = RedirectResponse("/login?next=" + quote(destino, safe=""), 303)
        resposta.delete_cookie(auth.COOKIE_SESSAO, path="/")
        return resposta

    if conta["perfil"] in contas.PERFIS_SOMENTE_LEITURA and request.method not in METODOS_LEITURA:
        return HTMLResponse("<h3>Acesso somente leitura.</h3>", status_code=403)

    request.state.conta = conta
    return await resposta_seguinte(request)


# ---------------------------------------------------------------------------
# Saude e sessao
# ---------------------------------------------------------------------------

@app.get("/api/health")
def saude():
    """Publico e sem versao: o Render depende dele e ele nao anuncia nada."""
    try:
        with closing(db.conectar()) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "erro"}, status_code=503)


def _destino_seguro(bruto: str | None) -> str:
    """Bloqueia open-redirect: so caminho interno."""
    if bruto and bruto.startswith("/") and not bruto.startswith("//"):
        return bruto
    return "/"


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request, next: str = "/", erro: str = ""):
    return templates.TemplateResponse(
        request, "login.html", {"next": _destino_seguro(next), "erro": erro})


@app.post("/login")
def entrar(request: Request, login: str = Form(""), senha: str = Form(""),
           next: str = Form("/")):
    destino = _destino_seguro(next)
    with closing(db.conectar()) as conn:
        conta = contas.autenticar(conn, login, senha)
        if conta is None:
            contas.registrar_acesso(conn, login, "login_negado", request.client.host
                                    if request.client else None)
            return RedirectResponse(
                "/login?next={}&erro={}".format(
                    quote(destino, safe=""), quote("Login ou senha inválidos.")), 303)
        contas.registrar_acesso(conn, conta["login"], "login",
                                request.client.host if request.client else None)

    resposta = RedirectResponse(destino, 303)
    resposta.set_cookie(
        auth.COOKIE_SESSAO, auth.assinar_sessao(conta["login"]),
        max_age=auth.DURACAO_SESSAO, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/")
    return resposta


@app.get("/logout")
def sair():
    resposta = RedirectResponse("/login", 303)
    resposta.delete_cookie(auth.COOKIE_SESSAO, path="/")
    return resposta


@app.get("/")
def raiz(request: Request):
    perfil = request.state.conta["perfil"]
    return RedirectResponse("/navio" if perfil == "navio" else "/painel", 303)


# ---------------------------------------------------------------------------
# Telas do comandante
# ---------------------------------------------------------------------------

def _viagem_do_navio(conn, navio_id: int):
    return conn.execute(
        "SELECT id, numero, status FROM viagem "
        " WHERE navio_id = ? AND status = 'aberta'", (navio_id,)).fetchone()


def _escalas_com_marcos(conn, viagem_id: int) -> list[dict]:
    """Cada escala com a lista de marcos que ELA pede, na ordem cronologica."""
    escalas = conn.execute(
        "SELECT e.id, e.ordem, e.codigo_porto, e.tipo_escala, e.sentido, e.motivo, "
        "       e.origem, p.nome AS porto_nome, p.offset_padrao "
        "  FROM escala e JOIN porto p ON p.codigo = e.codigo_porto "
        " WHERE e.viagem_id = ? AND e.status <> 'cancelada' ORDER BY e.ordem",
        (viagem_id,)).fetchall()

    saida = []
    for escala in escalas:
        exigidos = [r[0] for r in conn.execute(
            "SELECT tipo_evento FROM marco_exigido WHERE tipo_escala = ? ORDER BY ordem",
            (escala["tipo_escala"],))]
        lancados = {r["tipo"]: r for r in conn.execute(
            "SELECT tipo, hora_local, offset_utc, nome_responsavel, versao, observacao "
            "  FROM evento_vigente WHERE escala_id = ?", (escala["id"],))}
        saida.append({
            "escala": escala,
            "marcos": [{"tipo": t, "rotulo": dominio.ROTULO_MARCO[t],
                        "lancado": lancados.get(t)} for t in exigidos],
            "completa": all(t in lancados for t in exigidos),
        })
    return saida


@app.get("/navio", response_class=HTMLResponse)
def navio_inicio(request: Request):
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return RedirectResponse("/painel", 303)

    with closing(db.conectar()) as conn:
        viagem = _viagem_do_navio(conn, conta["navio_id"])
        if viagem is None:
            viagem_id, erros = viagens.abrir_viagem(
                conn, conta["navio_id"], por=conta["login"])
            viagem = _viagem_do_navio(conn, conta["navio_id"])
        blocos = _escalas_com_marcos(conn, viagem["id"]) if viagem else []
        portos = conn.execute(
            "SELECT codigo, nome FROM porto WHERE ativo = 1 ORDER BY nome").fetchall()

    return templates.TemplateResponse(request, "navio_inicio.html", {
        "conta": conta, "viagem": viagem,
        "blocos": blocos, "portos": portos,
    })


@app.get("/navio/marco/{escala_id}/{tipo}", response_class=HTMLResponse)
def navio_form_marco(request: Request, escala_id: int, tipo: str):
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        escala = conn.execute(
            "SELECT e.id, e.tipo_escala, e.codigo_porto, e.sentido, vg.navio_id, "
            "       vg.numero, p.nome AS porto_nome, p.offset_padrao "
            "  FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            "  JOIN porto p ON p.codigo = e.codigo_porto WHERE e.id = ?",
            (escala_id,)).fetchone()
        if escala is None or (conta["perfil"] == "navio"
                              and escala["navio_id"] != conta["navio_id"]):
            return HTMLResponse("<h3>Escala não encontrada.</h3>", status_code=404)
        atual = conn.execute(
            "SELECT hora_local, offset_utc, nome_responsavel, observacao, versao "
            "  FROM evento_vigente WHERE escala_id = ? AND tipo = ?",
            (escala_id, tipo)).fetchone()

    if tipo not in dominio.MARCOS_POR_TIPO.get(escala["tipo_escala"], ()):
        return HTMLResponse(
            "<h3>Esta escala não tem {}.</h3>".format(
                dominio.ROTULO_MARCO.get(tipo, tipo)), status_code=400)

    return templates.TemplateResponse(request, "navio_marco.html", {
        "conta": conta, "escala": escala, "tipo": tipo,
        "rotulo": dominio.ROTULO_MARCO[tipo], "atual": atual,
        "offset_padrao": escala["offset_padrao"],
    })


@app.post("/api/marco")
async def api_marco(request: Request):
    """Recebe o lancamento da fila local. Sempre JSON, nunca redirect.

    A fila do celular repete o envio quando o servidor demora a acordar; e o
    `id_cliente` que torna isso inofensivo.
    """
    corpo = await request.json()
    conta = request.state.conta

    with closing(db.conectar()) as conn:
        escala = conn.execute(
            "SELECT e.id, vg.navio_id FROM escala e "
            "  JOIN viagem vg ON vg.id = e.viagem_id WHERE e.id = ?",
            (corpo.get("escala_id"),)).fetchone()
        if escala is None:
            return JSONResponse({"ok": False, "erros": ["Escala não encontrada."]}, 404)
        if conta["perfil"] == "navio" and escala["navio_id"] != conta["navio_id"]:
            return JSONResponse({"ok": False, "erros": ["Escala de outro navio."]}, 403)

        evento_id, erros = viagens.lancar_marco(
            conn, escala["id"],
            tipo=corpo.get("tipo"),
            hora_local=corpo.get("hora_local"),
            offset=corpo.get("offset"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            id_cliente=corpo.get("id_cliente"),
            observacao=(corpo.get("observacao") or "").strip() or None,
            motivo_correcao=(corpo.get("motivo_correcao") or "").strip() or None,
        )

    if erros:
        # 422: o dado esta errado e reenviar nao vai adiantar. A fila precisa
        # distinguir isto de "servidor fora do ar", ou fica repetindo para sempre.
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True, "evento_id": evento_id}


@app.post("/navio/escala-extra")
def navio_escala_extra(
    request: Request,
    codigo_porto: str = Form(...),
    motivo: str = Form(...),
    apos_ordem: int = Form(...),
    tipo_escala: str = Form("fundeio"),
):
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        viagem = _viagem_do_navio(conn, conta["navio_id"])
        if viagem is None:
            return RedirectResponse("/navio", 303)
        _, erros = viagens.adicionar_escala_extra(
            conn, viagem["id"], codigo_porto=codigo_porto, tipo_escala=tipo_escala,
            motivo=motivo, apos_ordem=apos_ordem, por=conta["login"])
    destino = "/navio"
    if erros:
        destino += "?erro=" + quote(" | ".join(erros))
    return RedirectResponse(destino, 303)


# ---------------------------------------------------------------------------
# Painel da equipe em terra (esqueleto — Fase 3)
# ---------------------------------------------------------------------------

@app.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    with closing(db.conectar()) as conn:
        frota = conn.execute(
            "SELECT n.nome_oficial, vg.numero, vg.status, "
            "       (SELECT COUNT(*) FROM escalas_incompletas i WHERE i.viagem_id = vg.id) "
            "         AS faltantes "
            "  FROM navio n LEFT JOIN viagem vg "
            "    ON vg.navio_id = n.id AND vg.status = 'aberta' "
            " WHERE n.ativo = 1 ORDER BY n.id").fetchall()
        a_conferir = conn.execute(
            "SELECT COUNT(*) FROM escalas_a_conferir").fetchone()[0]
    return templates.TemplateResponse(request, "painel.html", {
        "conta": request.state.conta,
        "frota": frota, "a_conferir": a_conferir})
