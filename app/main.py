"""Aplicacao web. FastAPI + Jinja2, sem build step, sem node, sem CDN."""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, contas, db, dominio, viagens

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(RAIZ / "templates"))
templates.env.globals["ROTULO_MOTIVO"] = dominio.ROTULO_MOTIVO
templates.env.globals["ROTULO_MARCO"] = dominio.ROTULO_MARCO


def versao_estaticos() -> str:
    """Impressao digital do CSS e do JS, para o endereco deles mudar a cada deploy.

    O navegador guarda `/static/estilo.css` e continua servindo a copia velha
    depois de um deploy — o Vinicius viu a tela ANTERIOR ao redesenho por causa
    disso. A bordo e pior: ninguem vai ensinar comandante a limpar cache.

    Com `?v=<digest>` no fim, arquivo novo e ENDERECO novo: o navegador nao tem
    o que reaproveitar. Calculado uma vez, no import — dentro do container os
    arquivos nao mudam enquanto o processo vive.
    """
    digest = hashlib.blake2b(digest_size=8)
    for arquivo in sorted(ESTATICOS.glob("*")):
        if arquivo.is_file():
            digest.update(arquivo.name.encode("utf-8"))
            digest.update(arquivo.read_bytes())
    return digest.hexdigest()


templates.env.globals["V"] = versao_estaticos()

# Menu por perfil. Declarativo, como o NAV_TREE do Sistema Emissor: menu novo
# nasce de dado, nao de HTML espalhado. O do comandante e curto de proposito —
# o acesso dele e so lancar escala.
MENU = {
    "navio": ((("/navio"), "Viagens"),),
    "supervisor": ((("/painel"), "Frota"),),
    "analytics": ((("/painel"), "Frota"),),
    "admin": ((("/painel"), "Frota"), (("/admin/contas"), "Contas")),
}
templates.env.globals["MENU"] = MENU
templates.env.globals["ROTULO_CONDICAO"] = dominio.ROTULO_CONDICAO


def formato_br(iso):
    """'2026-08-21T04:20' -> '21/08/2026 04:20'.

    So EXIBICAO. O banco continua em ISO 8601, que e o que ordena certo e o que
    as contas de duracao usam — trocar o armazenamento por causa da leitura
    quebraria as duas coisas.
    """
    if not iso:
        return ""
    partes = str(iso)[:10].split("-")
    if len(partes) != 3:
        return str(iso)
    data = "{}/{}/{}".format(partes[2], partes[1], partes[0])
    hora = str(iso)[11:16]
    return (data + " " + hora).strip()


def formato_mt(valor, casas=3):
    """1234.5 -> '1.234,500'. Tres casas, no padrao brasileiro."""
    if valor is None or valor == "":
        return "\u2014"
    try:
        texto = "{:,.{}f}".format(float(valor), casas)
    except (TypeError, ValueError):
        return str(valor)
    # de 1,234.500 para 1.234,500
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


templates.env.filters["br"] = formato_br
templates.env.filters["mt"] = formato_mt


