/* O boletim do meio-dia (RDE) — formulário GFS1201 preenchido e enviado.
 *
 * Três movimentos, nenhum deles grava no banco:
 *   1. ABRIR: o modal chega pelo fragmento da tela já com o que o sistema sabe.
 *      Aqui entra o que o APARELHO lembra do boletim de ontem (calado, rancho,
 *      tripulação, horas de máquina...) nos campos que vieram vazios, e o
 *      "Total ROB" de ontem vira a "Qtd anterior" de hoje.
 *   2. GERAR: POST /navio/rde devolve o xlsx; o navegador baixa o arquivo.
 *   3. ENVIAR: o e-mail abre pronto (destinatários, assunto, texto) pelo
 *      mailto:; o comandante anexa o arquivo baixado. Nenhum navegador anexa
 *      arquivo por conta própria.
 *
 * Nada de location.reload(): a tela do comandante nunca pisca.
 */
(function () {
  "use strict";

  // O que NÃO se lembra de um dia para o outro: muda todo dia.
  const NAO_LEMBRAR = /^(data|diario_|meteo_|observacoes$)/;

  function modal() { return document.getElementById("modal-rde"); }
  function form() { return document.getElementById("form-rde"); }
  function chave() {
    const m = modal();
    return "shipops.rde." + (m ? m.dataset.navioId : "0");
  }

  function lembrado() {
    try { return JSON.parse(localStorage.getItem(chave()) || "{}") || {}; }
    catch (e) { return {}; }
  }

  function lembrar(dados) {
    const guardar = {};
    Object.keys(dados).forEach(function (nome) {
      if (NAO_LEMBRAR.test(nome)) return;
      if (dados[nome] !== "") guardar[nome] = dados[nome];
    });
    guardar._quando = new Date().toISOString().slice(0, 10);
    try { localStorage.setItem(chave(), JSON.stringify(guardar)); } catch (e) {}
  }

  function estado(classe, texto) {
    const alvo = document.querySelector("[data-estado-rde]");
    if (!alvo) return;
    alvo.className = "estado " + classe;
    alvo.textContent = texto;
  }

  /* ---- abrir: o que o aparelho lembra entra nos campos vazios ----------- */

  function abrir() {
    const f = form();
    const m = modal();
    if (!f || !m) return;
    const ontem = lembrado();

    f.querySelectorAll("input[name], textarea[name]").forEach(function (campo) {
      if (campo.type === "hidden" || campo.value !== "") return;
      if (campo.name in ontem) campo.value = ontem[campo.name];
    });
    // O ROB de ontem é a "quantidade anterior" de hoje — mesmo que o
    // sistema tenha sugerido o marco anterior.
    ["vlsfo", "mgo", "agua"].forEach(function (p) {
      const total = ontem["est_" + p + "_total"];
      const campo = f.querySelector('[name="est_' + p + '_anterior"]');
      if (campo && total !== undefined) campo.value = total;
    });
    // Comandante: quem preenche, na barra lateral.
    const cmt = f.querySelector('[name="comandante"]');
    const nome = document.getElementById("responsavel");
    if (cmt && !cmt.value && nome && nome.value.trim()) cmt.value = nome.value.trim().toUpperCase();
    // Horas de máquina: as "diárias" de hoje começam vazias; o operando de
    // ontem fica como base para somar.
    f.querySelectorAll("[data-maquina]").forEach(function (campo) {
      campo.value = "";
      campo.dataset.baseOperando = ontem["maq_" + campo.dataset.maquina + "_operando"] || "";
    });

    document.getElementById("rde-pronto").hidden = true;
    f.hidden = false;
    estado("", "");
    m.style.display = "flex";
    const data = f.querySelector('[name="data"]');
    if (data) data.focus();
  }

  function fechar() {
    const m = modal();
    if (m) m.style.display = "none";
  }

  /* ---- horas operando = ontem + hoje ------------------------------------ */

  function somarHoras(campoDiarias) {
    const base = parseFloat(campoDiarias.dataset.baseOperando || "");
    const hoje = parseFloat(campoDiarias.value || "");
    if (isNaN(base) || isNaN(hoje)) return;
    const operando = form().querySelector('[name="maq_' + campoDiarias.dataset.maquina + '_operando"]');
    if (operando) operando.value = Math.round((base + hoje) * 100) / 100;
  }

  /* ---- gerar e baixar ---------------------------------------------------- */

  function baixar(blob, nome) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
  }

  function mostrarEnvio(email) {
    const f = form();
    const pronto = document.getElementById("rde-pronto");
    document.getElementById("rde-pronto-arquivo").textContent = email.arquivo;
    document.getElementById("rde-pronto-para").textContent = email.para.join(" e ");
    document.getElementById("rde-pronto-texto").textContent = "Assunto: " + email.assunto + "\n\n" + email.corpo;
    document.getElementById("btn-rde-email").href = email.mailto;
    f.hidden = true;
    pronto.hidden = false;
    pronto.scrollIntoView({ block: "start" });
  }

  function gerar(evento) {
    evento.preventDefault();
    const f = form();
    if (!f.reportValidity()) return;
    const dados = {};
    new FormData(f).forEach(function (valor, nome) { dados[nome] = String(valor).trim(); });
    const botao = f.querySelector("button[type=submit]");
    botao.disabled = true;
    estado("pendente", "gerando o arquivo...");

    fetch("/navio/rde", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(dados)
    }).then(function (r) {
      if (r.status === 422) {
        return r.json().then(function (j) { throw new Error((j.erros || ["Dados inválidos."]).join(" ")); });
      }
      if (!r.ok) throw new Error("Não foi possível gerar o arquivo (" + r.status + ").");
      const email = JSON.parse(decodeURIComponent(r.headers.get("X-Rde-Email") || "%7B%7D"));
      return r.blob().then(function (blob) {
        baixar(blob, email.arquivo || "RDE.xlsx");
        lembrar(dados);
        mostrarEnvio(email);
        estado("", "");
      });
    }).catch(function (erro) {
      estado("erro", erro.message || "Falha ao gerar o boletim.");
    }).then(function () { botao.disabled = false; });
  }

  function copiarTexto() {
    const texto = document.getElementById("rde-pronto-texto").textContent;
    const botao = document.getElementById("btn-rde-copiar");
    const feito = function () { botao.textContent = "Texto copiado"; setTimeout(function () { botao.textContent = "Copiar o texto"; }, 2500); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(feito, function () { selecionar(); });
    } else {
      selecionar();
    }
    function selecionar() {
      const faixa = document.createRange();
      faixa.selectNodeContents(document.getElementById("rde-pronto-texto"));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(faixa);
    }
  }

  /* ---- ligações (delegadas: o modal chega pelo fragmento) --------------- */

  document.addEventListener("click", function (ev) {
    const alvo = ev.target;
    if (alvo.closest("[data-abrir-rde]")) { ev.preventDefault(); abrir(); }
    else if (alvo.closest("#btn-fechar-rde")) fechar();
    else if (alvo.closest("#btn-rde-voltar")) { document.getElementById("rde-pronto").hidden = true; form().hidden = false; }
    else if (alvo.closest("#btn-rde-copiar")) copiarTexto();
  });

  document.addEventListener("submit", function (ev) {
    if (ev.target && ev.target.id === "form-rde") gerar(ev);
  });

  document.addEventListener("input", function (ev) {
    if (ev.target && ev.target.dataset && ev.target.dataset.maquina !== undefined) somarHoras(ev.target);
  });
})();
