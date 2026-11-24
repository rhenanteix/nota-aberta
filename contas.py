"""Contas, empresas, certificado guardado e uso do mês (SQLite, só biblioteca padrão + o cofre).

Regras que o banco impõe sozinho (e os testes cobram):
  - uma pessoa entra só com e-mail VERIFICADO pelo Google (quem decide isso é o oauth.py);
  - uma empresa (CNPJ) pertence a uma conta só; outra pessoa não "adota" o CNPJ de alguém;
  - certificado só entra depois de conferido (certificado.ler) e só cifrado (cofre.guardar);
  - apagar o certificado apaga as três colunas cifradas: a inversa é real, não um "desativado".
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import certificado
import cofre

ESQUEMA = """
create table if not exists usuarios (
  id text primary key, google_sub text unique not null, email text not null, nome text not null default '',
  criado text not null);
create table if not exists empresas (
  id text primary key, usuario_id text not null references usuarios(id), cnpj text unique not null,
  razao text not null, municipio_ibge integer not null, regime text not null default 'simples',
  criado text not null);
create table if not exists certificados (
  empresa_id text primary key references empresas(id) on delete cascade,
  chave_embrulhada blob not null, pfx_cifrado blob not null, senha_cifrada blob not null,
  titular text not null, valido_ate text not null, guardado text not null);
create table if not exists chaves (
  hash text primary key, usuario_id text not null references usuarios(id) on delete cascade, nome text not null,
  prefixo text not null, criada text not null);
create table if not exists uso (
  empresa_id text not null references empresas(id) on delete cascade, mes text not null, notas integer not null default 0,
  primary key (empresa_id, mes));
create table if not exists pagamentos (
  id text primary key, usuario_id text not null references usuarios(id), plano_id text not null,
  abacatepay_id text unique not null, metodo text not null, status text not null default 'PENDING',
  valor_centavos integer not null, criado text not null, pago_em text);