def _garantir_admin() -> None:
    """Cria (ou realinha) a conta de administrador a partir do ambiente.

    Em producao no plano free do Render NAO existe terminal: sem isto o sistema
    sobe e ninguem consegue entrar. A regra e simples e previsivel — a senha do
    admin e o que SHIPOPS_ADMIN_SENHA disser. E tambem o caminho de recuperacao
    se a senha se perder: troca-se a variavel e redeploya.
    """
    if not config.ADMIN_LOGIN or not config.ADMIN_SENHA:
        return
    with closing(db.conectar()) as conn:
        if contas.buscar(conn, config.ADMIN_LOGIN) is None:
            contas.criar_conta(
                conn, login=config.ADMIN_LOGIN, senha=config.ADMIN_SENHA,
                nome_exibicao="Administrador", perfil="admin")
        elif contas.autenticar(conn, config.ADMIN_LOGIN, config.ADMIN_SENHA) is None:
            contas.trocar_senha(conn, config.ADMIN_LOGIN, config.ADMIN_SENHA)


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    # Falha aqui e falha cedo: melhor nao subir do que subir sem segredo.
    config.secret_key()
    config.checar_persistencia()
    db.inicializar()
    _garantir_admin()
    with closing(db.conectar()) as conn:
        viagens.normalizar_viagens_vazias(conn)
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
    elif request.url.path.startswith("/static"):
        # O endereco carrega a impressao digital do arquivo (ver versao_estaticos):
        # conteudo novo e endereco novo. Entao o velho pode ser guardado para
        # sempre — e a bordo isso vira economia de banda, nao risco de tela velha.
        resposta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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
        "       e.origem, e.condicao, p.nome AS porto_nome, p.offset_padrao "
        "  FROM escala e JOIN porto p ON p.codigo = e.codigo_porto "
        " WHERE e.viagem_id = ? AND e.status <> 'cancelada' ORDER BY e.ordem",
        (viagem_id,)).fetchall()

    saida = []
    for escala in escalas:
        exigidos = [r[0] for r in conn.execute(
            "SELECT tipo_evento FROM marco_exigido WHERE tipo_escala = ? ORDER BY ordem",
            (escala["tipo_escala"],))]
        lancados = {r["tipo"]: r for r in conn.execute(
            "SELECT tipo, hora_local, offset_utc, nome_responsavel, versao, observacao, "
            "       rob_vlsfo, rob_mgo "
            "  FROM evento_vigente WHERE escala_id = ?", (escala["id"],))}
        bunker = conn.execute(
            "SELECT vlsfo, mgo, nome_responsavel FROM abastecimento WHERE escala_id = ?",
            (escala["id"],)).fetchone()
        carga = conn.execute(
            "SELECT carregado, descarregado, nome_responsavel "
            "  FROM movimento_carga WHERE escala_id = ?", (escala["id"],)).fetchone()
        saida.append({
            "escala": escala,
            "marcos": [{
                "tipo": t,
                "rotulo": dominio.ROTULO_MARCO[t],
                "curto": dominio.ROTULO_CURTO[t],
                "lancado": lancados.get(t),
                # O Unberth de Alumar e o ultimo lancamento da viagem: quando
                # ele e salvo, a viagem fecha, outra abre e a pagina mudou toda.
                "encerra": (t == "unberth"
                            and escala["tipo_escala"] == "encerramento"),
            } for t in exigidos],
            "completa": all(t in lancados for t in exigidos),
            # So escala de bunker pede quantidade abastecida.
            "e_bunker": escala["motivo"] == "bunker",
            "bunker": bunker,
            # So quem carrega ou descarrega movimenta carga. A direcao vem da
            # condicao, nunca de uma escolha do comandante.
            "movimenta_carga": escala["condicao"] in ("loading", "discharging"),
            "carrega": escala["condicao"] == "loading",
            "carga": carga,
        })
    return saida


def _proximo_lancamento(blocos):
    """A primeira coisa que falta, na ordem da viagem.

    E o que permite a tela ter UMA acao obvia em vez de quinze formularios
    abertos: num celular, quinze formularios sao um paredao.
    """
    for bloco in blocos:
        for marco in bloco["marcos"]:
            if not marco["lancado"]:
                return {"escala": bloco["escala"], "marco": marco,
                        "bunker": False, "carga": False}
        if bloco["e_bunker"] and bloco["bunker"] is None:
            return {"escala": bloco["escala"], "marco": None,
                    "bunker": True, "carga": False}
        if bloco["movimenta_carga"] and bloco["carga"] is None:
            return {"escala": bloco["escala"], "marco": None,
                    "bunker": False, "carga": True,
                    "carrega": bloco["carrega"]}
    return None


def _lista_pt(nomes) -> str:
    """['Unberth', 'Sailing'] -> 'Unberth e Sailing'."""
    nomes = list(nomes)
    if len(nomes) <= 1:
        return "".join(nomes)
    return "{} e {}".format(", ".join(nomes[:-1]), nomes[-1])


def _resumo_marcos(marcos) -> str:
    """'Arrival 02/03/2026 22:30 · Berth 03/03/2026 06:10' — o que ja foi lancado.

    Pelo nome inteiro do marco e com a data inteira. Ja houve uma versao com
    inicial e data curta (A · B · U · S, 02/03) e o Vinicius recusou: vira
    codigo a decifrar num sistema que tem as palavras certas.
    """
    feitos = [
        "{} {}".format(marco["curto"], formato_br(marco["lancado"]["hora_local"])).strip()
        for marco in marcos if marco["lancado"]
    ]
    return " · ".join(feitos)


def _falta_marcos(marcos) -> str:
    """'Unberth e Sailing' — o que a parada ainda espera.

    E o que a contagem '2 de 4' nao diz: QUAL marco falta. Sem isso o
    comandante precisa abrir a parada para descobrir.
    """
    return _lista_pt(marco["curto"] for marco in marcos if not marco["lancado"])


