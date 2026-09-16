/* Diário de Bordo Eletrônico (ELB) — Corsair Posidonia */
(function () {
  "use strict";

  const STORAGE_KEY = "SHIP_ELB_ALUMAR_CIRCUIT_V1";

  const BASE_ROUTE_STAGES = [
    { loc: "Alumar - MA", action: "sailing", purpose: "Laden", desc: "1. Alumar — Início de Singradura p/ Fazendinha" },
    { loc: "Barra Norte", action: "arrival", purpose: "Laden", desc: "2. Barra Norte — Entrada no Canal / Espera Prático" },
    { loc: "Fazendinha", action: "arrival", purpose: "Laden", desc: "3. Fazendinha — Embarque Praticagem Macapá" },
    { loc: "Fazendinha", action: "sailing", purpose: "Laden", desc: "4. Fazendinha — Singradura Rio Amazonas p/ Juruti" },
    { loc: "Juruti", action: "arrival", purpose: "Loading", desc: "5. Juruti — Chegada na Barra / Fundeado" },
    { loc: "Juruti", action: "berth", purpose: "Loading", desc: "6. Juruti — Atracação Terminal Fluvial" },
    { loc: "Juruti", action: "Fueling", purpose: "Loading", desc: "7. Juruti — Operação de Carga / Bauxita" },
    { loc: "Juruti", action: "unberth", purpose: "Laden", desc: "8. Juruti — Término Carga & Desatracação" },
    { loc: "Juruti", action: "sailing", purpose: "Laden", desc: "9. Juruti — Singradura Descendo Rio Amazonas" },
    { loc: "Fazendinha", action: "arrival", purpose: "Laden", desc: "10. Fazendinha — Desembarque Praticagem" },
    { loc: "Fazendinha", action: "sailing", purpose: "Laden", desc: "11. Fazendinha — Saída Barra Norte Rumo Alumar" },
    { loc: "Alumar - MA", action: "arrival", purpose: "Discharging", desc: "12. Alumar — Chegada Barra São Marcos / Fundeado" },
    { loc: "Alumar - MA", action: "berth", purpose: "Discharging", desc: "13. Alumar — Atracação Terminal Alumar" },
    { loc: "Alumar - MA", action: "Standby", purpose: "Discharging", desc: "14. Alumar — Operação de Descarga Bauxita" },
    { loc: "Alumar - MA", action: "unberth", purpose: "Laden", desc: "15. Alumar — Término Descarga & Conclusão Ciclo" }
  ];

  let targetCount = 15;
  let records = [];
  let sessionMeta = { viagemId: '', comandante: '', notes: '' };
  let idCtxSelecionado = null;

  function obterDataHoraLocal() {
    const agora = new Date();
    const ano = agora.getFullYear();
    const mes = String(agora.getMonth() + 1).padStart(2, '0');
    const dia = String(agora.getDate()).padStart(2, '0');
    const horas = String(agora.getHours()).padStart(2, '0');
    const minutos = String(agora.getMinutes()).padStart(2, '0');
    return {
      data: `${ano}-${mes}-${dia}`,
      hora: `${horas}:${minutos}`
    };
  }

  function aplicarHoraLocalNoFormulario() {
    const local = obterDataHoraLocal();
    const fDate = document.getElementById("f_date");
    const fTime = document.getElementById("f_time");
    if (fDate) fDate.value = local.data;
    if (fTime) fTime.value = local.hora;
  }

  function salvarEstado() {
    const payload = { targetCount, sessionMeta, records };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }

  function carregarEstado() {
    const setupSection = document.getElementById("setup-section");
    const dashboardSection = document.getElementById("dashboard-section");
    if (!setupSection || !dashboardSection) return;

    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      setupSection.style.display = "block";
      dashboardSection.style.display = "none";
      return;
    }
    try {
      const data = JSON.parse(raw);
      if (!data.sessionMeta || !data.sessionMeta.comandante) {
        setupSection.style.display = "block";
        dashboardSection.style.display = "none";
        return;
      }
      targetCount = data.targetCount || 15;
      sessionMeta = data.sessionMeta;
      records = data.records || [];

      atualizarHUD();
      setupSection.style.display = "none";
      dashboardSection.style.display = "grid";

      renderizarTabela();
      atualizarAssistenteEtapa();
      aplicarHoraLocalNoFormulario();
    } catch (e) {
      localStorage.removeItem(STORAGE_KEY);
      setupSection.style.display = "block";
      dashboardSection.style.display = "none";
    }
  }

  function atualizarHUD() {
    const hudViagemId = document.getElementById("hud-viagem-id");
    const hudOperator = document.getElementById("hud-operator");
    const hudRoute = document.getElementById("hud-route");
    const hudCount = document.getElementById("hud-count");
    const indicador = document.getElementById("indicador-contador");

    if (hudViagemId) hudViagemId.textContent = sessionMeta.viagemId || "—";
    if (hudOperator) hudOperator.textContent = sessionMeta.comandante || "—";
    if (hudRoute) hudRoute.textContent = sessionMeta.notes ? sessionMeta.notes.slice(0, 24) : "Alumar ⇄ Juruti";
    if (hudCount) hudCount.textContent = records.length;

    if (indicador) {
      const prox = records.length + 1;
      indicador.textContent = prox <= targetCount ? `Observação ${prox} de ${targetCount}` : `Observação ${prox} (Extra além de ${targetCount})`;
    }
  }

  function atualizarAssistenteEtapa() {
    const texto = document.getElementById("texto-etapa-sugerida");
    if (!texto) return;
    const idx = records.length;
    texto.textContent = idx < BASE_ROUTE_STAGES.length ? BASE_ROUTE_STAGES[idx].desc : "Etapas padrão concluídas. Registros extras livres.";
  }

  function renderizarTabela() {
    const corpo = document.getElementById("tabela-corpo");
    if (!corpo) return;
    corpo.innerHTML = "";

    for (let i = records.length - 1; i >= 0; i--) {
      const r = records[i];
      const tr = document.createElement("tr");
      tr.dataset.id = r.id;

      const badgeRetificado = r.isEdited ? `<span class="tag-retificado" title="${r.editReason || ''}">RETIFICADO</span>` : '';

      tr.innerHTML = `
        <td><button type="button" class="btn-row-edit" data-id="${r.id}">✏️ Editar</button></td>
        <td><strong>#${r.id}</strong>${badgeRetificado}</td>
        <td class="num">${r.data.split("-").reverse().join("/")} ${r.hora}</td>
        <td>${r.local}</td>
        <td><span class="selo selo-acao">${r.acao}</span></td>
        <td>${r.proposito}</td>
        <td class="num">${r.vlsfo}</td>
        <td class="num">${r.mgo}</td>
        <td class="num">${r.fw ? r.fw + 't' : '—'}</td>
        <td>${r.lixo}</td>
        <td style="max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${r.comentarios}">${r.comentarios}</td>
      `;

      tr.querySelector(".btn-row-edit").addEventListener("click", () => abrirModalEdicao(r.id));

      tr.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        idCtxSelecionado = r.id;
        document.querySelectorAll("#tabela-logbook tr").forEach(el => el.classList.remove("linha-selecionada"));
        tr.classList.add("linha-selecionada");
        const menu = document.getElementById("menu-contexto");
        if (menu) {
          menu.style.display = "block";
          menu.style.left = `${e.pageX}px`;
          menu.style.top = `${e.pageY}px`;
        }
      });

      corpo.appendChild(tr);
    }
  }

  function abrirModalEdicao(id) {
    const item = records.find(r => r.id === id);
    if (!item) return;

    document.getElementById("edit-id").value = item.id;
    document.getElementById("modal-edicao-titulo").textContent = `✏️ Retificar Ocorrência #${item.id}`;
    document.getElementById("edit_date").value = item.data;
    document.getElementById("edit_time").value = item.hora;
    document.getElementById("edit_mc_1").value = item.agente;
    document.getElementById("edit_mc_2").value = item.local;
    document.getElementById("edit_mc_3").value = item.proposito;
    document.getElementById("edit_mc_4").value = item.acao;
    document.getElementById("edit_num_1").value = item.vlsfo;
    document.getElementById("edit_num_2").value = item.mgo;
    document.getElementById("edit_num_5").value = item.fw || '';
    document.getElementById("edit_trash").value = item.lixo === 'N/A' ? '' : item.lixo;
    document.getElementById("edit_comments").value = item.comentarios === '—' ? '' : item.comentarios;
    document.getElementById("edit_reason").value = item.editReason || '';

    const modal = document.getElementById("modal-edicao");
    if (modal) modal.style.display = "flex";
  }

  document.addEventListener("DOMContentLoaded", function () {
    const btnSync = document.getElementById("btn-sync-local-time");
    if (btnSync) btnSync.addEventListener("click", aplicarHoraLocalNoFormulario);

    const btnPreset = document.getElementById("btn-load-preset");
    if (btnPreset) {
      btnPreset.addEventListener("click", function () {
        document.getElementById("meta-target-count").value = 15;
        document.getElementById("meta-comandante").value = "Cap. Silveira";
        document.getElementById("meta-notes").value = "Alumar ➔ Fazendinha ➔ Juruti ➔ Alumar (15 Etapas Padrão)";
      });
    }

    const btnIniciar = document.getElementById("btn-iniciar-viagem");
    if (btnIniciar) {
      btnIniciar.addEventListener("click", function () {
        const comandante = document.getElementById("meta-comandante").value.trim();
        const count = parseInt(document.getElementById("meta-target-count").value, 10);
        const viagemId = document.getElementById("meta-viagem-id").value.trim();

        if (!comandante) {
          alert("O nome do Comandante é obrigatório.");
          return;
        }

        targetCount = count > 0 ? count : 15;
        sessionMeta = {
          viagemId: viagemId,
          comandante: comandante,
          notes: document.getElementById("meta-notes").value.trim() || "Alumar ⇄ Juruti"
        };

        atualizarHUD();
        document.getElementById("setup-section").style.display = "none";
        document.getElementById("dashboard-section").style.display = "grid";

        aplicarHoraLocalNoFormulario();
        salvarEstado();
        atualizarAssistenteEtapa();
      });
    }

    const btnEtapa = document.getElementById("btn-aplicar-etapa");
    if (btnEtapa) {
      btnEtapa.addEventListener("click", function () {
        const idx = records.length;
        if (idx < BASE_ROUTE_STAGES.length) {
          const item = BASE_ROUTE_STAGES[idx];
          document.getElementById("f_mc_2").value = item.loc;
          document.getElementById("f_mc_4").value = item.action;
          document.getElementById("f_mc_3").value = item.purpose;
        }
      });
    }

    const formRegistro = document.getElementById("form-registro");
    if (formRegistro) {
      formRegistro.addEventListener("submit", function (e) {
        e.preventDefault();

        const novoRegistro = {
          id: records.length + 1,
          viagemId: sessionMeta.viagemId,
          comandante: sessionMeta.comandante,
          data: document.getElementById("f_date").value,
          hora: document.getElementById("f_time").value,
          agente: document.getElementById("f_mc_1").value,
          local: document.getElementById("f_mc_2").value,
          proposito: document.getElementById("f_mc_3").value,
          acao: document.getElementById("f_mc_4").value,
          vlsfo: document.getElementById("f_num_1").value,
          mgo: document.getElementById("f_num_2").value,
          fw: document.getElementById("f_num_5").value || '',
          lixo: document.getElementById("f_trash").value || 'N/A',
          comentarios: document.getElementById("f_comments").value.trim() || '—',
          isEdited: false
        };

        records.push(novoRegistro);
        salvarEstado();
        renderizarTabela();
        atualizarHUD();
        atualizarAssistenteEtapa();

        // Envia para o banco de dados via fila IndexedDB (/api/marco)
        const escalaId = Number(document.getElementById("f_escala_id").value) || 1;
        if (window.Fila && window.Fila.enfileirar) {
          window.Fila.enfileirar({
            escala_id: escalaId,
            tipo: novoRegistro.acao.toLowerCase(),
            hora_local: novoRegistro.data + "T" + novoRegistro.hora,
            offset: "-03:00",
            nome_responsavel: sessionMeta.comandante,
            rob_vlsfo: novoRegistro.vlsfo || null,
            rob_mgo: novoRegistro.mgo || null,
            fw: novoRegistro.fw || null,
            lixo: novoRegistro.lixo || null,
            comentarios: novoRegistro.comentarios || null
          }, "/api/marco");
        }

        document.getElementById("f_num_1").value = "";
        document.getElementById("f_num_2").value = "";
        document.getElementById("f_num_5").value = "";
        document.getElementById("f_trash").value = "";
        document.getElementById("f_comments").value = "";
        aplicarHoraLocalNoFormulario();
      });
    }

    const formEdicao = document.getElementById("form-edicao");
    if (formEdicao) {
      formEdicao.addEventListener("submit", function (e) {
        e.preventDefault();
        const idAlvo = parseInt(document.getElementById("edit-id").value, 10);
        const idx = records.findIndex(r => r.id === idAlvo);
        if (idx === -1) return;

        records[idx].data = document.getElementById("edit_date").value;
        records[idx].hora = document.getElementById("edit_time").value;
        records[idx].agente = document.getElementById("edit_mc_1").value;
        records[idx].local = document.getElementById("edit_mc_2").value;
        records[idx].proposito = document.getElementById("edit_mc_3").value;
        records[idx].acao = document.getElementById("edit_mc_4").value;
        records[idx].vlsfo = document.getElementById("edit_num_1").value;
        records[idx].mgo = document.getElementById("edit_num_2").value;
        records[idx].fw = document.getElementById("edit_num_5").value;
        records[idx].lixo = document.getElementById("edit_trash").value || 'N/A';
        records[idx].comentarios = document.getElementById("edit_comments").value.trim() || '—';
        records[idx].isEdited = true;
        records[idx].editReason = document.getElementById("edit_reason").value.trim();

        // Envia a retificação para o banco gerando versão auditável
        const escalaId = Number(document.getElementById("f_escala_id").value) || 1;
        if (window.Fila && window.Fila.enfileirar) {
          window.Fila.enfileirar({
            escala_id: escalaId,
            tipo: records[idx].acao.toLowerCase(),
            hora_local: records[idx].data + "T" + records[idx].hora,
            offset: "-03:00",
            nome_responsavel: sessionMeta.comandante,
            motivo_correcao: records[idx].editReason,
            rob_vlsfo: records[idx].vlsfo || null,
            rob_mgo: records[idx].mgo || null,
            fw: records[idx].fw || null,
            lixo: records[idx].lixo || null,
            comentarios: records[idx].comentarios || null
          }, "/api/marco");
        }

        salvarEstado();
        renderizarTabela();
        document.getElementById("modal-edicao").style.display = "none";
      });
    }

    const btnFecharEdit = document.getElementById("btn-fechar-modal-edicao");
    if (btnFecharEdit) {
      btnFecharEdit.addEventListener("click", () => document.getElementById("modal-edicao").style.display = "none");
    }

    // Encerrar Viagem: Limpa sessão local e redireciona direto para a aba de Viagens Encerradas
    const btnEncerrar = document.getElementById("btn-encerrar-viagem");
    if (btnEncerrar) {
      btnEncerrar.addEventListener("click", function () {
        if (confirm("Deseja encerrar esta viagem e consultar o histórico em Viagens Encerradas?")) {
          localStorage.removeItem(STORAGE_KEY);
          window.location.href = "/navio/encerradas";
        }
      });
    }

    window.addEventListener("click", function () {
      const menu = document.getElementById("menu-contexto");
      if (menu) menu.style.display = "none";
      document.querySelectorAll("#tabela-logbook tr").forEach(el => el.classList.remove("linha-selecionada"));
    });

    const ctxEdit = document.getElementById("ctx-item-editar");
    if (ctxEdit) {
      ctxEdit.addEventListener("click", function () {
        if (idCtxSelecionado !== null) abrirModalEdicao(idCtxSelecionado);
      });
    }

    carregarEstado();
  });
})();
