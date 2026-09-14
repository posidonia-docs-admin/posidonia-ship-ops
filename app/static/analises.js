/* Análises: o construtor envia o formulário sozinho quando um seletor muda.
 *
 * O formulário é GET e funciona sem isto (há um botão "Aplicar" para quem
 * está sem JavaScript). Aqui só se poupa o clique. Os seletores "+ campo" e
 * "+ filtro" viram o parâmetro certo antes de enviar: "nova_l=navio" vira
 * "l=navio", "novo_filtro=ano" vira "f_ano=" (o filtro nasce vazio, com todos
 * os valores à mostra para ligar).
 */
(function () {
  "use strict";
  var form = document.querySelector("form.construtor[data-auto]");
  if (!form) return;

  form.addEventListener("change", function (ev) {
    var sel = ev.target;
    if (!sel.matches("select[data-envia]")) return;
    var valor = sel.value;
    if (sel.name === "nova_l" || sel.name === "nova_c" || sel.name === "novo_filtro") {
      if (!valor) return;
      var oculto = document.createElement("input");
      oculto.type = "hidden";
      oculto.name = sel.name === "nova_l" ? "l" : sel.name === "nova_c" ? "c" : "f_" + valor;
      oculto.value = sel.name === "novo_filtro" ? "" : valor;
      form.appendChild(oculto);
      sel.disabled = true;               // o próprio "nova_*" não vai no endereço
    }
    form.submit();
  });
})();
