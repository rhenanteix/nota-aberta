// Painel da conta. Toda escrita leva o cabeçalho X-Nota (o servidor recusa sem ele: CSRF).
// O certificado vai em base64 direto para /conta/empresas/<id>/certificado e não fica em nenhum lugar do navegador.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => el.querySelectorAll(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const reais = (v) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const ESTADOS = {
  emitida: "Emitida",
  recusada: "Recusada",
  desconhecida: "Aguardando o governo",
  pendente: "Pendente",
  cancelada: "Cancelada"
};

const ESTADO_CLASSE = {
  emitida: "e-CALCULO_SIMULADO",
  recusada: "e-FALHA_TECNICA",
  desconhecida: "e-CALCULO_SIMULADO",
  pendente: "e-CALCULO_SIMULADO",
  cancelada: "e-CANCELADA"
};

async function api(metodo, caminho, corpo) {
  const r = await fetch(caminho, {
    method: metodo,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Nota": "1" },
    body: corpo ? JSON.stringify(corpo) : undefined
  });
  const dados = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 202) throw Object.assign(new Error(dados?.erro?.mensagem || `erro ${r.status}`), { status: r.status });
  return dados;
}

let EU = null;
let PLANOS = [];
let SECTION_ATUAL = "visao";

function showSection(id) {
  $$(".app-section").forEach(s => s.hidden = true);
  $$(".sidebar-item").forEach(i => i.classList.remove("ativo"));
  const sec = $("#sec-" + id);
  const item = $(`.sidebar-item[data-section="${id}"]`);
  if (sec) sec.hidden = false;
  if (item) item.classList.add("ativo");
  SECTION_ATUAL = id;

  if (id === "notas" && EU) carregarNotasFiltradas();
  if (id === "financeiro" && EU) carregarFinanceiro();
  if (id === "api" && EU) carregarChaves();
  if (id === "config" && EU) carregarConfig();
}

function initSidebar() {
  const toggle = $("#sidebar-toggle");
  const sidebar = $("#sidebar");
  const main = $(".main-content");

  toggle?.addEventListener("click", () => {
    const aberto = sidebar.classList.toggle("fechado");
    toggle.setAttribute("aria-expanded", !aberto);
    main.style.marginLeft = aberto ? "0" : "";
  });

  $$(".sidebar-item").forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const section = item.dataset.section;
      if (section) {
        showSection(section);
        if (window.innerWidth < 720) {
          sidebar.classList.add("fechado");
          toggle.setAttribute("aria-expanded", "false");
        }
      }
    });
  });

  if (location.hash) {
    const hash = location.hash.slice(1);
    const validSections = ["visao", "emitir", "empresas", "notas", "financeiro", "api", "config"];
    if (validSections.includes(hash)) showSection(hash);
  }
}

function atualizarUsuario(eu) {
  const avatar = $("#usuario-avatar");
  const email = $("#usuario-email");
  if (eu?.usuario?.email) {
    email.textContent = eu.usuario.email;
    email.hidden = false;
    if (avatar) {
      const inicial = eu.usuario.email[0].toUpperCase();
      avatar.textContent = inicial;
      avatar.hidden = false;
    }
  }
}

