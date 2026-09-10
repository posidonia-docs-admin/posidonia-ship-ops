/* Lancamento no lugar — sem trocar de tela.
 *
 * O marco vai para a fila local e a linha vira "gravado" na hora. A pagina so
 * recarrega quando o Sailing de Alumar fecha a viagem e abre a proxima, porque
 * ai o conteudo inteiro mudou.
 */
(function () {
  "use strict";

  var CHAVE_NOME = "shipops.responsavel";
  var campoNome = document.getElementById("responsavel");

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

  function marcarGravado(bloco, form, valor, nome) {
    // Troca o formulario pela linha gravada, sem recarregar a pagina.
    var caixa = document.createElement("div");
    caixa.className = "gravado";
    caixa.innerHTML =
      '<span class="marco-nome"></span><span class="valor"></span>' +
      '<span class="por"></span><span class="estado salvo" data-estado></span>';
    caixa.querySelector(".marco-nome").textContent =
      (bloco.querySelector(".marco-nome") || {}).textContent || "";
    caixa.querySelector(".valor").textContent = valor;
    caixa.querySelector(".por").textContent = nome;
    caixa.querySelector("[data-estado]").textContent = "salvo";
    var container = form.closest("details") || form;
    container.replaceWith(caixa);
    if (!bloco.contains(caixa)) bloco.appendChild(caixa);
  }

  function enviar(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;

    var bloco = form.closest("[data-escala][data-tipo]");
    if (!bloco) return;

    var nome = campoNome ? campoNome.value.trim() : "";
    if (!nome) {
      estado(bloco, "erro", "informe quem preenche");
      if (campoNome) { campoNome.focus(); campoNome.scrollIntoView({ block: "center" }); }
      return;
    }
    try { localStorage.setItem(CHAVE_NOME, nome); } catch (e) {}

    var dados = new FormData(form);
    var data = (dados.get("data") || "").trim();
    var hora = (dados.get("hora") || "").trim();
    if (!data || !hora) return;

    var payload = {
      escala_id: Number(bloco.dataset.escala),
      tipo: bloco.dataset.tipo,
      hora_local: data + "T" + hora,
      offset: form.dataset.offset,
      nome_responsavel: nome,
      motivo_correcao: (dados.get("motivo_correcao") || "").trim(),
      // Combustivel a bordo NESTE instante. Opcional: campo vazio nao segura o
      // marco — perder a hora por causa do ROB seria trocar o certo pelo util.
      rob_vlsfo: (dados.get("rob_vlsfo") || "").trim() || null,
      rob_mgo: (dados.get("rob_mgo") || "").trim() || null
    };

    var botao = form.querySelector("button[type=submit]");
    if (botao) botao.disabled = true;
    estado(bloco, "pendente", "salvando...");

    window.Fila.enfileirar(payload).then(function (resultado) {
      if (resultado && resultado.erros && resultado.erros.length) {
        estado(bloco, "erro", resultado.erros.join(" "));
        if (botao) botao.disabled = false;
        return;
      }
      // Do cartao "proximo lancamento" a pagina recarrega: e assim que a
      // viagem se traduz — o cartao avanca sozinho para o marco seguinte.
      // No resto da trilha a linha vira "gravado" sem sair do lugar.
      if (form.dataset.avanca === "1" ||
          (form.dataset.encerra === "1" && resultado && resultado.viagemMudou)) {
        window.location.reload();
        return;
      }
      marcarGravado(bloco, form, data + " " + hora, nome);
    }).catch(function () {
      // Ficou na fila e sera reenviado sozinho; nao e erro para o comandante.
      estado(bloco, "pendente", "sem conexão — será enviado");
      if (botao) botao.disabled = false;
    });
  }

  function abastecer(form, evento) {
    evento.preventDefault();
    var bloco = form.closest("[data-escala][data-tipo]");
    if (!bloco) return;

    var nome = campoNome ? campoNome.value.trim() : "";
    if (!nome) {
      estado(bloco, "erro", "informe quem preenche");
      if (campoNome) { campoNome.focus(); campoNome.scrollIntoView({ block: "center" }); }
      return;
    }

    var dados = new FormData(form);
    var payload = {
      escala_id: Number(bloco.dataset.escala),
      tipo: bloco.dataset.tipo,
      vlsfo: (dados.get("vlsfo") || "").trim() || null,
      mgo: (dados.get("mgo") || "").trim() || null,
      nome_responsavel: nome
    };

    var botao = form.querySelector("button[type=submit]");
    if (botao) botao.disabled = true;
    estado(bloco, "pendente", "salvando...");

    window.Fila.enfileirar(payload, "/api/abastecimento").then(function (r) {
      if (r && r.erros && r.erros.length) {
        estado(bloco, "erro", r.erros.join(" "));
      } else if (form.dataset.avanca === "1") {
        window.location.reload();
        return;
      } else {
        estado(bloco, "salvo", "salvo");
      }
      if (botao) botao.disabled = false;
    }).catch(function () {
      estado(bloco, "pendente", "sem conexão — será enviado");
      if (botao) botao.disabled = false;
    });
  }

  document.addEventListener("submit", function (evento) {
    var form = evento.target;
    if (!form.classList) return;
    if (form.classList.contains("lancar")) enviar(form, evento);
    else if (form.classList.contains("abastecer")) abastecer(form, evento);
  });
})();