"""
SESSAO_SEGUNDOS = 14 * 24 * 3600


class Erro(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status, self.codigo, self.mensagem = status, codigo, mensagem


def _agora() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def mes_atual() -> str:
    """A cota vira à meia-noite de Brasília, não à do servidor (que roda em UTC)."""
    return dt.datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m")


def _id(prefixo: str) -> str:
    return f"{prefixo}_{secrets.token_urlsafe(12)}"


class Contas:
    def __init__(self, caminho: str | Path, segredo_sessao: bytes):
        if len(segredo_sessao) < 32:
            raise ValueError("segredo de sessão com menos de 32 bytes")
        self.segredo = segredo_sessao
        self.trava = threading.Lock()
        self.bd = sqlite3.connect(str(caminho), check_same_thread=False, isolation_level=None)
        self.bd.row_factory = sqlite3.Row
        self.bd.execute("pragma foreign_keys = on")
        self.bd.execute("pragma journal_mode = wal")
        self.bd.executescript(ESQUEMA)

    # ------------------------------------------------------------------ sessão

    def emitir_sessao(self, usuario_id: str, agora: float | None = None) -> str:
        corpo = base64.urlsafe_b64encode(json.dumps({"u": usuario_id, "e": int((agora or time.time()) + SESSAO_SEGUNDOS)}).encode()).decode()
        assinatura = hmac.new(self.segredo, corpo.encode(), hashlib.sha256).hexdigest()
        return f"{corpo}.{assinatura}"

    def usuario_da_sessao(self, token: str, agora: float | None = None) -> dict | None:
        corpo, _, assinatura = (token or "").rpartition(".")
        esperado = hmac.new(self.segredo, corpo.encode(), hashlib.sha256).hexdigest()
        if not corpo or not hmac.compare_digest(assinatura, esperado):
            return None
        try:
            dados = json.loads(base64.urlsafe_b64decode(corpo))
        except ValueError:
            return None
        if dados.get("e", 0) < (agora or time.time()):
            return None
        return self.usuario(dados.get("u", ""))

    # ---------------------------------------------------------------- usuários

    def entrar_google(self, ident: dict) -> dict:
        if not ident.get("verificado") or not ident.get("email") or not ident.get("sub"):
            raise Erro(403, "email_nao_verificado", "a conta Google precisa ter o e-mail verificado")
        with self.trava:
            linha = self.bd.execute("select * from usuarios where google_sub = ?", (ident["sub"],)).fetchone()
            if linha:
                self.bd.execute("update usuarios set email = ?, nome = ? where id = ?", (ident["email"], ident.get("nome", ""), linha["id"]))
                return self.usuario(linha["id"])
            uid = _id("usu")
            self.bd.execute("insert into usuarios values (?, ?, ?, ?, ?)", (uid, ident["sub"], ident["email"], ident.get("nome", ""), _agora()))
            return self.usuario(uid)

    def usuario(self, uid: str) -> dict | None:
        linha = self.bd.execute("select id, email, nome, criado from usuarios where id = ?", (uid,)).fetchone()
        return dict(linha) if linha else None

    # ------------------------------------------------------------ chaves de API

    def criar_chave(self, usuario_id: str, nome: str) -> str:
        """O valor aparece UMA vez; o banco guarda só o SHA-256 (vazar o banco não vaza a chave)."""
        token = "na_" + secrets.token_urlsafe(32)
        with self.trava:
            self.bd.execute("insert into chaves values (?, ?, ?, ?, ?)",
                            (hashlib.sha256(token.encode()).hexdigest(), usuario_id, (nome or "chave")[:60], token[:12], _agora()))
        return token

    def usuario_da_chave(self, token: str) -> dict | None:
        if not (token or "").startswith("na_"):
            return None
        linha = self.bd.execute("select usuario_id from chaves where hash = ?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return self.usuario(linha["usuario_id"]) if linha else None

    def chaves(self, usuario_id: str) -> list[dict]:
        return [dict(r) for r in self.bd.execute("select prefixo, nome, criada from chaves where usuario_id = ? order by criada",
                                                 (usuario_id,))]

    def revogar_chave(self, usuario_id: str, prefixo: str) -> int:
        with self.trava:
            return self.bd.execute("delete from chaves where usuario_id = ? and prefixo = ?", (usuario_id, prefixo)).rowcount

    # ------------------------------------------------------------ pagamentos (AbacatePay)
    def criar_pagamento(self, usuario_id: str, plano_id: str, abacatepay_id: str, metodo: str, valor_centavos: int) -> dict:
        pid = _id("pag")
        with self.trava:
            self.bd.execute("insert into pagamentos values (?, ?, ?, ?, ?, 'PENDING', ?, ?, null)",
                            (pid, usuario_id, plano_id, abacatepay_id, metodo, valor_centavos, _agora()))
        return self.pagamento(pid)

    def pagamento(self, pagamento_id: str) -> dict | None:
        r = self.bd.execute("select * from pagamentos where id = ?", (pagamento_id,)).fetchone()
        return dict(r) if r else None

    def pagamento_por_abacatepay(self, abacatepay_id: str) -> dict | None:
        r = self.bd.execute("select * from pagamentos where abacatepay_id = ?", (abacatepay_id,)).fetchone()
        return dict(r) if r else None

    def confirmar_pagamento(self, abacatepay_id: str) -> dict | None:
        with self.trava:
            self.bd.execute("update pagamentos set status = 'PAID', pago_em = ? where abacatepay_id = ?",
                            (_agora(), abacatepay_id))
        return self.pagamento_por_abacatepay(abacatepay_id)

    def planos_pagos(self, usuario_id: str) -> list[dict]:
        return [dict(r) for r in self.bd.execute(
            "select * from pagamentos where usuario_id = ? and status = 'PAID' order by pago_em desc", (usuario_id,))]

    def plano_ativo(self, usuario_id: str) -> str | None:
        r = self.bd.execute(
            "select plano_id from pagamentos where usuario_id = ? and status = 'PAID' order by pago_em desc limit 1",
            (usuario_id,)).fetchone()
        return r["plano_id"] if r else None

    # ---------------------------------------------------------------- empresas

    def cadastrar_empresa(self, usuario_id: str, cnpj: str, razao: str, municipio_ibge: int, regime: str = "simples") -> dict:
        cnpj = certificado.so_digitos(cnpj)
        if len(cnpj) != 14:
            raise Erro(422, "cnpj_invalido", "CNPJ precisa ter 14 dígitos")
        if not (razao or "").strip():
            raise Erro(422, "razao_vazia", "informe a razão social")
        if not (1_000_000 <= int(municipio_ibge) <= 9_999_999):
            raise Erro(422, "municipio_invalido", "código IBGE do município tem 7 dígitos")
        if regime not in ("simples", "mei", "normal"):
            raise Erro(422, "regime_invalido", "regime é simples, mei ou normal")
        with self.trava:
            dono = self.bd.execute("select usuario_id from empresas where cnpj = ?", (cnpj,)).fetchone()
            if dono and dono["usuario_id"] != usuario_id:
                raise Erro(409, "cnpj_de_outra_conta", "esse CNPJ já está em outra conta; fale com o suporte se ele é seu")
            if dono:
                raise Erro(409, "cnpj_ja_cadastrado", "esse CNPJ já está na sua conta")
            eid = _id("emp")
            self.bd.execute("insert into empresas values (?, ?, ?, ?, ?, ?, ?)",
                            (eid, usuario_id, cnpj, razao.strip(), int(municipio_ibge), regime, _agora()))
        return self.empresa(usuario_id, eid)

    def empresas(self, usuario_id: str) -> list[dict]:
        linhas = self.bd.execute("select id from empresas where usuario_id = ? order by criado", (usuario_id,)).fetchall()
        return [self.empresa(usuario_id, l["id"]) for l in linhas]

    def empresa(self, usuario_id: str, empresa_id: str) -> dict:
        """Só devolve empresa da própria conta: id de empresa de outra pessoa é 404, não 403 (não confirma que existe)."""
        e = self.bd.execute("select * from empresas where id = ? and usuario_id = ?", (empresa_id, usuario_id)).fetchone()
        if not e:
            raise Erro(404, "empresa_nao_encontrada", "empresa não encontrada")
        c = self.bd.execute("select titular, valido_ate, guardado from certificados where empresa_id = ?", (empresa_id,)).fetchone()
        mes = mes_atual()
        u = self.bd.execute("select notas from uso where empresa_id = ? and mes = ?", (empresa_id, mes)).fetchone()
        return {"id": e["id"], "cnpj": e["cnpj"], "razao": e["razao"], "municipio_ibge": e["municipio_ibge"], "regime": e["regime"],
                "certificado": dict(c) if c else None, "notas_no_mes": u["notas"] if u else 0}

    # ------------------------------------------------------------ certificado

    def guardar_certificado(self, usuario_id: str, empresa_id: str, pfx: bytes, senha: str, mestra: bytes) -> dict:
        emp = self.empresa(usuario_id, empresa_id)
        try:
            info = certificado.ler(pfx, senha, cnpj_esperado=emp["cnpj"])
        except certificado.CertificadoRecusado as e:
            raise Erro(422, "certificado_recusado", str(e)) from None
        colunas = cofre.guardar(empresa_id, pfx, senha, mestra)
        with self.trava:
            self.bd.execute("insert or replace into certificados values (?, ?, ?, ?, ?, ?, ?)",
                            (empresa_id, colunas["chave_embrulhada"], colunas["pfx_cifrado"], colunas["senha_cifrada"],
                             info["titular"], info["valido_ate"], _agora()))
        return {"titular": info["titular"], "valido_ate": info["valido_ate"], "dias_restantes": info["dias_restantes"]}

    def abrir_certificado(self, usuario_id: str, empresa_id: str, mestra: bytes) -> tuple[bytes, str]:
        self.empresa(usuario_id, empresa_id)
        r = self.bd.execute("select chave_embrulhada, pfx_cifrado, senha_cifrada from certificados where empresa_id = ?",
                            (empresa_id,)).fetchone()
        if not r:
            raise Erro(409, "sem_certificado", "suba o certificado A1 da empresa antes de emitir")
        return cofre.abrir(empresa_id, dict(r), mestra)

    def apagar_certificado(self, usuario_id: str, empresa_id: str) -> bool:
        self.empresa(usuario_id, empresa_id)
        with self.trava:
            return self.bd.execute("delete from certificados where empresa_id = ?", (empresa_id,)).rowcount == 1

    # -------------------------------------------------------------------- uso

    def consumir_nota(self, empresa_id: str, limite: int, mes: str | None = None) -> int:
        """Reserva uma nota da cota do mês de forma atômica; acima do limite recusa sem gastar."""
        mes = mes or mes_atual()
        with self.trava:
            self.bd.execute("insert or ignore into uso values (?, ?, 0)", (empresa_id, mes))
            cur = self.bd.execute("update uso set notas = notas + 1 where empresa_id = ? and mes = ? and notas < ?",
                                  (empresa_id, mes, limite))
            if cur.rowcount != 1:
                raise Erro(402, "cota_esgotada", f"a cota de {limite} notas do mês acabou; escolha um plano maior")
            return self.bd.execute("select notas from uso where empresa_id = ? and mes = ?", (empresa_id, mes)).fetchone()["notas"]

    def devolver_nota(self, empresa_id: str, mes: str | None = None) -> None:
        """Nota recusada pela Receita não conta na cota."""
        mes = mes or mes_atual()
        with self.trava:
            self.bd.execute("update uso set notas = max(notas - 1, 0) where empresa_id = ? and mes = ?", (empresa_id, mes))