function renderPlano(eu, planos) {
  const p = eu.plano;
  const usadas = eu.empresas.reduce((s, e) => s + e.notas_no_mes, 0);
  const outros = planos.filter((x) => x.preco_mes > p.preco_mes);

  $("#plano").innerHTML = `<h2>Plano ${esc(p.nome)}</h2>
    <p>${usadas} de ${p.notas_mes} notas usadas neste mês · até ${p.empresas} empresa(s)</p>
    <div class="acoes">${outros.map((o) => `
      <div class="plano-opcoes">
        <span>${esc(o.nome)}: R$ ${reais(o.preco_mes)}/mês</span>
        <button type="button" class="botao" data-plano="${esc(o.id)}" data-metodo="PIX">Assinar com PIX</button>
        <button type="button" class="secundario" data-plano="${esc(o.id)}" data-metodo="BOLETO">Assinar com Boleto</button>
      </div>`).join("")}
    ${outros.length ? '<button type="button" class="secundario" id="ja-assinei">Já assinei: atualizar</button>' : ""}</div>
    <p class="dica">${outros.length ? "Pague com PIX (instantâneo) ou Boleto (até 3 dias úteis). O plano libera automaticamente após a confirmação do pagamento." : "Você já está no plano mais alto."}</p>`;

  document.querySelectorAll("#plano [data-plano]").forEach((b) => {
    b.onclick = async () => {
      const planoId = b.dataset.plano;
      const metodo = b.dataset.metodo;
      const saida = $("#plano");
      try {
        const r = await api("POST", "/conta/planos/assinar", { plano_id: planoId, metodo: metodo });
        const cob = r.cobranca;
        let html = `<div class="cobranca"><h3>Pagamento via ${metodo}</h3>`;
        if (metodo === "PIX") {
          html += `<p>Escaneie o QR Code ou copie o código:</p>
            <img src="${esc(cob.brCodeBase64)}" alt="QR Code PIX" style="max-width:200px" />
            <p><code>${esc(cob.brCode)}</code></p>
            <button type="button" class="secundario" onclick="navigator.clipboard.writeText('${esc(cob.brCode)}')">Copiar código</button>`;
        } else {
          html += `<p>Boleto gerado. Pague até ${esc(cob.expiresAt ? cob.expiresAt.slice(0,10) : "")}.</p>
            <p><a href="${esc(cob.url)}" target="_blank" rel="noopener">Abrir boleto (PDF)</a></p>
            <p>Código de barras: <code>${esc(cob.barCode)}</code></p>
            ${cob.brCodeBase64 ? `<img src="${esc(cob.brCodeBase64)}" alt="PIX do boleto" style="max-width:150px" />` : ""}`;
        }
        html += `<p class="dica">Após o pagamento, clique em "Já assinei: atualizar" para ativar o plano.</p></div>`;
        saida.innerHTML = html;
      } catch (err) {
        saida.querySelector("p.dica")?.remove();
        const p = document.createElement("p");
        p.className = "erro";
        p.textContent = err.message;
        saida.appendChild(p);
      }
    };
  });

  const b = $("#ja-assinei");
  if (b) b.onclick = async () => { await api("POST", "/conta/plano/atualizar"); carregar(); };
}

function renderKPIs(eu) {
  const usadas = eu.empresas.reduce((s, e) => s + e.notas_no_mes, 0);
  const valorTotal = eu.empresas.reduce((s, e) => s + (e.valor_mes || 0), 0);
  $("#kpi-empresas").textContent = eu.empresas.filter(e => e.certificado).length;
  $("#kpi-notas").textContent = usadas;
  $("#kpi-valor").textContent = brl(valorTotal);
  $("#kpi-plano").textContent = eu.plano.nome;
  $("#badge-empresas").textContent = eu.empresas.length;
  $("#badge-empresas").hidden = eu.empresas.length === 0;
  $("#badge-plano").textContent = eu.plano.nome;
  $("#badge-plano").hidden = false;
}

function renderProximosPassos(eu) {
  const passos = [];
  if (!eu.empresas.length) {
    passos.push({ titulo: "Adicione sua primeira empresa", acao: "Ir para Empresas", section: "empresas" });
  } else if (!eu.empresas.some(e => e.certificado)) {
    passos.push({ titulo: "Suba o certificado A1 de uma empresa", acao: "Ver empresas", section: "empresas" });
  } else {
    passos.push({ titulo: "Emita sua primeira nota", acao: "Emitir nota", section: "emitir" });
  }
  if (eu.plano.id === "gratis" && usadas >= eu.plano.notas_mes * 0.8) {
    passos.push({ titulo: "Quase no limite do plano grátis", acao: "Ver planos", section: "financeiro" });
  }
  if (!eu.chaves.length) {
    passos.push({ titulo: "Crie uma chave de API para integrar", acao: "Ver API", section: "api" });
  }

  const html = passos.map(p => `
    <div class="passo-item" style="display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--borda)">
      <div>
        <strong>${esc(p.titulo)}</strong>
      </div>
      <button type="button" class="botao secundario mini" data-go="${p.section}">${esc(p.acao)}</button>
    </div>
  `).join("") || '<p class="dica">Tudo em ordem! 🎉</p>';

  $("#proximos-passos-lista").innerHTML = html;
  $$("#proximos-passos-lista [data-go]").forEach(b => {
    b.onclick = () => showSection(b.dataset.go);
  });
}