def _situacao(blocos):
    """Onde o navio esta e o que estava fazendo, pelo ultimo marco lancado."""
    ultimo = None
    for bloco in blocos:
        for marco in bloco["marcos"]:
            if marco["lancado"]:
                ultimo = {"escala": bloco["escala"], "marco": marco}
    return ultimo


def _contexto_navio(conta) -> dict:
    """Tudo o que a tela do comandante precisa.

    Serve as DUAS rotas: a pagina inteira e o fragmento de `GET /navio/tela`.
    Uma funcao so porque o fragmento tem que mostrar exatamente o mesmo que a
    pagina mostraria — se as duas montassem contexto por conta propria, um dia
    divergiriam e o comandante veria uma tela que nao existe.
    """
    with closing(db.conectar()) as conn:
        # Sem viagem aberta o comandante ficaria sem onde lancar.
        if _viagem_do_navio(conn, conta["navio_id"]) is None:
            viagens.abrir_viagem(conn, conta["navio_id"], por=conta["login"])

        linhas = conn.execute(
            "SELECT vg.id, vg.numero, vg.status, ev.hora_local AS abertura "
            "  FROM viagem vg "
            "  LEFT JOIN evento ev ON ev.id = vg.evento_abertura_id "
            " WHERE vg.navio_id = ? "
            " ORDER BY (vg.status = 'aberta') DESC, vg.id DESC LIMIT 6",
            (conta["navio_id"],)).fetchall()

        lista = []
        for viagem in linhas:
            faltantes = conn.execute(
                "SELECT COUNT(*) FROM escalas_incompletas WHERE viagem_id = ?",
                (viagem["id"],)).fetchone()[0]
            blocos = _escalas_com_marcos(conn, viagem["id"])
            for bloco in blocos:
                lancados = sum(1 for m in bloco["marcos"] if m["lancado"])
                bloco["lancados"] = lancados
                bloco["total"] = len(bloco["marcos"])
                bloco["estado"] = ("pronta" if bloco["completa"]
                                   else "parcial" if lancados else "vazia")
                bloco["resumo"] = _resumo_marcos(bloco["marcos"])
                bloco["falta"] = _falta_marcos(bloco["marcos"])
            lista.append({
                "viagem": viagem,
                "aberta": viagem["status"] == "aberta",
                "faltantes": faltantes,
                "blocos": blocos,
                # O Sailing de Alumar que abre a viagem — o comeco da historia,
                # que antes ficava invisivel para quem estava preenchendo.
                "abertura": viagem["abertura"],
                "proximo": _proximo_lancamento(blocos),
                "situacao": _situacao(blocos),
            })

        portos = conn.execute(
            "SELECT codigo, nome FROM porto WHERE ativo = 1 ORDER BY nome").fetchall()

    corrente = next((v for v in lista if v["aberta"]), None)
    lancados = sum(len([m for m in b["marcos"] if m["lancado"]])
                   for b in (corrente["blocos"] if corrente else []))
    total = sum(len(b["marcos"]) for b in (corrente["blocos"] if corrente else []))

    return {"conta": conta, "lista": lista, "portos": portos,
            "corrente": corrente, "lancados": lancados, "total": total}


@app.get("/navio", response_class=HTMLResponse)
def navio_inicio(request: Request):
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return RedirectResponse("/painel", 303)
    return templates.TemplateResponse(
        request, "navio_inicio.html", _contexto_navio(conta))


@app.get("/navio/tela", response_class=HTMLResponse)
def navio_tela(request: Request):
    """So a tela da viagem, sem a pagina em volta.

    E o que substituiu o `location.reload()` depois de cada lancamento. O
    reload deixava a tela em branco enquanto o Render acordava — 20 a 50
    segundos parecendo travamento, logo depois de o comandante clicar Salvar.
    Aqui so o miolo volta, e a pagina nunca pisca.
    """
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return HTMLResponse("", status_code=403)
    contexto = _contexto_navio(conta)
    if contexto["corrente"] is None:
        # Sem viagem aberta a tela inteira muda de forma; o JavaScript recarrega.
        return HTMLResponse("", status_code=409)
    return templates.TemplateResponse(request, "_tela_viagem.html", contexto)


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
            rob_vlsfo=corpo.get("rob_vlsfo"),
            rob_mgo=corpo.get("rob_mgo"),
        )

    if erros:
        # 422: o dado esta errado e reenviar nao vai adiantar. A fila precisa
        # distinguir isto de "servidor fora do ar", ou fica repetindo para sempre.
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)

    # O sailing de Alumar encerra a viagem e abre a seguinte. A tela precisa
    # saber disso para recarregar — o conteudo inteiro mudou.
    with closing(db.conectar()) as conn:
        ainda_aberta = conn.execute(
            "SELECT 1 FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE e.id = ? AND vg.status = 'aberta'", (escala["id"],)).fetchone()
    return {"ok": True, "evento_id": evento_id, "viagem_mudou": ainda_aberta is None}


