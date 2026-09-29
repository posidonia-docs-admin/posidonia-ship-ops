/* O pirata dançarino.
 *
 * A cada clique em qualquer coisa, uma chance em dez mil (data-pirata-uma-em
 * no <body>) de um pirata pequenino aparecer onde está o cursor, dançando.
 * Clicar nele faz o pirata acenar e sumir. Não guarda nada, não fala com o
 * servidor, não atrapalha o clique original: é só um pirata.
 */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var umaEm = Number((document.body && document.body.dataset.pirataUmaEm) || 10000);
  var vivo = null;

  function el(nome, atributos, pai) {
    var e = document.createElementNS(NS, nome);
    Object.keys(atributos).forEach(function (k) { e.setAttribute(k, atributos[k]); });
    if (pai) pai.appendChild(e);
    return e;
  }

  // Um pirata de 48px: chapéu, tapa-olho, casaco, perna de pau e um braço
  // que acena. Desenhado em SVG para escalar sem pixelar e recolorir por CSS.
  function desenhar() {
    var svg = el("svg", { viewBox: "0 0 48 48", width: 48, height: 48, "class": "pirata-svg", role: "img", "aria-label": "pirata" });
    el("ellipse", { cx: 24, cy: 46, rx: 12, ry: 2, "class": "pirata-sombra" }, svg);
    el("rect", { x: 20, y: 34, width: 4, height: 10, rx: 1, "class": "pirata-perna" }, svg);
    el("rect", { x: 26, y: 34, width: 3, height: 10, rx: 1, "class": "pirata-pau" }, svg);
    el("path", { d: "M16 20h16v15H16z", "class": "pirata-casaco" }, svg);
    el("path", { d: "M22 20h4v15h-4z", "class": "pirata-camisa" }, svg);
    el("circle", { cx: 24, cy: 13, r: 7, "class": "pirata-pele" }, svg);
    el("path", { d: "M14 10h20l-3-6H17z", "class": "pirata-chapeu" }, svg);
    el("rect", { x: 13, y: 9, width: 22, height: 3, rx: 1, "class": "pirata-chapeu" }, svg);
    el("circle", { cx: 21, cy: 13, r: 2.2, "class": "pirata-tapa" }, svg);
    el("path", { d: "M18 11l7 1", "class": "pirata-tira" }, svg);
    el("circle", { cx: 27.5, cy: 13, r: 1.1, "class": "pirata-olho" }, svg);
    el("path", { d: "M21 17q3 2.5 6 0", "class": "pirata-boca" }, svg);
    el("path", { d: "M16 22l-5 8", "class": "pirata-braco" }, svg);
    var braco = el("g", { "class": "pirata-braco-aceno" }, svg);
    el("path", { d: "M32 22l6-8", "class": "pirata-braco" }, braco);
    el("circle", { cx: 38.5, cy: 13.5, r: 2, "class": "pirata-pele" }, braco);
    return svg;
  }

  function aparecer(x, y) {
    if (vivo) return;
    var caixa = document.createElement("div");
    caixa.className = "pirata";
    caixa.title = "Arrr! Clique em mim.";
    caixa.style.left = (x - 24) + "px";
    caixa.style.top = (y - 52) + "px";
    caixa.appendChild(desenhar());
    document.body.appendChild(caixa);
    vivo = caixa;

    caixa.addEventListener("click", function (ev) {
      ev.stopPropagation();
      caixa.classList.remove("dancando");
      caixa.classList.add("acenando");
      setTimeout(function () {
        caixa.classList.add("sumindo");
        setTimeout(function () { caixa.remove(); vivo = null; }, 700);
      }, 1500);
    }, { once: true });

    // Se ninguém der bola, ele vai embora sozinho depois de um tempo.
    setTimeout(function () {
      if (vivo === caixa && !caixa.classList.contains("acenando")) {
        caixa.classList.add("sumindo");
        setTimeout(function () { caixa.remove(); if (vivo === caixa) vivo = null; }, 700);
      }
    }, 20000);
    requestAnimationFrame(function () { caixa.classList.add("dancando"); });
  }

  document.addEventListener("click", function (ev) {
    if (vivo || ev.target.closest(".pirata")) return;
    if (Math.random() * umaEm >= 1) return;
    aparecer(ev.clientX + window.scrollX, ev.clientY + window.scrollY);
  }, true);

  // Para testar sem esperar dez mil cliques: no console, window.Pirata.aparecer().
  window.Pirata = { aparecer: function () { aparecer(window.innerWidth / 2, window.innerHeight / 2); } };
})();
