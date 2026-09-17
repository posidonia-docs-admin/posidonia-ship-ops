/* Lançamento Integrado Corsair com Fila Offline e Atualização Fluida */
(function () {
  "use strict";

  const CHAVE_RESPONSAVEL = "shipops.responsavel";
  const caixaTela = document.getElementById("tela-viagem");

  function sincronizarDataHoraLocal() {
    const agora = new Date();
    const ano = agora.getFullYear();
    const mes = String(agora.getMonth() + 1).padStart(2, '0');
    const dia = String(agora.getDate()).padStart(2, '0');
    const horas = String(agora.getHours()).padStart(2, '0');
    const minutos = String(agora.getMinutes()).padStart(2, '0');

    const fDate = document.getElementById("f_date");
    const fTime = document.getElementById("f_time");
    if (fDate && !fDate.value) fDate.value = `${ano}-${mes}-${dia}`;
    if (fTime && !fTime.value) fTime.value = `${horas}:${minutos}`;
  }

  function inicializarResponsavel() {
    let salvo = "";
    try {
      salvo = localStorage.getItem(CHAVE_RESPONSAVEL) || "";
    } catch (e) {}

    const campoPrincipal = document.getElementById("campo-responsavel");
    if (campoPrincipal && !campoPrincipal.value && salvo) {
      campoPrincipal.value = salvo;
    }

    document.querySelectorAll(".campo-responsavel-sec").forEach(el => {
      if (!el.value && salvo) el.value = salvo;
    });
  }

  function estado(bloco, classe, texto) {
    const alvo = bloco.querySelector("[data-estado]");
    if (!alvo) return;
    alvo.className = "estado " + classe;
    alvo.textContent = texto;
  }

  let trocando = false;
  function trocarTela() {
    if (!caixaTela || trocando) return Promise.resolve();
    trocando = true;
    caixaTela.classList.add("atualizando");

    return fetch("/navio/tela", {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" }
    }).then(function (r) {
      if (r.status === 409 || r.status === 401 || r.status === 403 || r.redirected) {
        window.location.reload();
        return null;
      }
      if (!r.ok) throw new Error("Fragmento indisponível");
      return r.text();
    }).then(function (html) {
      if (html === null) return;
      caixaTela.innerHTML = html;
      sincronizarDataHoraLocal();
      inicializarResponsavel();
      if (window.Fila && window.Fila.pintar) window.Fila.pintar();
    }).catch(function () {
      // Offline: o dado fica na fila e o status visual reflete 'na fila'
    }).then(function () {
      trocando = false;
      caixaTela.classList.remove("atualizando");
    });
  }

  function despachar(form, bloco, payload, url) {
    const botao = form.querySelector("button[type=submit]");
    if (botao) botao.disabled = true;
    estado(bloco, "pendente", "enviando...");

    return window.Fila.enfileirar(payload, url).then(function (r) {
      if (r && r.erros && r.erros.length) {
        estado(bloco, "erro", r.erros.join(" "));
        if (botao) botao.disabled = false;
        return;
      }
      // Se a viagem fechou (meta atingida) ou o form pediu recarga, recarrega a janela
      if (r && r.viagemMudou) {
        window.location.reload();
        return;
      }
      if (form.dataset.recarrega === "1") {
        window.location.reload();
        return;
      }
      return trocarTela();
    }).catch(function () {
      estado(bloco, "pendente", "na fila offline");
    });
  }

  function enviarMarco(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;

    const bloco = form.closest("[data-escala][data-tipo]") || form;
    const dados = new FormData(form);

    const responsavel = (dados.get("nome_responsavel") || "").trim();
    if (!responsavel) {
      estado(bloco, "erro", "Informe quem preenche.");
      return;
    }
    try {
      localStorage.setItem(CHAVE_RESPONSAVEL, responsavel);
    } catch (e) {}

    const data = (dados.get("data") || "").trim();
    const hora = (dados.get("hora") || "").trim();
    const escalaId = Number(dados.get("escala_id") || form.dataset.escala);
    const tipo = (dados.get("tipo") || form.dataset.tipo);
    const offset = (dados.get("offset") || form.dataset.offset || "-03:00");

    despachar(form, bloco, {
      escala_id: escalaId,
      tipo: tipo,
      hora_local: data + "T" + hora,
      offset: offset,
      nome_responsavel: responsavel,
      motivo_correcao: (dados.get("motivo_correcao") || "").trim() || null,
      rob_vlsfo: (dados.get("rob_vlsfo") || "").trim() || null,
      rob_mgo: (dados.get("rob_mgo") || "").trim() || null,
      fw: (dados.get("fw") || "").trim() || null,
      lixo: (dados.get("lixo") || "").trim() || null,
      comentarios: (dados.get("comentarios") || "").trim() || null
    }, "/api/marco");
  }

  function enviarCarga(form, evento) {
    evento.preventDefault();
    const dados = new FormData(form);
    const responsavel = (dados.get("nome_responsavel") || "").trim();
    const bloco = form.closest("[data-escala]") || form;

    despachar(form, bloco, {
      escala_id: Number(dados.get("escala_id")),
      quantidade: (dados.get("quantidade") || "").trim(),
      nome_responsavel: responsavel
    }, "/api/carga");
  }

  function enviarAbastecimento(form, evento) {
    evento.preventDefault();
    const dados = new FormData(form);
    const responsavel = (dados.get("nome_responsavel") || "").trim();
    const bloco = form.closest("[data-escala]") || form;

    despachar(form, bloco, {
      escala_id: Number(dados.get("escala_id")),
      vlsfo: (dados.get("vlsfo") || "").trim() || null,
      mgo: (dados.get("mgo") || "").trim() || null,
      nome_responsavel: responsavel
    }, "/api/abastecimento");
  }

  window.abrirModalEdicaoMarco = function (escalaId, tipo, horaLocal, offset, vlsfo, mgo, fw, lixo, comentarios, responsavel) {
    document.getElementById("edit-escala-id").value = escalaId;
    document.getElementById("edit-tipo").value = tipo;
    document.getElementById("edit-offset").value = offset;
    document.getElementById("edit-date").value = (horaLocal || "").slice(0, 10);
    document.getElementById("edit-time").value = (horaLocal || "").slice(11, 16);
    document.getElementById("edit-vlsfo").value = vlsfo || "";
    document.getElementById("edit-mgo").value = mgo || "";
    document.getElementById("edit-fw").value = fw || "";
    document.getElementById("edit-lixo").value = lixo || "";
    document.getElementById("edit-comments").value = comentarios === "—" ? "" : comentarios;
    document.getElementById("edit-responsavel").value = responsavel || "";
    document.getElementById("edit-reason").value = "";

    const modal = document.getElementById("modal-edicao");
    if (modal) modal.style.display = "flex";
  };

  document.addEventListener("DOMContentLoaded", function () {
    sincronizarDataHoraLocal();
    inicializarResponsavel();

    const btnSync = document.getElementById("btn-sync-local-time");
    if (btnSync) {
      btnSync.addEventListener("click", function () {
        const agora = new Date();
        const ano = agora.getFullYear();
        const mes = String(agora.getMonth() + 1).padStart(2, '0');
        const dia = String(agora.getDate()).padStart(2, '0');
        const horas = String(agora.getHours()).padStart(2, '0');
        const minutos = String(agora.getMinutes()).padStart(2, '0');
        document.getElementById("f_date").value = `${ano}-${mes}-${dia}`;
        document.getElementById("f_time").value = `${horas}:${minutos}`;
      });
    }

    const btnFecharModal = document.getElementById("btn-fechar-modal-edicao");
    if (btnFecharModal) {
      btnFecharModal.addEventListener("click", () => {
        document.getElementById("modal-edicao").style.display = "none";
      });
    }
  });

  document.addEventListener("submit", function (evento) {
    const form = evento.target;
    if (!form.classList) return;
    if (form.classList.contains("lancar")) {
      enviarMarco(form, evento);
      const modal = document.getElementById("modal-edicao");
      if (modal && modal.style.display === "flex") modal.style.display = "none";
    } else if (form.classList.contains("carregar")) {
      enviarCarga(form, evento);
    } else if (form.classList.contains("abastecer")) {
      enviarAbastecimento(form, evento);
    }
  });
})();

    carregarEstado();
  });
})();
