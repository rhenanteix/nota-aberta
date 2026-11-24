// Página oficial: os planos vêm de /conta/planos (planos.json no servidor), nunca escritos aqui.
"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

document.querySelectorAll(".host").forEach((el) => { el.textContent = location.host; });

const reais = (v) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

fetch("/conta/planos").then((r) => r.ok ? r.json() : Promise.reject(r.status)).then(({ planos, sob_medida: sm }) => {
  const meio = Math.min(2, planos.length - 1);
  const cartoes = planos.map((p, i) => `
    <article class="plano${i === meio ? " destaque-plano" : ""}">
      <h3>${esc(p.nome)}</h3>
      <p class="preco">${p.preco_mes ? `R$ ${reais(p.preco_mes)}<span>/mês</span>` : "R$ 0"}</p>
      <ul>${p.destaques.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
      <a class="botao${i === meio ? "" : " secundario"}" href="/painel.html">${!p.preco_mes ? "Começar grátis" : "Assinar no painel"}</a>
    </article>`);
  if (sm && sm.texto) {
    const zap = sm.whatsapp ? `https://wa.me/${encodeURIComponent(sm.whatsapp)}?text=${encodeURIComponent("Quero um plano sob medida da Emite Ai")}` : "";
    cartoes.push(`
    <article class="plano">
      <h3>${esc(sm.nome || "Sob medida")}</h3>
      <p class="preco preco-texto">Fale com a gente</p>
      <ul><li>${esc(sm.texto)}</li></ul>
      ${zap ? `<a class="botao secundario" href="${esc(zap)}" rel="noopener">WhatsApp</a>` : ""}
      ${sm.email ? `<a class="botao secundario" href="mailto:${esc(sm.email)}" title="${esc(sm.email)}">E-mail</a>` : ""}
    </article>`);
  }
  document.getElementById("planos").innerHTML = cartoes.join("");
}).catch(() => {
  document.getElementById("planos").innerHTML = '<p class="dica">Os planos não carregaram agora. Tente de novo em instantes.</p>';
});