function renderUltimasNotas(eu) {
  const todas = [];
  for (const emp of eu.empresas) {
    // We'll load from API when needed, show placeholder for now
  }
  $("#ultimas-notas-lista").innerHTML = '<p class="dica">Selecione uma empresa na aba "Notas emitidas" para ver o histórico.</p>';
}

function empresas(eu) {
  const caixa = $("#empresas-lista");
  caixa.innerHTML = eu.empresas.length ? "" : '<p class="dica">Nenhuma empresa ainda. Adicione abaixo.</p>';

  for (const e of eu.empresas) {
    const el = $("#tpl-empresa").content.firstElementChild.cloneNode(true);
    $(".e-nome", el).textContent = `${e.razao} · ${e.cnpj}`;
    const c = e.certificado;
    const status = c ? `Certificado de ${c.titular}, válido até ${c.valido_ate.split("-").reverse().join("/")}. ${e.notas_no_mes} nota(s) no mês.`
      : "Sem certificado: suba o A1 para emitir.";
    $(".e-info", el).textContent = status;
    $(".e-status", el).textContent = c ? "Ativa" : "Sem certificado";
    $(".e-status", el).className = "estado " + (c ? "e-CALCULO_SIMULADO" : "e-FALHA_TECNICA");
    $(".apagar-cert", el).hidden = !c;
    $(".apagar-cert", el).onclick = async () => {
      if (!confirm("Apagar o certificado desta empresa? Ele some do cofre e as notas param até subir outro.")) return;
      await api("DELETE", `/conta/empresas/${e.id}/certificado`); carregar();
    };
    $(".f-cert", el).onsubmit = async (ev) => {
      ev.preventDefault();
      const f = ev.target, saida = $(".r-cert", el);
      const arquivo = f.pfx.files[0];
      if (!arquivo || arquivo.size > 64 * 1024) { saida.textContent = "Escolha o arquivo .pfx (até 64 KB)."; return; }
      const bytes = new Uint8Array(await arquivo.arrayBuffer());
      let bin = "";
      for (let i = 0; i < bytes.length; i += 8192) bin += String.fromCharCode(...bytes.subarray(i, i + 8192));
      const b64 = btoa(bin);
      try {
        const r = await api("POST", `/conta/empresas/${e.id}/certificado`, { pfx_base64: b64, senha: f.senha.value });
        f.reset(); saida.textContent = `Guardado, válido até ${r.valido_ate.split("-").reverse().join("/")}.`; carregar();
      } catch (err) { saida.textContent = err.message; }
    };
    caixa.appendChild(el);
  }

  const sel = $("#f-emitir").empresa, atual = sel.value;
  sel.innerHTML = eu.empresas.map((e) => `<option value="${esc(e.id)}">${esc(e.razao)}</option>`).join("");
  if (atual) sel.value = atual;

  const filtroSel = $("#filtro-empresa");
  filtroSel.innerHTML = '<option value="">Todas as empresas</option>' +
    eu.empresas.map((e) => `<option value="${esc(e.id)}">${esc(e.razao)}</option>`).join("");

  if (sel.value) notas(sel.value);
}

async function notas(eid) {
  const { notas: lista } = await api("GET", `/conta/empresas/${eid}/notas`);
  renderNotasLista(lista, eid);
}

