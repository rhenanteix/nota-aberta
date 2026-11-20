<div align="center">

# Nota Aberta

**Nota fiscal de serviço grátis e a calculadora oficial da Reforma Tributária, com tela, API e MCP. Aberta.**

CBS · IBS estadual · IBS municipal · Imposto Seletivo (LC 214/2025)

[![Licença MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-0f6b4f)](LICENSE)
[![Motor oficial da Receita](https://img.shields.io/badge/motor-Receita%20Federal%201.6.1-0f6b4f)](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular)
[![Portaria Cotec 342/2026](https://img.shields.io/badge/vers%C3%A3o-conferida%20na%20Portaria%20342%2F2026-0f6b4f)](https://www.in.gov.br/web/dou/-/portaria-cotec-n-342-de-12-de-novembro-de-2026-744398356)
[![MCP](https://img.shields.io/badge/MCP-4%20ferramentas-0f6b4f)](#mcp-para-agentes-de-ia)

</div>

---

Em novembro de 2026 a Receita Federal abriu, sob licença MIT, o código da calculadora que usa para a CBS e o IBS.
Ela veio como uma API Java para programador. **A Nota Aberta coloca uma tela, uma API pública e um servidor MCP em
volta desse motor, sem mudar uma linha da conta.** O número que sai daqui é o número da Receita.

E emite **NFS-e nacional** (nota de serviço) com o certificado A1 do próprio emitente: login com Google, certificado
cifrado, cota por plano, PDF oficial (o DANFSe que a Receita também abriu), API e MCP. Veja o [roteiro](#roteiro).

## O que tem hoje

| | |
|---|---|
| 🧮 **Calculadora na web** | data, UF, município, CST e cClassTrib das tabelas oficiais da data, base de cálculo. Resultado por item com redução, alíquota efetiva e a memória de cálculo do motor |
| 🔌 **API REST** | a mesma API da Receita em `/api`, com Swagger. Rotas de cálculo e de dados abertos; teto por IP; CORS aberto |
| 🤖 **MCP** | `calcular_cbs_ibs`, `tabelas_cbs_ibs`, `aliquotas_referencia` e `versao_motor` em `POST /mcp`, só leitura |
| ✅ **Mesmo número da Receita** | o motor é compilado do commit registrado na Portaria Cotec 342/2026, e um teste falha se o commit mudar sem paridade nova |
| 🧾 **Emissor de NFS-e** | DPS do padrão nacional (produção 1.01 e produção restrita 1.02), assinatura RSA-SHA256, envio à Sefin Nacional com o A1 só em memória, pedido repetido nunca duplica |
| 🔐 **Certificado cifrado** | AES-256-GCM com uma chave por empresa, embrulhada por uma chave mestra fora do banco; apaga quando quiser |
| 📄 **PDF oficial** | DANFSe Service da Receita (Go) ao lado do motor |

## Rodar em um minuto

```bash
docker build -t nota-aberta .
docker run --rm -p 8080:8080 --read-only --tmpfs /tmp nota-aberta
# abra http://localhost:8080
```

O build baixa o motor oficial no commit fixado, gera a base de tabelas com o flyway da própria Receita e compila o
jar. Esse comando liga a calculadora pública; cadastro e emissão exigem a configuração abaixo.

### Habilitar cadastro e emissão localmente

Copie `.env.example` para `.env` e preencha `NOTA_SEGREDO_SESSAO` e `NOTA_CHAVE_MESTRA` com os
comandos indicados no arquivo. Preencha também `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` do seu cliente OAuth.
O endereço de retorno usado pela aplicação local é `http://localhost:8080/entrar/google/retorno`;
ele precisa estar autorizado no cliente Google. Não publique o arquivo `.env`.

Pare o container anterior antes de executar:

```bash
docker build -t nota-aberta . &&
docker run --rm -p 8080:8080 --read-only --tmpfs /tmp \
  --env-file .env --mount source=nota-aberta-dados,target=/data nota-aberta
```

O volume guarda contas e certificados entre reinicializações e é gravável pelo usuário do container.
Guarde a chave mestra junto com o backup do banco: trocar a chave impede abrir os certificados existentes.
Sem `NOTA_DB` e um segredo de sessão de pelo menos 32 caracteres, as rotas de conta retornam `conta_desligada`.
A emissão permanece em produção restrita (`NOTA_PRODUCAO=0`). Login e emissão precisam acessar os serviços externos.

## Exemplo pela API

O item 3 do exemplo oficial da Receita (serviço de educação, redução de 60%):

```bash
curl -s -X POST http://localhost:8080/api/calculadora/observabilidade/regime-geral \
  -H 'Content-Type: application/json' -d '{
  "id": "exemplo", "versao": "1.0.0", "dhFatoGerador": "2027-03-10T10:00:00-03:00",
  "municipio": 4314902, "uf": "RS",
  "itens": [{ "numero": 1, "nbs": "122011200", "cst": "200", "cClassTrib": "200028",
              "baseCalculo": 1200.00,
              "aliquotasNominais": { "cbs": 8.8, "ibsEstadual": 0.05, "ibsMunicipal": 0.05 } }]
}'
# gCBS.vCBS = "42.24" (8,8% com redução de 60% = 3,52% efetiva) · vIBS = "0.48"
```

Duas regras do motor que pegam todo mundo:

- **Até 31/12/2026** o motor resolve as alíquotas sozinho e **recusa** `aliquotasNominais` (erro `REG-036`).
- **A partir de 2027** a alíquota nominal é obrigatória na entrada. O motor só traz alíquotas de referência de 2026.

## MCP para agentes de IA

Aponte o seu cliente MCP para `https://<seu-host>/mcp` (Streamable HTTP, só POST, sem sessão).

| ferramenta | faz |
|---|---|
| `calcular_cbs_ibs` | calcula uma operação inteira, item a item, com memória de cálculo |
| `tabelas_cbs_ibs` | CST e cClassTrib vigentes na data, com reduções e se o item usa NCM ou NBS |
| `aliquotas_referencia` | alíquotas de referência da CBS, do IBS estadual e do municipal |
| `versao_motor` | versão do motor e da base de tabelas que está respondendo |

Com `Authorization: Bearer na_...` (chave criada no painel): `listar_empresas`, `emitir_nfse`, `cancelar_nfse` e `listar_notas`.

## Como funciona

```
navegador / sistema / agente
        │
        ▼
  servidor.py  (Python, só biblioteca padrão)
   ├─ /            página (web/)
   ├─ /api/*       repasse ao motor, só rotas de cálculo e dados abertos, teto por IP, corpo ≤ 256 KB
   ├─ /mcp         MCP (mcp.py), cada chamada conta no teto
   └─ /health      sonda
        │ 127.0.0.1
        ▼
  motor oficial da Receita (Java 21, Spring Boot) + SQLite das tabelas, só leitura
```

Nenhuma conta de tributo é feita por código nosso: a página monta a entrada e mostra a saída. Reescrever a fórmula
seria uma segunda cópia da regra fiscal, e ela divergiria da oficial na primeira atualização.

## Roteiro

- [x] Calculadora pública com o motor oficial (Regime Regular 1.6.1)
- [x] API e MCP
- [x] DANFSe: PDF oficial da NFS-e a partir do XML (serviço em Go da Receita, ~0,3 s por nota)
- [x] Emissor de NFS-e nacional: contas com Google, certificado cifrado, cota, painel, API e MCP
- [x] Primeira nota aceita pela Sefin: produção restrita em 18/11 e **produção (valor fiscal) em 19/11**, com cancelamento
- [ ] Simples Nacional (motor 0.0.6 da Receita: já compila e sobe com a mesma base)
- [x] Cancelar nota pela tela, pela API e pelo MCP (`cancelar_nfse`; evento 101101 aceito pela Sefin em 18/11)
- [ ] Substituir nota (cancelar e reemitir numa operação só)
- [ ] Conferência em lote: suba as notas do seu sistema e veja onde o cálculo diverge do oficial

## Testes

```bash
pip install cryptography lxml && python3 -m pytest -q tests/       # 70 testes, governo e motor falsos
RTC_MOTOR_REAL=http://127.0.0.1:8081 python3 -m pytest -q tests/    # paridade com o motor oficial rodando
```

## Aviso

Este projeto **não é da Receita Federal**. Ele roda, sem alterar, o código que a Receita publicou (veja
[NOTICE.md](NOTICE.md)). O resultado é informativo: decisão judicial ou interpretação tributária diferente pode exigir
adaptação, e nem a Receita nem nós damos garantia ou efeito vinculante ao cálculo. Confira com o seu contador antes
de emitir documento fiscal.

## Licença

[MIT](LICENSE). Use, copie, mude e venda à vontade. Componentes de terceiros seguem as próprias licenças
([NOTICE.md](NOTICE.md)).

Feito pela [Genesis Innovation](https://genesisinnovation.io).
