/* Lancamento no lugar — a pagina NUNCA recarrega.
 *
 * Antes, todo lancamento do cartao "proximo lancamento" terminava em
 * `location.reload()`. Numa maquina que dormiu no Render isso e a tela em
 * branco por 20 a 50 segundos logo depois do clique em Salvar: parece
 * travamento, e o formulario reaparece preenchido como se nada tivesse sido
 * gravado. Foi exatamente o que o Vinicius descreveu.
 *
 * Agora sao dois movimentos:
 *   1. O formulario vira REGISTRO na hora, com o que ele acabou de digitar.
 *   2. Em segundo plano, `GET /navio/tela` traz so o miolo da tela e a caixa
 *      e trocada — o cartao avanca para o marco seguinte, a trilha e a
 *      contagem se atualizam, e a pagina nao pisca.
 *
 * Sem conexao o passo 2 nao acontece: o registro fica marcado como "na fila",
 * porque o lancamento ESTA salvo no aparelho e sai sozinho depois.
 */
(function () {
  "use strict";

  var CHAVE_NOME = "shipops.responsavel";
  var campoNome = document.getElementById("responsavel");
  var caixaTela = document.getElementById("tela-viagem");

  // Ele digita o nome uma vez; nas proximas ja vem preenchido.
  if (campoNome) {
    try {
      var guardado = localStorage.getItem(CHAVE_NOME);
      if (guardado && !campoNome.value) campoNome.value = guardado;
    } catch (e) { /* storage bloqueado: segue sem lembrar */ }
    campoNome.addEventListener("change", function () {
      try { localStorage.setItem(CHAVE_NOME, campoNome.value.trim()); } catch (e) {}
    });
  }

  function estado(bloco, classe, texto) {
    var alvo = bloco.querySelector("[data-estado]");
    if (!alvo) return;
    alvo.className = "estado " + classe;
    alvo.textContent = texto;
  }

  function quemPreenche(bloco) {
    var nome = campoNome ? campoNome.value.trim() : "";
    if (!nome) {
      estado(bloco, "erro", "informe quem preenche");
      if (campoNome) {
        campoNome.focus();
        campoNome.scrollIntoView({ block: "center" });
      }
      return "";
    }
    try { localStorage.setItem(CHAVE_NOME, nome); } catch (e) {}
    return nome;
  }

  /* ---- o formulario vira registro -------------------------------------- */

  function texto(pai, classe, valor) {
    if (!valor) return;
    var el = document.createElement("span");
    el.className = classe;
    el.textContent = valor;
    pai.appendChild(el);
  }

  /* Troca o formulario por uma linha gravada. E o "fixar como se fosse um
   * registro" que o Vinicius pediu: o campo editavel some, e o que ficou na
   * tela e o que foi salvo — nao ha mais como duvidar se pegou. */
  function virarRegistro(bloco, form, dados) {
    var caixa = document.createElement("div");
    caixa.className = "gravado";
    texto(caixa, "marco-nome", dados.nome ||
          (bloco.querySelector(".marco-nome") || {}).textContent || "");
    texto(caixa, "valor", dados.valor);
    texto(caixa, "rob", dados.detalhe);
    texto(caixa, "por", dados.por);

    var marca = document.createElement("span");
    marca.className = "estado " + (dados.pendente ? "pendente" : "salvo");
    marca.textContent = dados.pendente ? "na fila — será enviado" : "salvo";
    marca.setAttribute("data-estado", "");
    caixa.appendChild(marca);

    var container = form.closest("details") || form;
    container.replaceWith(caixa);
    if (!bloco.contains(caixa)) bloco.appendChild(caixa);
    return caixa;
  }

  /* ---- troca so o miolo da tela ---------------------------------------- */

  /* Que paradas estavam abertas. Trocar o miolo fecharia todas, e fechar na
   * cara de quem estava lendo e o tipo de detalhe que faz a tela parecer que
   * "pulou" — o mesmo incomodo do reload, em miniatura. */
  function abertas() {
    var ids = [];
    Array.prototype.forEach.call(
      document.querySelectorAll(".parada[open][data-escala]"),
      function (d) { ids.push(d.dataset.escala); });
    return ids;
  }

  function reabrir(ids) {
    if (!ids.length) return;
    Array.prototype.forEach.call(
      document.querySelectorAll(".parada[data-escala]"),
      function (d) { if (ids.indexOf(d.dataset.escala) >= 0) d.open = true; });
  }

  var trocando = false;

  function trocarTela() {
    if (!caixaTela || trocando) return Promise.resolve();
    trocando = true;
    caixaTela.classList.add("atualizando");
    var reabrirDepois = abertas();

    return fetch("/navio/tela", {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" }
    }).then(function (r) {
      // 409: a viagem acabou e outra comecou — a pagina inteira mudou de forma.
      // 401/403 ou redirect para o login: a sessao caiu. Nos dois casos so o
      // recarregamento resolve, e ai ele e o certo, nao o atalho.
      if (r.status === 409 || r.status === 401 || r.status === 403 || r.redirected) {
        window.location.reload();
        return null;
      }
      if (!r.ok) throw new Error("fragmento indisponivel");
      return r.text();
    }).then(function (html) {
      if (html === null) return;
      caixaTela.innerHTML = html;
      reabrir(reabrirDepois);
      if (window.Fila && window.Fila.pintar) window.Fila.pintar();
    }).catch(function () {
      // O registro otimista continua na tela; nada se perde. A tela alcanca o
      // servidor no proximo lancamento ou quando o comandante abrir de novo.
    }).then(function () {
      trocando = false;
      caixaTela.classList.remove("atualizando");
    });
  }

  /* ---- os tres lancamentos --------------------------------------------- */

  function despachar(form, bloco, payload, url, registro) {
    var botao = form.querySelector("button[type=submit]");
    if (botao) botao.disabled = true;
    estado(bloco, "pendente", "salvando...");

    return window.Fila.enfileirar(payload, url).then(function (r) {
      if (r && r.erros && r.erros.length) {
        estado(bloco, "erro", r.erros.join(" "));
        if (botao) botao.disabled = false;
        return;
      }
      virarRegistro(bloco, form, registro);
      return trocarTela();
    }).catch(function () {
      // Ficou na fila local e sera reenviado sozinho. Vira registro do mesmo
      // jeito: reabrir o formulario so convidaria a digitar duas vezes.
      registro.pendente = true;
      virarRegistro(bloco, form, registro);
    });
  }

  function enviar(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;

    var bloco = form.closest("[data-escala][data-tipo]");
    if (!bloco) return;
    var nome = quemPreenche(bloco);
    if (!nome) return;

    var dados = new FormData(form);
    var data = (dados.get("data") || "").trim();
    var hora = (dados.get("hora") || "").trim();
    if (!data || !hora) return;

    var vlsfo = (dados.get("rob_vlsfo") || "").trim();
    var mgo = (dados.get("rob_mgo") || "").trim();
    var rob = "";
    if (vlsfo || mgo) rob = "VLSFO " + (vlsfo || "—") + " · MGO " + (mgo || "—");

    despachar(form, bloco, {
      escala_id: Number(bloco.dataset.escala),
      tipo: bloco.dataset.tipo,
      hora_local: data + "T" + hora,
      offset: form.dataset.offset,
      nome_responsavel: nome,
      motivo_correcao: (dados.get("motivo_correcao") || "").trim(),
      // Combustivel a bordo NESTE instante. Opcional: campo vazio nao segura o
      // marco — perder a hora por causa do ROB seria trocar o certo pelo util.
      rob_vlsfo: vlsfo || null,
      rob_mgo: mgo || null
    }, "/api/marco", {
      nome: form.dataset.nome,
      valor: data.split("-").reverse().join("/") + " " + hora,
      detalhe: rob,
      por: nome
    });
  }

  function abastecer(form, evento) {
    evento.preventDefault();
    var bloco = form.closest("[data-escala][data-tipo]");
    if (!bloco) return;
    var nome = quemPreenche(bloco);
    if (!nome) return;

    var dados = new FormData(form);
    var vlsfo = (dados.get("vlsfo") || "").trim();
    var mgo = (dados.get("mgo") || "").trim();
    if (!vlsfo && !mgo) {
      estado(bloco, "erro", "informe VLSFO ou MGO");
      return;
    }

    despachar(form, bloco, {
      escala_id: Number(bloco.dataset.escala),
      tipo: bloco.dataset.tipo,
      vlsfo: vlsfo || null,
      mgo: mgo || null,
      nome_responsavel: nome
    }, "/api/abastecimento", {
      nome: form.dataset.nome || "Abastecido",
      valor: "VLSFO " + (vlsfo || "—") + " · MGO " + (mgo || "—"),
      por: nome
    });
  }

  function movimentarCarga(form, evento) {
    evento.preventDefault();
    var bloco = form.closest("[data-escala][data-tipo]");
    if (!bloco) return;
    var nome = quemPreenche(bloco);
    if (!nome) return;

    var quantidade = (new FormData(form).get("quantidade") || "").trim();
    if (!quantidade) {
      estado(bloco, "erro", "informe a quantidade em MT");
      return;
    }

    // A direcao (carrega ou descarrega) vem da condicao da escala, no servidor.
    despachar(form, bloco, {
      escala_id: Number(bloco.dataset.escala),
      tipo: bloco.dataset.tipo,
      quantidade: quantidade,
      nome_responsavel: nome
    }, "/api/carga", {
      nome: form.dataset.nome || "Carga",
      valor: quantidade + " MT",
      por: nome
    });
  }

  // Delegado no documento: vale tambem para os formularios que chegam na troca
  // do miolo, sem precisar religar nada depois.
  document.addEventListener("submit", function (evento) {
    var form = evento.target;
    if (!form.classList) return;
    if (form.classList.contains("lancar")) enviar(form, evento);
    else if (form.classList.contains("abastecer")) abastecer(form, evento);
    else if (form.classList.contains("carregar")) movimentarCarga(form, evento);
  });
})();