function renderNotasLista(lista, eid) {
  const filtroStatus = $("#filtro-status").value;
  const filtroPeriodo = $("#filtro-periodo").value;

  let filtradas = lista;
  if (filtroStatus) filtradas = filtradas.filter(n => n.status === filtroStatus);
  if (filtroPeriodo) {
    const agora = new Date();
    let inicio;
    if (filtroPeriodo === "30") inicio = new Date(agora.getTime() - 30*24*60*60*1000);
    else if (filtroPeriodo === "90") inicio = new Date(agora.getTime() - 90*24*60*60*1000);
    else if (filtroPeriodo === "ano") inicio = new Date(agora.getFullYear(), 0, 1);
    else inicio = new Date(agora.getFullYear(), agora.getMonth(), 1);
    filtradas = filtradas.filter(n => new Date(n.criada) >= inicio);
  }

  $("#notas-lista").innerHTML = filtradas.length ? `<table><thead><tr><th>Quando</th><th>Tomador</th><th class="num">Valor</th><th>Situação</th></tr></thead><tbody>
    ${filtradas.map((n) => `<tr><td>${esc(n.criada.slice(0, 16).replace("T", " "))}</td><td>${esc(n.tomador || "—")}</td><td class="num">${brl(n.valor)}</td>
      <td>${n.producao ? "" : '<span class="etiqueta">teste</span> '}<span class="estado ${ESTADO_CLASSE[n.status] || ""}">${esc(ESTADOS[n.status] || n.status)}${n.numero_nfse ? ` nº ${esc(n.numero_nfse)}` : ""}${n.status === "emitida" ? ` · <a href="/conta/empresas/${esc(eid)}/notas/${esc(n.id)}/pdf" target="_blank" rel="noopener">PDF</a> · <button type="button" class="secundario mini cancelar-nota" data-n="${esc(n.id)}" data-num="${esc(n.numero_nfse || "")}">Cancelar</button>` : ""}${["emitida", "recusada", "cancelada"].includes(n.status) ? ` · <button type="button" class="secundario mini repetir-nota" data-n="${esc(n.id)}">Repetir</button>` : ""}${n.erros.length ? `<br><span class="erro-curto">${esc(n.erros.map((x) => `${x.codigo} ${x.mensagem}${x.dica ? ` → ${x.dica}` : ""}`).join("; "))}</span>` : ""}</td></tr>`).join("")}
    </tbody></table>` : '<p class="dica">Nenhuma nota com esses filtros.</p>';

  document.querySelectorAll("#notas-lista .cancelar-nota").forEach((b) => { b.onclick = () => cancelar(eid, b.dataset.n, b.dataset.num); });
  document.querySelectorAll("#notas-lista .repetir-nota").forEach((b) => { b.onclick = () => repetir(lista.find((x) => x.id === b.dataset.n)); });
  $("#badge-notas").textContent = filtradas.length;
  $("#badge-notas").hidden = filtradas.length === 0;
}

async function carregarNotasFiltradas() {
  const eid = $("#filtro-empresa").value;
  if (!eid) {
    $("#notas-lista").innerHTML = '<p class="dica">Selecione uma empresa no filtro acima.</p>';
    return;
  }
  await notas(eid);
}

function repetir(n) {
  const f = $("#f-emitir"), s = n.servico || {}, t = s.tomador || {};
  f.codigo_servico.value = s.codigo_servico || ""; f.descricao.value = s.descricao || ""; f.nbs.value = s.nbs || "";
  f.valor.value = Number(n.valor).toFixed(2).replace(".", ",");
  f.doc.value = t.documento || ""; f.nome.value = t.nome || ""; f.email.value = t.email || "";
  for (const k of CAMPOS_END) f[k].value = t[k] || "";
  f.cidade.value = t.municipio_ibge ? `IBGE ${t.municipio_ibge}` : "";
  $("#end-tomador").open = Boolean(t.cep);
  f.ambiente.value = n.producao ? "producao" : "teste"; marcarAmbiente();
  const d = $("#dica-repetir"); d.hidden = false;
  d.textContent = `Copiado da nota de ${n.criada.slice(0, 10).split("-").reverse().join("/")}. Confira a competência antes de emitir.`;
  f.scrollIntoView({ behavior: "smooth", block: "start" }); f.competencia.focus();
  showSection("emitir");
}

const CAMPOS_END = ["cep", "logradouro", "numero", "complemento", "bairro", "municipio_ibge"];
function enderecoDoForm(f) {
  const end = Object.fromEntries(CAMPOS_END.map((k) => [k, f[k].value.trim()]).filter(([, v]) => v));
  if (end.cep) end.cep = end.cep.replace(/\D/g, "");
  return end;
}
async function buscarCep(f) {
  const cep = f.cep.value.replace(/\D/g, ""), saida = $("#r-cep");
  if (cep.length !== 8) return;
  saida.textContent = "Buscando o CEP…";
  try {
    const d = await (await fetch(`https://viacep.com.br/ws/${cep}/json/`)).json();
    if (d.erro) { saida.textContent = "CEP não encontrado: preencha à mão."; return; }
    f.logradouro.value ||= d.logradouro || ""; f.bairro.value ||= d.bairro || "";
    f.municipio_ibge.value = d.ibge || ""; f.cidade.value = d.localidade ? `${d.localidade}/${d.uf}` : "";
    saida.textContent = "Endereço preenchido pelo CEP. Confira e coloque o número.";
    f.numero.focus();
  } catch { saida.textContent = "Não consegui consultar o CEP agora."; }
}

