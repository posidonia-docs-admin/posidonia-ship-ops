/* Diário de Bordo Eletrônico (ELB) — Corsair Posidonia */
(function () {
  "use strict";

  const STORAGE_KEY = "SHIP_ELB_ALUMAR_CIRCUIT_V1";
  const ARCHIVE_KEY = "SHIP_ELB_ARCHIVE_HISTORY_V1";

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
  let sessionMeta = { vessel: '', operator: '', notes: '' };
  let previousHash = "0000000000000000000000000000000000000000000000000000000000000000";
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

  async function calcularSHA256(texto) {
    const buffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
    return Array.from(new Uint8Array(buffer)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  async function recalcularCadeiaHashes() {
    let prev = "0000000000000000000000000000000000000000000000000000000000000000";
    for (let i = 0; i < records.length; i++) {
      records[i].prevHash = prev;
      const clone = { ...records[i] };
      delete clone.hash;
      records[i].hash = await calcularSHA256(JSON.stringify(clone));
      prev = records[i].hash;
    }
    previousHash = prev;
  }

  function salvarEstado() {
    const payload = { targetCount, sessionMeta, previousHash, records };
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
      if (!data.sessionMeta || !data.sessionMeta.vessel) {
        setupSection.style.display = "block";
        dashboardSection.style.display = "none";
        return;
      }
      targetCount = data.targetCount || 15;
      sessionMeta = data.sessionMeta;
      previousHash = data.previousHash || previousHash;
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
    const hudRoute = document.getElementById("hud-route");
    const hudCount = document.getElementById("hud-count");
    const indicador = document.getElementById("indicador-contador");

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
        <td style="max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${r.comentarios}">${r.comentarios}</td>
        <td><span class="hash-badge" title="${r.hash}">${(r.hash || '').substring(0, 8)}…</span></td>
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

  function dispararDownloadJSON(objeto, nomeArquivo) {
    const blob = new Blob([JSON.stringify(objeto, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = nomeArquivo;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function arquivarViagemAtiva(motivo) {
    if (!records.length) return;
    const pacote = {
      dataArquivamento: new Date().toISOString(),
      motivo: motivo,
      sessionMeta: { ...sessionMeta },
      totalObservacoes: records.length,
      finalHash: previousHash,
      records: [...records]
    };

    let historico = [];
    try { historico = JSON.parse(localStorage.getItem(ARCHIVE_KEY)) || []; } catch(e) { historico = []; }
    historico.unshift(pacote);
    localStorage.setItem(ARCHIVE_KEY, JSON.stringify(historico));

    dispararDownloadJSON(pacote, `ELB_ARQUIVO_${(sessionMeta.vessel || 'Navio').replace(/[^a-zA-Z0-9]/g, '_')}_${Date.now()}.json`);
    localStorage.removeItem(STORAGE_KEY);
  }

  // Registros de Eventos
  document.addEventListener("DOMContentLoaded", function () {
    const btnSync = document.getElementById("btn-sync-local-time");
    if (btnSync) btnSync.addEventListener("click", aplicarHoraLocalNoFormulario);

    const btnPreset = document.getElementById("btn-load-preset");
    if (btnPreset) {
      btnPreset.addEventListener("click", function () {
        document.getElementById("meta-target-count").value = 15;
        document.getElementById("meta-operator").value = "Comandante Operacional";
        document.getElementById("meta-notes").value = "Alumar ➔ Fazendinha ➔ Juruti ➔ Alumar (15 Etapas Padrão)";
      });
    }

    const btnIniciar = document.getElementById("btn-iniciar-viagem");
    if (btnIniciar) {
      btnIniciar.addEventListener("click", function () {
        const vessel = document.getElementById("meta-vessel").value.trim();
        const operator = document.getElementById("meta-operator").value.trim();
        const count = parseInt(document.getElementById("meta-target-count").value, 10);

        if (!vessel || !operator) {
          alert("Embarcação e Operador são obrigatórios.");
          return;
        }

        targetCount = count > 0 ? count : 15;
        sessionMeta = {
          vessel: vessel,
          operator: operator,
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
      formRegistro.addEventListener("submit", async function (e) {
        e.preventDefault();

        const novoRegistro = {
          id: records.length + 1,
          prevHash: previousHash,
          vessel: sessionMeta.vessel,
          operator: sessionMeta.operator,
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

        novoRegistro.hash = await calcularSHA256(JSON.stringify(novoRegistro));
        previousHash = novoRegistro.hash;

        records.push(novoRegistro);
        salvarEstado();
        renderizarTabela();
        atualizarHUD();
        atualizarAssistenteEtapa();

        // Envia para o backend FastAPI e para a fila local offline (IndexedDB)
        const escalaId = Number(document.getElementById("f_escala_id").value) || 1;
        if (window.Fila && window.Fila.enfileirar) {
          window.Fila.enfileirar({
            escala_id: escalaId,
            tipo: novoRegistro.acao.toLowerCase(),
            hora_local: novoRegistro.data + "T" + novoRegistro.hora,
            offset: "-03:00",
            nome_responsavel: sessionMeta.operator,
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
      formEdicao.addEventListener("submit", async function (e) {
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

        await recalcularCadeiaHashes();
        salvarEstado();
        renderizarTabela();
        document.getElementById("modal-edicao").style.display = "none";
      });
    }

    const btnFecharEdit = document.getElementById("btn-fechar-modal-edicao");
    if (btnFecharEdit) {
      btnFecharEdit.addEventListener("click", () => document.getElementById("modal-edicao").style.display = "none");
    }

    const btnExport = document.getElementById("btn-export-json");
    if (btnExport) {
      btnExport.addEventListener("click", function () {
        if (!records.length) {
          alert("Nenhum lançamento registrado para exportar.");
          return;
        }
        dispararDownloadJSON({ sessionMeta, targetCount, records, finalHash: previousHash }, `ELB_MANUAL_${Date.now()}.json`);
      });
    }

    const btnEncerrar = document.getElementById("btn-encerrar-viagem");
    if (btnEncerrar) {
      btnEncerrar.addEventListener("click", function () {
        if (!records.length) {
          if (confirm("Nenhum dado lançado. Deseja reiniciar?")) {
            localStorage.removeItem(STORAGE_KEY);
            location.reload();
          }
          return;
        }
        if (confirm("Deseja arquivar esta viagem permanentemente, baixar o arquivo JSON e iniciar uma nova?")) {
          arquivarViagemAtiva("Encerrada Manualmente pelo Comandante");
          location.reload();
        }
      });
    }

    const btnArquivo = document.getElementById("btn-abrir-arquivo");
    if (btnArquivo) {
      btnArquivo.addEventListener("click", function () {
        let historico = [];
        try { historico = JSON.parse(localStorage.getItem(ARCHIVE_KEY)) || []; } catch(e) { historico = []; }
        const corpo = document.getElementById("corpo-tabela-arquivo");
        if (!corpo) return;
        corpo.innerHTML = "";

        if (!historico.length) {
          corpo.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--suave); padding:12px;">Nenhuma viagem arquivada ainda.</td></tr>`;
        } else {
          historico.forEach((item, idx) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td class="num">${new Date(item.dataArquivamento).toLocaleString('pt-BR')}</td>
              <td><strong>${item.sessionMeta.vessel || '—'}</strong></td>
              <td>${item.sessionMeta.operator || '—'}</td>
              <td class="num">${item.totalObservacoes}</td>
              <td><span class="hash-badge">${(item.finalHash || '').substring(0, 8)}…</span></td>
              <td><button class="btn btn-secondary" style="font-size:10px; padding:2px 6px;" onclick="baixarArquivoHistorico(${idx})">Baixar JSON</button></td>
            `;
            corpo.appendChild(tr);
          });
        }
        document.getElementById("modal-arquivo").style.display = "flex";
      });
    }

    const btnFecharArq = document.getElementById("btn-fechar-modal-arquivo");
    if (btnFecharArq) {
      btnFecharArq.addEventListener("click", () => document.getElementById("modal-arquivo").style.display = "none");
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

    window.baixarArquivoHistorico = function (index) {
      const historico = JSON.parse(localStorage.getItem(ARCHIVE_KEY)) || [];
      const item = historico[index];
      if (item) dispararDownloadJSON(item, `ELB_HISTORICO_${Date.now()}.json`);
    };

    carregarEstado();
  });
})();
