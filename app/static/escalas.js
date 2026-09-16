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
      // Em viagens encerradas nao ha miolo a trocar, e a correcao mexe no
      // RESUMO da viagem la em cima — duracao, espera, atracado. Deixar so a
      // linha atualizada faria a tela mostrar a hora nova com a duracao velha.
      if (form.dataset.recarrega === "1") {
        window.location.reload();
        return;
      }
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
   var fw = (dados.get("fw") || "").trim();
   var lixo = (dados.get("lixo") || "").trim();
   var comentarios = (dados.get("comentarios") || "").trim();

   var detalhes = [];
   if (vlsfo) detalhes.push("VLSFO " + vlsfo);
   if (mgo) detalhes.push("MGO " + mgo);
   if (fw) detalhes.push("FW " + fw + "t");
   if (lixo) detalhes.push("Lixo: " + lixo);

   despachar(form, bloco, {
     escala_id: Number(bloco.dataset.escala),
     tipo: bloco.dataset.tipo,
     hora_local: data + "T" + hora,
     offset: form.dataset.offset,
     nome_responsavel: nome,
     motivo_correcao: (dados.get("motivo_correcao") || "").trim(),
     rob_vlsfo: vlsfo || null,
     rob_mgo: mgo || null,
     fw: fw || null,
     lixo: lixo || null,
     comentarios: comentarios || null
   }, "/api/marco", {
     nome: form.dataset.nome,
     valor: data.split("-").reverse().join("/") + " " + hora,
     detalhe: detalhes.join(" · "),
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

/* Integração do Painel Lado a Lado com o Backend Corsair */
(function () {
  "use strict";

  var ETAPAS_ROTA = [
    { loc: "Alumar - MA", acao: "sailing", prop: "Laden", desc: "1. Alumar — Início de Singradura p/ Fazendinha" },
    { loc: "Barra Norte", acao: "arrival", prop: "Laden", desc: "2. Barra Norte — Entrada no Canal / Espera Prático" },
    { loc: "Fazendinha", acao: "arrival", prop: "Laden", desc: "3. Fazendinha — Embarque Praticagem Macapá" },
    { loc: "Fazendinha", acao: "sailing", prop: "Laden", desc: "4. Fazendinha — Singradura Rio Amazonas p/ Juruti" },
    { loc: "Juruti", acao: "arrival", prop: "Loading", desc: "5. Juruti — Chegada na Barra / Fundeado" },
    { loc: "Juruti", acao: "berth", prop: "Loading", desc: "6. Juruti — Atracação Terminal Fluvial" },
    { loc: "Juruti", acao: "Fueling", prop: "Loading", desc: "7. Juruti — Operação de Carga / Bauxita" },
    { loc: "Juruti", acao: "unberth", prop: "Laden", desc: "8. Juruti — Término Carga & Desatracação" },
    { loc: "Juruti", acao: "sailing", prop: "Laden", desc: "9. Juruti — Singradura Descendo Rio Amazonas" },
    { loc: "Fazendinha", acao: "arrival", prop: "Laden", desc: "10. Fazendinha — Desembarque Praticagem" },
    { loc: "Fazendinha", acao: "sailing", prop: "Laden", desc: "11. Fazendinha — Saída Barra Norte Rumo Alumar" },
    { loc: "Alumar - MA", acao: "arrival", prop: "Discharging", desc: "12. Alumar — Chegada Barra São Marcos / Fundeado" },
    { loc: "Alumar - MA", acao: "berth", prop: "Discharging", desc: "13. Alumar — Atracação Terminal Alumar" },
    { loc: "Alumar - MA", acao: "Standby", prop: "Discharging", desc: "14. Alumar — Operação de Descarga Bauxita" },
    { loc: "Alumar - MA", acao: "unberth", prop: "Laden", desc: "15. Alumar — Término Descarga & Conclusão Ciclo" }
  ];

  var historicoOcorrencias = [];
  var idSelecionadoCtx = null;

  function sincronizarRelogio() {
    var agora = new Date();
    var campoData = document.getElementById("campo-data");
    var campoHora = document.getElementById("campo-hora");
    if (campoData && campoHora) {
      campoData.value = agora.toISOString().slice(0, 10);
      campoHora.value = agora.toISOString().slice(11, 16);
    }
  }

  function atualizarSugerido() {
    var label = document.getElementById("texto-etapa-sugerida");
    if (!label) return;
    var idx = historicoOcorrencias.length;
    if (idx < ETAPAS_ROTA.length) {
      label.textContent = ETAPAS_ROTA[idx].desc;
    } else {
      label.textContent = "Etapas padrão concluídas. Registros extras livres.";
    }
  }

  // Preencher etapa assistida
  var btnEtapa = document.getElementById("btn-aplicar-etapa");
  if (btnEtapa) {
    btnEtapa.addEventListener("click", function () {
      var idx = historicoOcorrencias.length;
      if (idx < ETAPAS_ROTA.length) {
        var item = ETAPAS_ROTA[idx];
        document.getElementById("campo-local").value = item.loc;
        document.getElementById("campo-acao").value = item.acao;
        document.getElementById("campo-proposito").value = item.prop;
      }
    });
  }

  var btnRelogio = document.getElementById("btn-sync-relogio");
  if (btnRelogio) btnRelogio.addEventListener("click", sincronizarRelogio);

  // Renderizar Tabela à Direita
  function renderizarTabela() {
    var corpo = document.getElementById("corpo-tabela-logbook");
    if (!corpo) return;
    corpo.innerHTML = "";

    for (var i = historicoOcorrencias.length - 1; i >= 0; i--) {
      var r = historicoOcorrencias[i];
      var tr = document.createElement("tr");
      var seloRetificado = r.isEdited ? '<span class="tag-retificado" title="' + (r.motivo || '') + '">RETIFICADO</span>' : '';

      tr.innerHTML =
        '<td><button type="button" class="btn-mini-acao" data-id="' + r.id + '">✏️ Editar</button></td>' +
        '<td><strong>#' + r.id + '</strong>' + seloRetificado + '</td>' +
        '<td class="num">' + r.data.split("-").reverse().join("/") + ' ' + r.hora + '</td>' +
        '<td>' + r.local + '</td>' +
        '<td><span class="selo">' + r.acao + '</span></td>' +
        '<td>' + r.proposito + '</td>' +
        '<td class="num">' + (r.vlsfo || '—') + '</td>' +
        '<td class="num">' + (r.mgo || '—') + '</td>' +
        '<td class="num">' + (r.fw ? r.fw + 't' : '—') + '</td>' +
        '<td>' + (r.lixo || '—') + '</td>' +
        '<td>' + (r.comentarios || '—') + '</td>';

      (function (registro) {
        tr.addEventListener("contextmenu", function (e) {
          e.preventDefault();
          idSelecionadoCtx = registro.id;
          var menu = document.getElementById("menu-contexto-logbook");
          if (menu) {
            menu.style.display = "block";
            menu.style.left = e.pageX + "px";
            menu.style.top = e.pageY + "px";
          }
        });
      })(r);

      corpo.appendChild(tr);
    }
  }

  // Interceptar Envio do Formulário Principal
  var formPrincipal = document.getElementById("form-registro-principal");
  if (formPrincipal) {
    formPrincipal.addEventListener("submit", function (e) {
      e.preventDefault();

      var novoItem = {
        id: historicoOcorrencias.length + 1,
        data: document.getElementById("campo-data").value,
        hora: document.getElementById("campo-hora").value,
        agente: document.getElementById("campo-agente").value,
        local: document.getElementById("campo-local").value,
        proposito: document.getElementById("campo-proposito").value,
        acao: document.getElementById("campo-acao").value,
        vlsfo: document.getElementById("campo-vlsfo").value,
        mgo: document.getElementById("campo-mgo").value,
        fw: document.getElementById("campo-fw").value,
        lixo: document.getElementById("campo-lixo").value,
        comentarios: document.getElementById("campo-comentarios").value,
        isEdited: false
      };

      historicoOcorrencias.push(novoItem);

      // Despacha para a API e fila offline nativa do Corsair
      var escalaId = Number(document.getElementById("campo-escala-id").value) || 1;
      if (window.Fila && window.Fila.enfileirar) {
        window.Fila.enfileirar({
          escala_id: escalaId,
          tipo: novoItem.acao.toLowerCase(),
          hora_local: novoItem.data + "T" + novoItem.hora,
          offset: "-03:00",
          nome_responsavel: (localStorage.getItem("shipops.responsavel") || "Comandante"),
          rob_vlsfo: novoItem.vlsfo || null,
          rob_mgo: novoItem.mgo || null,
          fw: novoItem.fw || null,
          lixo: novoItem.lixo || null,
          comentarios: novoItem.comentarios || null
        }, "/api/marco");
      }

      renderizarTabela();
      atualizarSugerido();

      // Limpa combustíveis e notas mantendo o relógio atualizado
      document.getElementById("campo-vlsfo").value = "";
      document.getElementById("campo-mgo").value = "";
      document.getElementById("campo-fw").value = "";
      document.getElementById("campo-lixo").value = "";
      document.getElementById("campo-comentarios").value = "";
      sincronizarRelogio();
    });
  }

  // Exportar JSON direto
  var btnExport = document.getElementById("btn-exportar-json");
  if (btnExport) {
    btnExport.addEventListener("click", function () {
      if (!historicoOcorrencias.length) {
        alert("Nenhum lançamento registrado para exportar.");
        return;
      }
      var blob = new Blob([JSON.stringify(historicoOcorrencias, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "ELB_Viagem_" + Date.now() + ".json";
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  // Fechar menu de clique direito
  window.addEventListener("click", function () {
    var menu = document.getElementById("menu-contexto-logbook");
    if (menu) menu.style.display = "none";
  });

  sincronizarRelogio();
  atualizarSugerido();
})();