async function cancelar(eid, nid, numero) {
  const codigo = prompt("Motivo do cancelamento:\n1 = erro na emissão\n2 = serviço não prestado\n9 = outro", "1");
  if (codigo === null) return;
  if (!["1", "2", "9"].includes(codigo.trim())) { alert("Digite 1, 2 ou 9."); return; }
  const motivo = prompt("Descreva o motivo (15 caracteres ou mais):", "");
  if (motivo === null) return;
  if (motivo.trim().length < 15) { alert("O motivo precisa ter 15 caracteres ou mais."); return; }
  if (!confirm(`Cancelar a NFS-e${numero ? ` nº ${numero}` : ""}? O cancelamento vai para o governo e não tem volta.`)) return;
  try {
    await api("POST", `/conta/empresas/${eid}/notas/${nid}/cancelar`, { motivo_codigo: codigo.trim(), motivo: motivo.trim() });
    alert("Nota cancelada no governo.");
  } catch (err) { alert(`Não cancelou: ${err.message}`); }
  if (SECTION_ATUAL === "notas") carregarNotasFiltradas();
  else carregar();
}

function chaves(eu) {
  $("#chaves-lista").innerHTML = eu.chaves.length ? `<ul class="lista">${eu.chaves.map((c) => `<li><code>${esc(c.prefixo)}…</code> ${esc(c.nome)} <button type="button" class="secundario mini" data-p="${esc(c.prefixo)}">Revogar</button></li>`).join("")}</ul>` : '<p class="dica">Nenhuma chave criada ainda.</p>';
  document.querySelectorAll("#chaves-lista [data-p]").forEach((b) => { b.onclick = async () => { await api("DELETE", `/conta/chaves/${b.dataset.p}`); carregar(); }; });

  const mcpUrl = `mcp://emiteai.com.br/{sua-chave}`;
  $("#mcp-url").textContent = mcpUrl;
  $("#copiar-mcp").onclick = () => {
    navigator.clipboard.writeText(mcpUrl);
    const btn = $("#copiar-mcp");
    const original = btn.textContent;
    btn.textContent = "Copiado!";
    setTimeout(() => btn.textContent = original, 1500);
  };
}

