/* Tela de lancamento de marco.
 *
 * O envio nunca e sincrono: o marco vai para a fila local e a tela volta na
 * hora. Se o servidor estiver dormindo, o comandante nao espera por ele.
 */
(function () {
  "use strict";

  var form = document.getElementById("form-marco");
  if (!form) return;

  var CHAVE_NOME = "shipops.nome_responsavel";
  var campoNome = form.elements.nome_responsavel;

  // Ele digita o nome uma vez; nas proximas o campo ja vem preenchido.
  if (campoNome && !campoNome.value) {
    try {
      var guardado = localStorage.getItem(CHAVE_NOME);
      if (guardado) campoNome.value = guardado;
    } catch (e) { /* aparelho com storage bloqueado: segue sem lembrar */ }
  }

  form.addEventListener("submit", function (evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;

    var dados = new FormData(form);
    var data = (dados.get("data") || "").trim();
    var hora = (dados.get("hora") || "").trim();
    if (!data || !hora) return;

    var nome = (dados.get("nome_responsavel") || "").trim();
    try { localStorage.setItem(CHAVE_NOME, nome); } catch (e) { /* idem */ }

    var payload = {
      escala_id: Number(form.dataset.escala),
      tipo: form.dataset.tipo,
      hora_local: data + "T" + hora,
      offset: dados.get("offset"),
      nome_responsavel: nome,
      observacao: (dados.get("observacao") || "").trim(),
      motivo_correcao: (dados.get("motivo_correcao") || "").trim()
    };

    var botao = form.querySelector("button[type=submit]");
    if (botao) { botao.disabled = true; botao.textContent = "Salvando..."; }

    window.Fila.enfileirar(payload).then(function () {
      window.location.href = "/navio";
    }).catch(function () {
      // Fila local indisponivel (aba anonima, storage bloqueado): manda direto.
      fetch("/api/marco", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (r) {
        if (r.ok) { window.location.href = "/navio"; return; }
        return r.json().catch(function () { return {}; }).then(function (corpo) {
          alert((corpo.erros || ["Nao foi possivel salvar."]).join("\n"));
          if (botao) { botao.disabled = false; botao.textContent = "Salvar"; }
        });
      }).catch(function () {
        alert("Sem conexao e sem fila local. Tente de novo em instantes.");
        if (botao) { botao.disabled = false; botao.textContent = "Salvar"; }
      });
    });
  });
})();
