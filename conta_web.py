"""Rotas da conta: login com Google, empresas, certificado, emissão, notas, chaves de API e plano.

Autenticação: cookie de sessão `na_sessao` (HttpOnly, Secure, SameSite=Lax) para a página, ou
`Authorization: Bearer na_...` para sistema e agente. Toda escrita pelo cookie exige o cabeçalho `X-Nota: 1`, que um
formulário de outro site não consegue mandar (CSRF); pela chave de API ele não é preciso.

Ambiente da emissão: produção restrita (sem valor fiscal) por padrão. Produção só com `NOTA_PRODUCAO=1` no servidor.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import urllib.parse
from http.cookies import CookieError, SimpleCookie

import abacatepay
import cofre
import oauth
import planos as planos_mod
from contas import Contas, Erro
from emissor import Emissor

COOKIE = "na_sessao"
COOKIE_OAUTH = "na_oauth"


class Resultado:
    def __init__(self, status: int, corpo: object = None, *, redirecionar: str = "", cookies: list[str] | None = None,
                 bruto: bytes | None = None, tipo: str = ""):
        self.status, self.corpo, self.redirecionar, self.cookies = status, corpo, redirecionar, cookies or []
        self.bruto, self.tipo = bruto, tipo


def danfse(nfse_xml: str, env: dict) -> bytes:
    """PDF oficial da NFS-e pelo DANFSe Service da Receita (Go), que roda ao lado em DANFSE_URL."""
    import urllib.error
    import urllib.request
    base = env.get("DANFSE_URL", "")
    if not base:
        raise Erro(503, "danfse_desligado", "o gerador de PDF não está ligado neste servidor")
    req = urllib.request.Request(base.rstrip("/") + "/api/danfse", data=nfse_xml.encode(), method="POST",
                                 headers={"Content-Type": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except (urllib.error.URLError, OSError):
        raise Erro(502, "danfse_falhou", "o gerador de PDF não respondeu; tente de novo") from None


def _cookie(nome: str, valor: str, idade: int) -> str:
    return f"{nome}={valor}; Path=/; Max-Age={idade}; HttpOnly; Secure; SameSite=Lax"


class Conta:
    def __init__(self, contas: Contas, *, base: str, planos: planos_mod.Planos | None = None, emissor: Emissor | None = None,
                 env: dict | None = None):
        self.c, self.base, self.env = contas, base.rstrip("/"), env if env is not None else os.environ
        self.planos = planos or planos_mod.Planos()
        self.emissor = emissor or Emissor(contas, producao=self.env.get("NOTA_PRODUCAO") == "1")

    # ---------------------------------------------------------------- portas

    def tratar(self, metodo: str, caminho: str, consulta: str, cab: dict, corpo: bytes) -> Resultado:
        try:
            return self._rotear(metodo, caminho, urllib.parse.parse_qs(consulta), cab, corpo)
        except Erro as e:
            return Resultado(e.status, {"erro": {"codigo": e.codigo, "mensagem": e.mensagem}})
        except cofre.CofreIndisponivel:
            return Resultado(503, {"erro": {"codigo": "cofre_indisponivel", "mensagem": "o cofre de certificados está fora do ar"}})

    def _cookies(self, cab: dict) -> dict:
        jar = SimpleCookie()
        try:
            jar.load(cab.get("cookie", ""))
        except CookieError:  # cookie malformado de outro site no mesmo domínio: ignora
            return {}
        return {k: m.value for k, m in jar.items()}

    def _quem(self, cab: dict) -> tuple[dict | None, bool]:
        """(usuário, veio por chave de API)."""
        aut = cab.get("authorization", "")
        if aut.lower().startswith("bearer "):
            u = self.c.usuario_da_chave(aut[7:].strip())
            if not u:
                raise Erro(401, "chave_invalida", "chave de API inválida ou revogada")
            return u, True
        return self.c.usuario_da_sessao(self._cookies(cab).get(COOKIE, "")), False

    def _exigir(self, cab: dict, metodo: str) -> dict:
        u, por_chave = self._quem(cab)
        if not u:
            raise Erro(401, "sem_sessao", "entre com o Google ou use uma chave de API")
        if metodo != "GET" and not por_chave and cab.get("x-nota") != "1":
            raise Erro(403, "csrf", "pedido sem o cabeçalho X-Nota")
        return u

    @staticmethod
    def _json(corpo: bytes) -> dict:
        try:
            d = json.loads(corpo or b"{}")
        except ValueError:
            raise Erro(400, "json_invalido", "corpo não é JSON") from None
        if not isinstance(d, dict):
            raise Erro(400, "json_invalido", "corpo precisa ser um objeto")
        return d

    def _mestra(self) -> bytes:
        return cofre.chave_mestra(self.env)

    # ---------------------------------------------------------------- rotas

    def _rotear(self, m: str, caminho: str, q: dict, cab: dict, corpo: bytes) -> Resultado:
        if caminho == "/entrar/google" and m == "GET":
            if not oauth.configurado("google"):
                raise Erro(503, "login_indisponivel", "o login com Google ainda não está configurado neste servidor")
            url, estado = oauth.iniciar("google", self.base)
            return Resultado(302, redirecionar=url, cookies=[_cookie(COOKIE_OAUTH, estado, 600)])
        if caminho == "/entrar/google/retorno" and m == "GET":
            estado, _, verif = self._cookies(cab).get(COOKIE_OAUTH, "").partition(".")
            if not estado or q.get("state", [""])[0] != estado:
                raise Erro(400, "estado_invalido", "o login expirou ou veio de outra aba; tente de novo")
            ident = oauth.identidade("google", q.get("code", [""])[0], self.base, verif)
            u = self.c.entrar_google(ident)
            return Resultado(302, redirecionar="/painel.html",
                             cookies=[_cookie(COOKIE, self.c.emitir_sessao(u["id"]), 14 * 24 * 3600), _cookie(COOKIE_OAUTH, "", 0)])
        if caminho == "/sair":
            return Resultado(302, redirecionar="/", cookies=[_cookie(COOKIE, "", 0)])
        if caminho == "/conta/planos" and m == "GET":
            return Resultado(200, {"planos": [p | {"checkout": planos_mod.link_checkout(p)} for p in self.planos.lista],
                                   "sob_medida": planos_mod.sob_medida(), "producao": self.emissor.producao})

        if caminho == "/webhooks/abacatepay" and m == "POST":
            return self._webhook_abacatepay(corpo, cab, q)

        u = self._exigir(cab, m)
        partes = [p for p in caminho.split("/") if p][1:]  # sem o "conta"
        if partes == ["eu"] and m == "GET":
            plano = self.planos.do_email(u["email"])
            return Resultado(200, {"usuario": u, "plano": plano, "empresas": self.c.empresas(u["id"]),
                                   "chaves": self.c.chaves(u["id"]), "producao": self.emissor.producao,
                                   "login_google": oauth.configurado("google")})
        if partes == ["plano", "atualizar"] and m == "POST":
            self.planos.esquecer(u["email"])
            return Resultado(200, {"plano": self.planos.do_email(u["email"])})
        if partes == ["planos", "assinar"] and m == "POST":
            return self._assinar_plano(u, corpo)
        if partes == ["empresas"] and m == "POST":
            d = self._json(corpo)
            plano = self.planos.do_email(u["email"])
            if len(self.c.empresas(u["id"])) >= plano["empresas"]:
                raise Erro(402, "limite_empresas", f"o plano {plano['nome']} permite {plano['empresas']} empresa(s)")
            try:
                ibge = int(d.get("municipio_ibge", 0))
            except (TypeError, ValueError):
                raise Erro(422, "municipio_invalido", "código IBGE do município tem 7 dígitos") from None
            return Resultado(201, self.c.cadastrar_empresa(u["id"], str(d.get("cnpj", "")), str(d.get("razao", "")),
                                                            ibge, str(d.get("regime", "simples"))))
        if len(partes) >= 2 and partes[0] == "empresas":
            return self._empresa(m, u, partes[1], partes[2:], corpo)
        if partes == ["chaves"] and m == "POST":
            return Resultado(201, {"chave": self.c.criar_chave(u["id"], str(self._json(corpo).get("nome", ""))),
                                   "aviso": "guarde agora: ela não aparece de novo"})
        if len(partes) == 2 and partes[0] == "chaves" and m == "DELETE":
            return Resultado(200, {"revogadas": self.c.revogar_chave(u["id"], partes[1])})
        raise Erro(404, "rota_inexistente", "rota da conta não existe")

    def _assinar_plano(self, u: dict, corpo: bytes) -> Resultado:
        d = self._json(corpo)
        plano_id = str(d.get("plano_id", ""))
        metodo = str(d.get("metodo", "PIX")).upper()
        plano = None
        for p in self.planos.lista:
            if p["id"] == plano_id:
                plano = p
                break
        if not plano or plano["preco_mes"] <= 0:
            return Resultado(400, {"erro": {"codigo": "plano_invalido", "mensagem": "plano não encontrado ou gratuito"}})
        if metodo not in ("PIX", "BOLETO"):
            return Resultado(400, {"erro": {"codigo": "metodo_invalido", "mensagem": "método deve ser PIX ou BOLETO"}})
        valor_centavos = int(round(plano["preco_mes"] * 100))
        customer = {}
        if u.get("email"):
            customer["email"] = u["email"]
        if u.get("nome"):
            customer["name"] = u["nome"]
        # AbacatePay exige cellphone se customer for informado
        # Se não tiver, não enviar customer (PIX aceita sem customer)
        if not customer.get("cellphone"):
            customer = None
        external_id = f"plano-{plano_id}-{u['id']}"
        metadata = {"plano_id": plano_id, "usuario_id": u["id"], "email": u["email"]}
        try:
            if metodo == "PIX":
                cobranca = abacatepay.criar_cobranca_pix(valor_centavos, external_id, f"Plano {plano['nome']}", customer, metadata)
            else:
                cobranca = abacatepay.criar_cobranca_boleto(valor_centavos, external_id, f"Plano {plano['nome']}", customer, metadata)
        except RuntimeError as e:
            return Resultado(502, {"erro": {"codigo": "abacatepay_erro", "mensagem": str(e)}})
        self.c.criar_pagamento(u["id"], plano_id, cobranca["id"], metodo, valor_centavos)
        return Resultado(200, {"cobranca": cobranca, "plano": plano})

    def _webhook_abacatepay(self, corpo: bytes, cab: dict, q: dict = None) -> Resultado:
        secret = self.env.get("ABACATEPAY_WEBHOOK_SECRET", "")
        secret_recebido = cab.get("x-webhook-secret", "")
        # Tentar extrair do query string
        if not secret_recebido and q:
            vals = q.get("webhookSecret", [])
            if vals:
                secret_recebido = vals[0] if isinstance(vals, list) else vals
        if secret and secret_recebido != secret:
            return Resultado(401, {"erro": {"codigo": "secret_invalido", "mensagem": "webhook secret inválido"}})
        try:
            d = json.loads(corpo or b"{}")
        except ValueError:
            return Resultado(400, {"erro": "JSON inválido"})
        evento = d.get("event", "")
        if evento == "transparent.completed":
            data = d.get("data", {})
            cobranca_id = data.get("id", "")
            if cobranca_id:
                pag = self.c.confirmar_pagamento(cobranca_id)
                if pag:
                    self.planos.esquecer(pag["usuario_id"] and self.c.usuario(pag["usuario_id"])["email"]) if False else None
                    # Limpar cache do plano do usuário
                    try:
                        u = self.c.usuario(pag["usuario_id"])
                        if u:
                            self.planos.esquecer(u["email"])
                    except Exception:
                        pass
        return Resultado(200, {"ok": True})

    def _empresa(self, m: str, u: dict, eid: str, resto: list[str], corpo: bytes) -> Resultado:
        if not re.fullmatch(r"emp_[A-Za-z0-9_-]{1,40}", eid):
            raise Erro(404, "empresa_nao_encontrada", "empresa não encontrada")
        if resto == [] and m == "GET":
            return Resultado(200, self.c.empresa(u["id"], eid))
        if resto == ["certificado"] and m == "POST":
            d = self._json(corpo)
            try:
                pfx = base64.b64decode(str(d.get("pfx_base64", "")), validate=True)
            except (binascii.Error, ValueError):
                raise Erro(422, "pfx_invalido", "envie o arquivo .pfx em base64") from None
            return Resultado(201, self.c.guardar_certificado(u["id"], eid, pfx, str(d.get("senha", "")), self._mestra()))
        if resto == ["certificado"] and m == "DELETE":
            return Resultado(200, {"apagado": self.c.apagar_certificado(u["id"], eid)})
        if resto == ["notas"] and m == "GET":
            return Resultado(200, {"notas": self.emissor.notas(u["id"], eid)})
        if resto == ["notas"] and m == "POST":
            plano = self.planos.do_email(u["email"])
            nota = self.emissor.emitir(u["id"], eid, self._json(corpo), limite_mes=plano["notas_mes"], mestra=self._mestra())
            return Resultado(201 if nota["status"] == "emitida" else 202, nota)
        if len(resto) == 3 and resto[0] == "notas" and resto[2] == "cancelar" and m == "POST":
            d = self._json(corpo)
            return Resultado(200, self.emissor.cancelar(u["id"], eid, resto[1], motivo_codigo=str(d.get("motivo_codigo", "1")),
                                                        motivo=str(d.get("motivo", "")), mestra=self._mestra()))
        if len(resto) == 3 and resto[0] == "notas" and resto[2] == "pdf" and m == "GET":
            self.c.empresa(u["id"], eid)
            linha = self.c.bd.execute("select nfse_xml from notas where id = ? and empresa_id = ? and status = 'emitida'",
                                      (resto[1], eid)).fetchone()
            if not linha or not linha[0]:
                raise Erro(404, "nota_sem_xml", "nota não emitida ou sem o XML da NFS-e")
            return Resultado(200, bruto=danfse(linha[0], self.env), tipo="application/pdf")
        raise Erro(404, "rota_inexistente", "rota da empresa não existe")