@app.post("/api/abastecimento")
async def api_abastecimento(request: Request):
    """Quanto entrou de combustivel nesta escala. Mesmo contrato do marco:
    sempre JSON, 422 no dado invalido para a fila nao repetir."""
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

        gravou, erros = viagens.registrar_abastecimento(
            conn, escala["id"],
            vlsfo=corpo.get("vlsfo"),
            mgo=corpo.get("mgo"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            observacao=(corpo.get("observacao") or "").strip() or None)

    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}


@app.post("/api/carga")
async def api_carga(request: Request):
    """Quanto de carga entrou ou saiu nesta escala, em MT.

    Mesmo contrato do marco: sempre JSON, 422 no dado invalido para a fila nao
    ficar repetindo. A direcao (carrega ou descarrega) vem da condicao da
    escala, nao do corpo da requisicao.
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

        gravou, erros = viagens.registrar_movimento_carga(
            conn, escala["id"],
            quantidade=corpo.get("quantidade"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            observacao=(corpo.get("observacao") or "").strip() or None)

    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}


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


@app.post("/navio/escala-extra/remover")
def navio_escala_extra_remover(request: Request, escala_id: int = Form(...)):
    """Desfaz uma parada acrescentada por engano.

    O `escala_id` vem do formulario, entao o vinculo com o navio da sessao e
    conferido AQUI: sem isso um comandante removeria parada de outro navio so
    trocando o numero.
    """
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        dono = conn.execute(
            "SELECT 1 FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE e.id = ? AND vg.navio_id = ?",
            (escala_id, conta["navio_id"])).fetchone()
        erros = (["Parada não encontrada nesta embarcação."] if dono is None
                 else viagens.remover_escala_extra(conn, escala_id)[1])
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


# ---------------------------------------------------------------------------
# Administracao de contas
#
# Existe porque o plano free do Render nao tem terminal: o script de linha de
# comando funciona na maquina do Vinicius, mas em producao nao ha onde roda-lo.
# ---------------------------------------------------------------------------

def _so_admin(request: Request):
    return request.state.conta["perfil"] == "admin"


@app.get("/admin/contas", response_class=HTMLResponse)
def admin_contas(request: Request, erro: str = "", ok: str = ""):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        lista = conn.execute(
            "SELECT c.login, c.nome_exibicao, c.perfil, c.ativo, n.nome_oficial AS navio "
            "  FROM conta c LEFT JOIN navio n ON n.id = c.navio_id "
            " ORDER BY c.perfil, c.login").fetchall()
        navios = conn.execute(
            "SELECT id, nome_oficial FROM navio WHERE ativo = 1 ORDER BY id").fetchall()
    return templates.TemplateResponse(request, "admin_contas.html", {
        "conta": request.state.conta, "lista": lista, "navios": navios,
        "erro": erro, "ok": ok})


@app.post("/admin/contas")
def admin_criar_conta(
    request: Request,
    login: str = Form(...),
    nome_exibicao: str = Form(...),
    perfil: str = Form(...),
    senha: str = Form(...),
    navio_id: str = Form(""),
):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        criado, erros = contas.criar_conta(
            conn, login=login, senha=senha, nome_exibicao=nome_exibicao,
            perfil=perfil, navio_id=int(navio_id) if navio_id.strip() else None)
    if erros:
        return RedirectResponse(
            "/admin/contas?erro=" + quote(" | ".join(erros)), 303)
    return RedirectResponse(
        "/admin/contas?ok=" + quote("Conta {} criada.".format(criado)), 303)


@app.post("/admin/contas/senha")
def admin_trocar_senha(request: Request, login: str = Form(...), senha: str = Form(...)):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        trocou, erros = contas.trocar_senha(conn, login, senha)
    destino = "/admin/contas?" + (
        "erro=" + quote(" | ".join(erros)) if erros
        else "ok=" + quote("Senha de {} trocada.".format(login)))
    return RedirectResponse(destino, 303)
