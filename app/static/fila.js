/* Fila local de lancamentos.
 *
 * O lancamento e gravado NO APARELHO no instante em que o comandante toca
 * Salvar, e enviado ao servidor em segundo plano. Se o servidor demorar 50s
 * para acordar, ele nao percebe.
 *
 * A distincao que faz isto funcionar: erro de VALIDACAO (422) nao se repete —
 * reenviar nao vai consertar o dado. Erro de REDE se repete para sempre.
 */
(function () {
  "use strict";

  var BANCO = "shipops";
  var LOJA = "pendentes";
  var bd = null;

  function abrir() {
    if (bd) return Promise.resolve(bd);
    return new Promise(function (ok, falha) {
      var req = indexedDB.open(BANCO, 1);
      req.onupgradeneeded = function () {
        var d = req.result;
        if (!d.objectStoreNames.contains(LOJA)) {
          d.createObjectStore(LOJA, { keyPath: "id_cliente" });
        }
      };
      req.onsuccess = function () { bd = req.result; ok(bd); };
      req.onerror = function () { falha(req.error); };
    });
  }

  function transacao(modo, acao) {
    return abrir().then(function (d) {
      return new Promise(function (ok, falha) {
        var t = d.transaction(LOJA, modo);
        var req = acao(t.objectStore(LOJA));
        t.oncomplete = function () { ok(req && req.result); };
        t.onerror = function () { falha(t.error); };
      });
    });
  }

  function todos() {
    return transacao("readonly", function (loja) { return loja.getAll(); })
      .then(function (r) { return r || []; });
  }

  function gravar(item) {
    return transacao("readwrite", function (loja) { return loja.put(item); });
  }

  function remover(id) {
    return transacao("readwrite", function (loja) { return loja.delete(id); });
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "id-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  /* ---- envio ---------------------------------------------------------- */

  var enviando = false;

  function sincronizar() {
    if (enviando) return Promise.resolve();
    enviando = true;
    return todos().then(function (itens) {
      var fila = itens.filter(function (i) { return i.estado === "pendente"; });
      return fila.reduce(function (anterior, item) {
        return anterior.then(function () { return enviarUm(item); });
      }, Promise.resolve());
    }).then(function () {
      enviando = false;
      pintar();
    }).catch(function () {
      enviando = false;
    });
  }

  function enviarUm(item) {
    return fetch("/api/marco", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item.payload)
    }).then(function (r) {
      if (r.ok) {
        return r.json().catch(function () { return {}; }).then(function (corpo) {
          return remover(item.id_cliente).then(function () {
            return { ok: true, viagemMudou: !!(corpo && corpo.viagem_mudou) };
          });
        });
      }
      if (r.status === 422 || r.status === 403 || r.status === 404) {
        // O dado esta errado. Reenviar nao conserta — para de tentar e mostra.
        return r.json().catch(function () { return {}; }).then(function (corpo) {
          item.estado = "erro";
          item.erros = (corpo && corpo.erros) || ["Lancamento recusado."];
          return gravar(item).then(function () {
            return { ok: false, erros: item.erros };
          });
        });
      }
      // 5xx ou servidor dormindo: continua pendente, tenta de novo depois.
      throw new Error("servidor indisponivel");
    });
  }

  /* ---- estado na tela -------------------------------------------------- */

  function chave(escalaId, tipo) { return String(escalaId) + "|" + tipo; }

  function pintar() {
    var alvos = document.querySelectorAll("[data-escala][data-tipo]");
    if (!alvos.length) return;
    todos().then(function (itens) {
      var mapa = {};
      itens.forEach(function (i) {
        mapa[chave(i.payload.escala_id, i.payload.tipo)] = i;
      });
      Array.prototype.forEach.call(alvos, function (el) {
        var item = mapa[chave(el.dataset.escala, el.dataset.tipo)];
        var marca = el.querySelector("[data-estado]");
        if (!marca) return;
        if (!item) { marca.textContent = ""; marca.className = ""; return; }
        if (item.estado === "erro") {
          marca.textContent = "recusado: " + (item.erros || []).join(" ");
          marca.className = "estado erro";
        } else {
          marca.textContent = "enviando...";
          marca.className = "estado pendente";
        }
      });
    });
  }

  /* ---- api usada pelas telas ------------------------------------------ */

  window.Fila = {
    enfileirar: function (payload) {
      payload.id_cliente = payload.id_cliente || uuid();
      var item = {
        id_cliente: payload.id_cliente,
        payload: payload,
        estado: "pendente",
        criado_em: new Date().toISOString()
      };
      // Grava primeiro, envia depois: se o envio falhar, o lancamento ja esta
      // salvo no aparelho e sai sozinho mais tarde. O retorno diz o que o
      // servidor respondeu, para a linha se atualizar sem recarregar a pagina.
      return gravar(item).then(function () { return enviarUm(item); });
    },
    sincronizar: sincronizar,
    pintar: pintar,
    limparErro: function (id) { return remover(id).then(pintar); }
  };

  document.addEventListener("DOMContentLoaded", function () {
    pintar();
    sincronizar();
  });
  window.addEventListener("online", sincronizar);
  // O servidor pode estar apenas acordando; tentar de novo sem alarde.
  setInterval(sincronizar, 30000);
})();