async function carregarFinanceiro() {
  if (!EU) return;
  const { planos, faturas, pagamentos } = await api("GET", "/conta/financeiro").catch(() => ({ planos: PLANOS, faturas: [], pagamentos: [] }));

  const p = EU.plano;
  const usadas = EU.empresas.reduce((s, e) => s + e.notas_no_mes, 0);
  $("#plano-detalhes").innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:20px">
      <div style="background:#fbfaff;padding:16px;border-radius:12px;border:1px solid var(--borda)">
        <span style="font-size:11px;color:var(--suave);text-transform:uppercase;letter-spacing:.5px">Plano atual</span>
        <h3 style="margin:8px 0 0;font-size:22px">${esc(p.nome)}</h3>
      </div>
      <div style="background:#fbfaff;padding:16px;border-radius:12px;border:1px solid var(--borda)">
        <span style="font-size:11px;color:var(--suave);text-transform:uppercase;letter-spacing:.5px">Notas usadas</span>
        <h3 style="margin:8px 0 0;font-size:22px">${usadas} / ${p.notas_mes}</h3>
      </div>
      <div style="background:#fbfaff;padding:16px;border-radius:12px;border:1px solid var(--borda)">
        <span style="font-size:11px;color:var(--suave);text-transform:uppercase;letter-spacing:.5px">Empresas</span>
        <h3 style="margin:8px 0 0;font-size:22px">${EU.empresas.length} / ${p.empresas}</h3>
      </div>
      <div style="background:#fbfaff;padding:16px;border-radius:12px;border:1px solid var(--borda)">
        <span style="font-size:11px;color:var(--suave);text-transform:uppercase;letter-spacing:.5px">Valor/mês</span>
        <h3 style="margin:8px 0 0;font-size:22px">${p.preco_mes ? `R$ ${reais(p.preco_mes)}` : "Grátis"}</h3>
      </div>
    </div>
    <div class="acoes">
      ${p.id !== "gratis" ? `<button type="button" class="secundario" id="cancelar-plano">Cancelar assinatura</button>` : ""}
      <a href="#planos-disponiveis" class="botao secundario" onclick="showSection('financeiro');setTimeout(()=>document.getElementById('planos-disponiveis').scrollIntoView({behavior:'smooth'}),100)">Ver todos os planos</a>
    </div>
  `;

  const outros = planos.filter((x) => x.preco_mes > p.preco_mes || (x.preco_mes === 0 && p.id !== "gratis"));
  $("#planos-painel").innerHTML = outros.map((o, i) => `
    <article class="plano${o.id === p.id ? " destaque-plano" : ""}" style="${o.id === p.id ? "border:2px solid var(--marca);background:#f4f2ff" : ""}">
      <h3>${esc(o.nome)}</h3>
      <p class="preco">${o.preco_mes ? `R$ ${reais(o.preco_mes)}<span>/mês</span>` : "R$ 0"}</p>
      <ul>${o.destaques.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
      <div class="acoes" style="margin-top:auto">
        ${o.id !== p.id ? `
          <button type="button" class="botao" data-plano="${esc(o.id)}" data-metodo="PIX" style="flex:1">Assinar com PIX</button>
          <button type="button" class="secundario" data-plano="${esc(o.id)}" data-metodo="BOLETO" style="flex:1">Assinar com Boleto</button>
        ` : `<span class="botao" style="background:#e7e5f2;color:var(--suave);cursor:default">Plano atual</span>`}
      </div>
    </article>
  `).join("");

  document.querySelectorAll("#planos-painel [data-plano]").forEach((b) => {
    b.onclick = async () => {
      const planoId = b.dataset.plano;
      const metodo = b.dataset.metodo;
      const saida = $("#planos-painel");
      try {
        const r = await api("POST", "/conta/planos/assinar", { plano_id: planoId, metodo: metodo });
        const cob = r.cobranca;
        let html = `<div class="cobranca" style="grid-column:1/-1"><h3>Pagamento via ${metodo}</h3>`;
        if (metodo === "PIX") {
          html += `<p>Escaneie o QR Code ou copie o código:</p>
            <img src="${esc(cob.brCodeBase64)}" alt="QR Code PIX" style="max-width:200px" />
            <p><code>${esc(cob.brCode)}</code></p>
            <button type="button" class="secundario" onclick="navigator.clipboard.writeText('${esc(cob.brCode)}')">Copiar código</button>`;
        } else {
          html += `<p>Boleto gerado. Pague até ${esc(cob.expiresAt ? cob.expiresAt.slice(0,10) : "")}.</p>
            <p><a href="${esc(cob.url)}" target="_blank" rel="noopener">Abrir boleto (PDF)</a></p>
            <p>Código de barras: <code>${esc(cob.barCode)}</code></p>
            ${cob.brCodeBase64 ? `<img src="${esc(cob.brCodeBase64)}" alt="PIX do boleto" style="max-width:150px" />` : ""}`;
        }
        html += `<p class="dica">Após o pagamento, clique em "Já assinei: atualizar" no plano atual para ativar.</p></div>`;
        saida.innerHTML = html + saida.innerHTML;
      } catch (err) {
        alert(err.message);
      }
    };
  });

  const cancelarBtn = $("#cancelar-plano");
  if (cancelarBtn) {
    cancelarBtn.onclick = async () => {
      if (!confirm("Cancelar a assinatura? O plano continua ativo até o fim do período pago.")) return;
      try {
        await api("POST", "/conta/plano/cancelar");
        alert("Assinatura cancelada. O plano volta para Grátis no fim do período.");
        carregar();
      } catch (err) { alert(err.message); }
    };
  }

  $("#pagamentos-lista").innerHTML = pagamentos.length ? `<table><thead><tr><th>Data</th><th>Plano</th><th class="num">Valor</th><th>Método</th><th>Status</th></tr></thead><tbody>
    ${pagamentos.map(pg => `<tr><td>${esc(pg.criada.slice(0,10).split("-").reverse().join("/"))}</td><td>${esc(pg.plano_nome)}</td><td class="num">${brl(pg.valor)}</td><td>${esc(pg.metodo)}</td><td><span class="estado ${pg.status === "pago" ? "e-CALCULO_SIMULADO" : "e-FALHA_TECNICA"}">${esc(pg.status)}</span></td></tr>`).join("")}
    </tbody></table>` : '<p class="dica">Nenhum pagamento realizado ainda.</p>';

  $("#faturas-lista").innerHTML = faturas.length ? `<table><thead><tr><th>Vencimento</th><th>Plano</th><th class="num">Valor</th><th>Status</th><th></th></tr></thead><tbody>
    ${faturas.map(f => `<tr><td>${esc(f.vence.slice(0,10).split("-").reverse().join("/"))}</td><td>${esc(f.plano_nome)}</td><td class="num">${brl(f.valor)}</td><td><span class="estado ${f.status === "aberta" ? "e-FALHA_TECNICA" : "e-CALCULO_SIMULADO"}">${esc(f.status)}</span></td><td>${f.status === "aberta" ? `<a class="botao secundario mini" href="${esc(f.url_pix || f.url_boleto)}" target="_blank">${f.url_pix ? "PIX" : "Boleto"}</a>` : ""}</td></tr>`).join("")}
    </tbody></table>` : '<p class="dica">Nenhuma fatura em aberto.</p>';
}

function carregarChaves() {
  if (!EU) return;
  chaves(EU);
}

function carregarConfig() {
  if (!EU) return;
  $("#perfil-nome").value = EU.usuario.nome || "";
  $("#perfil-email").value = EU.usuario.email || "";
  $("#f-prefs").ambiente_padrao.value = EU.preferencias?.ambiente_padrao || "teste";
  $("#f-prefs").competencia_padrao.value = EU.preferencias?.competencia_padrao || "atual";
}

async function carregar() {
  try { EU = await api("GET", "/conta/eu"); } catch (e) {
    if (e.status === 401) { $("#fora").hidden = false; $("#dentro").hidden = true; return; }
    $("#fora").hidden = false; $("#fora").querySelector("p").textContent = e.message; return;
  }
  const { planos } = await api("GET", "/conta/planos");
  PLANOS = planos;

  $("#fora").hidden = true; $("#dentro").hidden = false;
  atualizarUsuario(EU);

  const amb = $("#ambiente");
  amb.hidden = false;
  amb.textContent = EU.producao ? "Escolha em cada nota: Teste (sem valor fiscal) ou Valor fiscal (nota de verdade)."
    : "Este servidor só emite nota de teste (ambiente de teste do governo): as notas NÃO têm valor fiscal.";
  amb.classList.toggle("faixa-teste", !EU.producao);
  const fiscal = $('#f-emitir input[value="producao"]');
  fiscal.disabled = !EU.producao;
  if (!EU.producao) $('#f-emitir input[value="teste"]').checked = true;
  marcarAmbiente();

  renderPlano(EU, planos);
  renderKPIs(EU);
  renderProximosPassos(EU);
  renderUltimasNotas(EU);
  empresas(EU);
  carregarChaves();
  carregarConfig();
  initSidebar();

  if (location.hash) {
    const hash = location.hash.slice(1);
    const validSections = ["visao", "emitir", "empresas", "notas", "financeiro", "api", "config"];
    if (validSections.includes(hash)) showSection(hash);
  }
}

$("#f-emitir").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = ev.target, saida = $("#r-emitir");
  const tomador = f.doc.value.trim() ? { documento: f.doc.value, nome: f.nome.value, email: f.email.value, ...enderecoDoForm(f) } : undefined;
  saida.textContent = "Enviando ao governo…";
  try {
    const ambiente = f.ambiente.value;
    if (ambiente === "producao" && !confirm("Emitir NOTA COM VALOR FISCAL? Ela vale para a Receita e para a prefeitura.")) { saida.textContent = ""; return; }
    const n = await api("POST", `/conta/empresas/${f.empresa.value}/notas`, { codigo_servico: f.codigo_servico.value, descricao: f.descricao.value,
      valor: f.valor.value, tomador, ambiente, competencia: f.competencia.value, nbs: f.nbs.value.replace(/\D/g, ""),
      ref: `painel-${Date.now()}` });
    saida.textContent = n.status === "emitida" ? `Nota emitida${n.numero_nfse ? ` nº ${n.numero_nfse}` : ""}.`
      : n.status === "desconhecida" ? "O governo não respondeu. Ela aparece na lista assim que confirmar." : `Recusada: ${n.erros.map((x) => x.dica || x.mensagem).join("; ")}`;
    carregar();
  } catch (err) { saida.textContent = err.message; }
};

$("#f-emitir").empresa.onchange = (ev) => {
  notas(ev.target.value);
  $("#filtro-empresa").value = ev.target.value;
  if (SECTION_ATUAL === "notas") carregarNotasFiltradas();
};

$("#f-emitir").cep.addEventListener("blur", () => buscarCep($("#f-emitir")));

$("#f-emitir").competencia.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

function marcarAmbiente() {
  const fiscal = $("#f-emitir").ambiente.value === "producao", b = $("#b-emitir");
  b.textContent = fiscal ? "Emitir nota com valor fiscal" : "Emitir nota de teste";
  b.classList.toggle("botao-fiscal", fiscal);
}
document.querySelectorAll('#f-emitir input[name="ambiente"]').forEach((r) => { r.onchange = marcarAmbiente; });

$("#f-empresa").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    await api("POST", "/conta/empresas", { cnpj: f.cnpj.value, razao: f.razao.value, municipio_ibge: Number(f.municipio.value), regime: f.regime.value });
    f.reset(); $("#r-empresa").textContent = "Empresa adicionada. Agora suba o certificado."; carregar();
  } catch (err) { $("#r-empresa").textContent = err.message; }
};

$("#f-chave").onsubmit = async (ev) => {
  ev.preventDefault();
  const r = await api("POST", "/conta/chaves", { nome: ev.target.nome.value });
  const p = $("#chave-nova"); p.hidden = false; p.textContent = `Sua chave: ${r.chave} (${r.aviso})`;
  ev.target.reset(); carregar();
};

$("#f-perfil").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    await api("POST", "/conta/perfil", { nome: ev.target.nome.value });
    $("#r-perfil").textContent = "Salvo.";
    carregar();
  } catch (err) { $("#r-perfil").textContent = err.message; }
};

$("#f-prefs").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    await api("POST", "/conta/preferencias", { ambiente_padrao: ev.target.ambiente_padrao.value, competencia_padrao: ev.target.competencia_padrao.value });
    $("#r-prefs").textContent = "Preferências salvas.";
  } catch (err) { $("#r-prefs").textContent = err.message; }
};

$("#btn-exportar").onclick = async () => {
  if (!confirm("Exportar todos os seus dados (empresas, notas, chaves)? Isso pode demorar.")) return;
  try {
    const blob = await (await fetch("/conta/exportar", { credentials: "same-origin", headers: { "X-Nota": "1" } })).blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `emiteai-dados-${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) { alert("Erro ao exportar: " + err.message); }
};

$("#btn-excluir").onclick = async () => {
  if (!confirm("EXCLUIR CONTA? Isso apaga TUDO: empresas, certificados, notas, chaves. Irreversível.")) return;
  if (!prompt("Digite EXCLUIR para confirmar:")) return;
  try {
    await api("DELETE", "/conta/minha-conta");
    location.href = "/sair";
  } catch (err) { alert("Erro: " + err.message); }
};

fetch("/api/calculadora/dados-abertos/ufs").then((r) => r.json()).then((ufs) => {
  const sel = $("#f-empresa").uf;
  sel.innerHTML = '<option value="">Escolha</option>' + ufs.sort((a, b) => a.sigla.localeCompare(b.sigla)).map((u) => `<option>${esc(u.sigla)}</option>`).join("");
  sel.onchange = async () => {
    const m = $("#f-empresa").municipio;
    m.disabled = true;
    const lista = await (await fetch(`/api/calculadora/dados-abertos/ufs/municipios?siglaUf=${encodeURIComponent(sel.value)}`)).json();
    m.innerHTML = lista.map((x) => `<option value="${x.codigo}">${esc(x.nome)}</option>`).join(""); m.disabled = false;
  };
}).catch(() => {});

$("#filtro-empresa").onchange = carregarNotasFiltradas;
$("#filtro-status").onchange = carregarNotasFiltradas;
$("#filtro-periodo").onchange = carregarNotasFiltradas;

carregar();