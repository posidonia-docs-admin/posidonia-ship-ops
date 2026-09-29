/* Lançamento no lugar — a página NUNCA recarrega no caminho do dia a dia.
 *
 * Cada lançamento passa por três movimentos:
 *   1. "Tem certeza?" — um resumo do que foi digitado, para conferir antes
 *      de gravar. Só depois de confirmar o dado sai do formulário.
 *   2. A fila local (fila.js) guarda e envia; sem conexão fica "na fila" e
 *      sai sozinho depois.
 *   3. `GET /navio/tela` traz só o miolo da tela e a caixa é trocada — a
 *      etapa avança, o roteiro e o logbook se atualizam, e a página não pisca.
 *
 * QUEM PREENCHE vive na barra lateral, uma vez só (#responsavel): é digitado
 * uma vez, lembrado no aparelho e vai em todo lançamento. Os formulários não
 * perguntam de novo.
 *
 * Só dois `location.reload()` são legítimos: sessão caída / sem viagem
 * aberta (409) e a correção em viagem encerrada (data-recarrega).
 */
(function () {
  "use strict";

  const CHAVE_RESPONSAVEL = "shipops.responsavel";
  const caixaTela = document.getElementById("tela-viagem");
  const campoNome = document.getElementById("responsavel");

  /* ---- quem preenche: a barra lateral --------------------------------- */

  if (campoNome) {
    try {
      const guardado = localStorage.getItem(CHAVE_RESPONSAVEL);
      if (guardado && !campoNome.value) campoNome.value = guardado;
    } catch (e) { /* storage bloqueado: segue sem lembrar */ }
    campoNome.addEventListener("change", function () {
      try { localStorage.setItem(CHAVE_RESPONSAVEL, campoNome.value.trim()); } catch (e) {}
    });
  }

  function quemPreenche(bloco) {
    let nome = campoNome ? campoNome.value.trim() : "";
    if (!nome) {
      // formulários antigos (encerradas) ainda podem trazer o campo
      const dentro = bloco.querySelector("[name=nome_responsavel]");
      if (dentro) nome = dentro.value.trim();
    }
    if (!nome) {
      estado(bloco, "erro", "Informe quem preenche, na barra lateral.");
      if (campoNome) { campoNome.focus(); campoNome.scrollIntoView({ block: "center" }); }
      return "";
    }
    try { localStorage.setItem(CHAVE_RESPONSAVEL, nome); } catch (e) {}
    return nome;
  }

  /* ---- data e hora de agora nos campos vazios ------------------------- */

  function agoraLocal() {
    const a = new Date();
    const p = function (n) { return String(n).padStart(2, "0"); };
    return { data: a.getFullYear() + "-" + p(a.getMonth() + 1) + "-" + p(a.getDate()),
             hora: p(a.getHours()) + ":" + p(a.getMinutes()) };
  }

  function sincronizarDataHoraLocal(forcar) {
    const agora = agoraLocal();
    const fDate = document.getElementById("f_date");
    const fTime = document.getElementById("f_time");
    if (fDate && (forcar || !fDate.value)) fDate.value = agora.data;
    if (fTime && (forcar || !fTime.value)) fTime.value = agora.hora;
  }

  function estado(bloco, classe, texto) {
    const alvo = bloco.querySelector("[data-estado]");
    if (!alvo) return;
    alvo.className = "estado " + classe;
    alvo.textContent = texto;
  }

  /* ---- a troca do miolo ----------------------------------------------- */

  let trocando = false;
  function trocarTela() {
    if (!caixaTela || trocando) return Promise.resolve();
    trocando = true;
    caixaTela.classList.add("atualizando");
    return fetch("/navio/tela", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.status === 409 || r.status === 401 || r.status === 403 || r.redirected) {
          window.location.reload();
          return null;
        }
        if (!r.ok) throw new Error("Fragmento indisponível");
        return r.text();
      }).then(function (html) {
        if (html === null) return;
        caixaTela.innerHTML = html;
        sincronizarDataHoraLocal(false);
        if (window.Fila && window.Fila.pintar) window.Fila.pintar();
      }).catch(function () {
        // Offline: o dado fica na fila e o status visual reflete "na fila"
      }).then(function () {
        trocando = false;
        caixaTela.classList.remove("atualizando");
      });
  }

  /* ---- "Tem certeza?" ------------------------------------------------- */

  function confirmar(titulo, linhas) {
    const modal = document.getElementById("modal-confirmar");
    if (!modal) return Promise.resolve(true);
    const lista = document.getElementById("confirmar-resumo");
    const cabeca = document.getElementById("confirmar-titulo");
    if (cabeca) cabeca.textContent = titulo;
    lista.innerHTML = "";
    linhas.forEach(function (par) {
      if (par[1] === null || par[1] === undefined || par[1] === "") return;
      const dt = document.createElement("dt"); dt.textContent = par[0];
      const dd = document.createElement("dd"); dd.textContent = par[1];
      lista.appendChild(dt); lista.appendChild(dd);
    });
    modal.style.display = "flex";
    return new Promise(function (resolve) {
      const sim = document.getElementById("btn-confirmar");
      const nao = document.getElementById("btn-voltar");
      function fechar(resposta) {
        modal.style.display = "none";
        sim.removeEventListener("click", ok); nao.removeEventListener("click", volta);
        resolve(resposta);
      }
      function ok() { fechar(true); }
      function volta() { fechar(false); }
      sim.addEventListener("click", ok); nao.addEventListener("click", volta);
      sim.focus();
    });
  }

  function br(iso) {
    // "2026-09-29T14:30" -> "29/09/2026 14:30"
    if (!iso || iso.length < 16) return iso || "";
    return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4) + " " + iso.slice(11, 16);
  }

  /* ---- envio ---------------------------------------------------------- */

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
      if (form.dataset.recarrega === "1") {
        window.location.reload();
        return;
      }
      return trocarTela();
    }).catch(function () {
      estado(bloco, "pendente", "na fila offline");
    });
  }

  function valor(dados, nome) {
    return (dados.get(nome) || "").toString().trim() || null;
  }

  function enviarMarco(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;
    const bloco = form.closest("[data-escala][data-tipo]") || form;
    const nome = quemPreenche(form);
    if (!nome) return;
    const dados = new FormData(form);
    const horaLocal = valor(dados, "data") + "T" + valor(dados, "hora");
    const payload = {
      escala_id: Number(dados.get("escala_id") || form.dataset.escala),
      tipo: dados.get("tipo") || form.dataset.tipo,
      hora_local: horaLocal,
      offset: dados.get("offset") || form.dataset.offset || "-03:00",
      nome_responsavel: nome,
      motivo_correcao: valor(dados, "motivo_correcao"),
      rob_vlsfo: valor(dados, "rob_vlsfo"),
      rob_mgo: valor(dados, "rob_mgo"),
      fw: valor(dados, "fw"),
      lixo: valor(dados, "lixo"),
      comentarios: valor(dados, "comentarios")
    };
    const lixoSel = form.querySelector("[name=lixo]");
    confirmar(form.dataset.titulo || (form.dataset.nome ? "Gravar " + form.dataset.nome : "Gravar lançamento"), [
      ["Parada", form.dataset.porto || ""],
      ["Marco", form.dataset.nome || payload.tipo],
      ["Data e hora", br(horaLocal)],
      ["ROB VLSFO (t)", payload.rob_vlsfo],
      ["ROB MGO (t)", payload.rob_mgo],
      ["Água doce (t)", payload.fw],
      ["Lixo", lixoSel && lixoSel.selectedIndex > 0 ? lixoSel.options[lixoSel.selectedIndex].text : payload.lixo],
      ["Comentários", payload.comentarios],
      ["Motivo da correção", payload.motivo_correcao],
      ["Quem preenche", nome]
    ]).then(function (ok) {
      if (!ok) return;
      const modal = document.getElementById("modal-edicao");
      if (modal && modal.style.display === "flex") modal.style.display = "none";
      despachar(form, bloco, payload, "/api/marco");
    });
  }

  function enviarCarga(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;
    const bloco = form.closest("[data-escala]") || form;
    const nome = quemPreenche(form);
    if (!nome) return;
    const dados = new FormData(form);
    const payload = { escala_id: Number(dados.get("escala_id")), quantidade: valor(dados, "quantidade") || "",
                      nome_responsavel: nome };
    confirmar("Gravar movimento de carga", [
      ["Parada", form.dataset.porto || ""], ["Quantidade (MT)", payload.quantidade], ["Quem preenche", nome]
    ]).then(function (ok) { if (ok) despachar(form, bloco, payload, "/api/carga"); });
  }

  function enviarAbastecimento(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;
    const bloco = form.closest("[data-escala]") || form;
    const nome = quemPreenche(form);
    if (!nome) return;
    const dados = new FormData(form);
    const payload = { escala_id: Number(dados.get("escala_id")), vlsfo: valor(dados, "vlsfo"),
                      mgo: valor(dados, "mgo"), nome_responsavel: nome };
    confirmar("Gravar abastecimento", [
      ["Parada", form.dataset.porto || ""], ["VLSFO recebido (t)", payload.vlsfo], ["MGO recebido (t)", payload.mgo],
      ["Quem preenche", nome]
    ]).then(function (ok) { if (ok) despachar(form, bloco, payload, "/api/abastecimento"); });
  }

  function enviarSof(form, evento) {
    evento.preventDefault();
    if (!form.reportValidity()) return;
    const bloco = form;
    const nome = quemPreenche(form);
    if (!nome) return;
    const dados = new FormData(form);
    const payload = { escala_id: Number(dados.get("escala_id")), nome_responsavel: nome };
    const linhas = [["Parada", form.dataset.porto || ""]];
    let url;
    if (form.classList.contains("sof-marco")) {
      payload.tipo = valor(dados, "tipo_sof");
      payload.hora_local = valor(dados, "data") + "T" + valor(dados, "hora");
      payload.offset = form.dataset.offset || "-03:00";
      payload.observacao = valor(dados, "observacao");
      const sel = form.querySelector("[name=tipo_sof]");
      linhas.push(["Marco do SOF", sel ? sel.options[sel.selectedIndex].text : payload.tipo],
                  ["Data e hora", br(payload.hora_local)], ["Observação", payload.observacao]);
      url = "/api/sof/marco";
    } else {
      payload.dados = {};
      form.querySelectorAll("[data-sof-campo]").forEach(function (campo) {
        const v = campo.value.trim();
        if (v !== "") { payload.dados[campo.name] = v; linhas.push([campo.dataset.rotulo || campo.name, v]); }
      });
      url = "/api/sof/dados";
    }
    linhas.push(["Quem preenche", nome]);
    confirmar("Gravar dados do SOF", linhas).then(function (ok) { if (ok) despachar(form, bloco, payload, url); });
  }

  /* ---- retificação (modal) ------------------------------------------- */

  window.abrirModalEdicaoMarco = function (escalaId, tipo, horaLocal, offset, vlsfo, mgo, fw, lixo, comentarios) {
    const g = function (id) { return document.getElementById(id); };
    g("edit-escala-id").value = escalaId;
    g("edit-tipo").value = tipo;
    g("edit-offset").value = offset;
    g("edit-date").value = (horaLocal || "").slice(0, 10);
    g("edit-time").value = (horaLocal || "").slice(11, 16);
    g("edit-vlsfo").value = vlsfo || "";
    g("edit-mgo").value = mgo || "";
    g("edit-fw").value = fw || "";
    g("edit-lixo").value = lixo || "";
    g("edit-comments").value = comentarios === "—" ? "" : (comentarios || "");
    g("edit-reason").value = "";
    const form = g("form-edicao");
    if (form) form.dataset.nome = "retificação de " + tipo;
    const modal = g("modal-edicao");
    if (modal) modal.style.display = "flex";
  };

  /* ---- ligações ------------------------------------------------------- */

  document.addEventListener("DOMContentLoaded", function () {
    sincronizarDataHoraLocal(false);
  });

  // Delegado no documento: vale também para o que chega pelo fragmento.
  document.addEventListener("click", function (ev) {
    const alvo = ev.target;
    if (alvo.closest("[data-editar]")) {
      const d = alvo.closest("[data-editar]").dataset;
      window.abrirModalEdicaoMarco(Number(d.escalaId), d.tipo, d.hora, d.offset,
        d.vlsfo, d.mgo, d.fw, d.lixo, d.comentarios);
    } else if (alvo.closest("#btn-sync-local-time")) {
      sincronizarDataHoraLocal(true);
    } else if (alvo.closest("#btn-fechar-modal-edicao")) {
      document.getElementById("modal-edicao").style.display = "none";
    } else if (alvo.closest("[data-usar-sugestao]")) {
      // "usar o último ROB": copia a sugestão para o campo
      const botao = alvo.closest("[data-usar-sugestao]");
      const campo = document.querySelector('[name="' + botao.dataset.usarSugestao + '"]');
      if (campo) campo.value = botao.dataset.valor || "";
    }
  });

  document.addEventListener("submit", function (evento) {
    const form = evento.target;
    if (!form.classList) return;
    if (form.classList.contains("sof-marco") || form.classList.contains("sof-dados")) enviarSof(form, evento);
    else if (form.classList.contains("lancar")) enviarMarco(form, evento);
    else if (form.classList.contains("carregar")) enviarCarga(form, evento);
    else if (form.classList.contains("abastecer")) enviarAbastecimento(form, evento);
  });
})();
