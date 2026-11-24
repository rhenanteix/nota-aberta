// Painel da conta. Toda escrita leva o cabeçalho X-Nota (o servidor recusa sem ele: CSRF).
// O certificado vai em base64 direto para /conta/empresas/<id>/certificado e não fica em nenhum lugar do navegador.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const ESTADOS = { emitida: "Emitida", recusada: "Recusada", desconhecida: "Aguardando o governo", pendente: "Pendente", cancelada: "Cancelada" };

async function api(metodo, caminho, corpo) {
  const r = await fetch(caminho, { method: metodo, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Nota": "1" }, body: corpo ? JSON.stringify(corpo) : undefined });
  const dados = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 202) throw Object.assign(new Error(dados?.erro?.mensagem || `erro ${r.status}`), { status: r.status });
  return dados;
}

let EU = null;

function plano(eu, planos) {
  const p = eu.plano;
  const usadas = eu.empresas.reduce((s, e) => s + e.notas_no_mes, 0);
  const outros = planos.filter((x) => x.preco_mes > p.preco_mes);
  const reais = (v) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
  // Eventos dos botões de assinatura
  document.querySelectorAll("[data-plano]").forEach((b) => {
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

function empresas(eu) {
  const caixa = $("#empresas");
  caixa.innerHTML = eu.empresas.length ? "" : '<p class="dica">Nenhuma empresa ainda. Adicione abaixo.</p>';
  for (const e of eu.empresas) {
    const el = $("#tpl-empresa").content.firstElementChild.cloneNode(true);
    $(".e-nome", el).textContent = `${e.razao} · ${e.cnpj}`;
    const c = e.certificado;
    $(".e-info", el).textContent = c ? `Certificado de ${c.titular}, válido até ${c.valido_ate.split("-").reverse().join("/")}. ${e.notas_no_mes} nota(s) no mês.`
      : "Sem certificado: suba o A1 para emitir.";
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
  if (sel.value) notas(sel.value);
}

async function notas(eid) {
  const { notas: lista } = await api("GET", `/conta/empresas/${eid}/notas`);
  $("#notas").innerHTML = lista.length ? `<table><thead><tr><th>Quando</th><th>Tomador</th><th class="num">Valor</th><th>Situação</th></tr></thead><tbody>
    ${lista.map((n) => `<tr><td>${esc(n.criada.slice(0, 16).replace("T", " "))}</td><td>${esc(n.tomador || "—")}</td><td class="num">${brl(n.valor)}</td>
      <td>${n.producao ? "" : '<span class="etiqueta">teste</span> '}${esc(ESTADOS[n.status] || n.status)}${n.numero_nfse ? ` nº ${esc(n.numero_nfse)}` : ""}${n.status === "emitida" ? ` · <a href="/conta/empresas/${esc(eid)}/notas/${esc(n.id)}/pdf" target="_blank" rel="noopener">PDF</a> · <button type="button" class="secundario mini cancelar-nota" data-n="${esc(n.id)}" data-num="${esc(n.numero_nfse || "")}">Cancelar</button>` : ""}${["emitida", "recusada", "cancelada"].includes(n.status) ? ` · <button type="button" class="secundario mini repetir-nota" data-n="${esc(n.id)}">Repetir</button>` : ""}${n.erros.length ? `<br><span class="erro-curto">${esc(n.erros.map((x) => `${x.codigo} ${x.mensagem}${x.dica ? ` → ${x.dica}` : ""}`).join("; "))}</span>` : ""}</td></tr>`).join("")}
    </tbody></table>` : '<p class="dica">Nenhuma nota ainda.</p>';
  document.querySelectorAll("#notas .cancelar-nota").forEach((b) => { b.onclick = () => cancelar(eid, b.dataset.n, b.dataset.num); });
  document.querySelectorAll("#notas .repetir-nota").forEach((b) => { b.onclick = () => repetir(lista.find((x) => x.id === b.dataset.n)); });
}

// Contrato recorrente (licitação de prefeitura): mesma nota todo mês, muda só a competência. Preenche o formulário
// com a nota escolhida e deixa a competência para a pessoa conferir; nada é enviado sem clicar em Emitir.
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
}

// Endereço do tomador: vai inteiro ou não vai (o servidor recusa pela metade). O CEP preenche rua, bairro e o código IBGE.
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

// Cancelamento é evento no governo e não tem volta: pede motivo (o governo exige 15+ caracteres) e confirma.
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
  notas(eid);
}

function chaves(eu) {
  $("#chaves").innerHTML = eu.chaves.length ? `<ul class="lista">${eu.chaves.map((c) => `<li><code>${esc(c.prefixo)}…</code> ${esc(c.nome)} <button type="button" class="secundario mini" data-p="${esc(c.prefixo)}">Revogar</button></li>`).join("")}</ul>` : "";
  document.querySelectorAll("#chaves [data-p]").forEach((b) => { b.onclick = async () => { await api("DELETE", `/conta/chaves/${b.dataset.p}`); carregar(); }; });
}

async function carregar() {
  try { EU = await api("GET", "/conta/eu"); } catch (e) {
    if (e.status === 401) { $("#fora").hidden = false; $("#dentro").hidden = true; return; }
    $("#fora").hidden = false; $("#fora").querySelector("p").textContent = e.message; return;
  }
  const { planos } = await api("GET", "/conta/planos");
  $("#fora").hidden = true; $("#dentro").hidden = false;
  $("#quem").textContent = EU.usuario.email;
  const amb = $("#ambiente");
  amb.hidden = false;
  amb.textContent = EU.producao ? "Escolha em cada nota: Teste (sem valor fiscal) ou Valor fiscal (nota de verdade)."
    : "Este servidor só emite nota de teste (ambiente de teste do governo): as notas NÃO têm valor fiscal.";
  amb.classList.toggle("faixa-teste", !EU.producao);
  const fiscal = $('#f-emitir input[value="producao"]');
  fiscal.disabled = !EU.producao;
  if (!EU.producao) $('#f-emitir input[value="teste"]').checked = true;
  marcarAmbiente();
  plano(EU, planos); empresas(EU); chaves(EU);
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
$("#f-emitir").empresa.onchange = (ev) => notas(ev.target.value);
$("#f-emitir").cep.addEventListener("blur", () => buscarCep($("#f-emitir")));
// Competência começa em hoje (horário de Brasília pelo relógio da pessoa) e fica aberta: serviço de outubro emitido em novembro.
$("#f-emitir").competencia.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

// O botão diz o que vai sair: "nota de teste" ou "nota com valor fiscal" (vermelho), para ninguém emitir fiscal sem ver.
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

// UF e município vêm das tabelas da própria calculadora (motor da Receita).
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

carregar();
