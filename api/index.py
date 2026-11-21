"""Vercel Python serverless function para nota-aberta.
Adapta servidor.py + conta_web.py + mcp.py ao formato do Vercel.
Arquivos estáticos em web/ são servidos pelo outputDirectory automaticamente.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import conta_web
    import contas
    import mcp
except Exception:
    conta_web = None
    contas = None
    mcp = None

ROTAS_CONTA = ("/entrar/", "/sair", "/conta/")


def _cookie(nome: str, valor: str, idade: int) -> str:
    return f"{nome}={valor}; Path=/; Max-Age={idade}; HttpOnly; Secure; SameSite=Lax"


def _build_conta(env: dict | None = None):
    env = env if env is not None else os.environ
    if conta_web is None or contas is None:
        return None
    if not env.get("NOTA_DB") or len(env.get("NOTA_SEGREDO_SESSAO", "")) < 32:
        return None
    return conta_web.Conta(
        contas.Contas(env["NOTA_DB"], env["NOTA_SEGREDO_SESSAO"].encode()),
        base=env.get("NOTA_BASE_URL", "https://nota-aberta.onrender.com"),
        env=env,
    )


def _path_original(headers: dict) -> str:
    """O Vercel reescreve o path; o original vem no header x-vercel-original-url."""
    for k, v in headers.items():
        if k.lower() == "x-vercel-original-url":
            return v.split("?")[0]
    return "/"


def _resposta(status: int, headers: list, body) -> dict:
    if isinstance(body, bytes):
        corpo = body.decode("utf-8")
    elif isinstance(body, str):
        corpo = body
    else:
        corpo = json.dumps(body, ensure_ascii=False)
    h = dict(headers)
    h["Content-Length"] = str(len(corpo.encode("utf-8")))
    return {"statusCode": status, "headers": h, "body": corpo}


def handler(request):
    method = getattr(request, "method", "GET")
    path = getattr(request, "path", "/")
    query = getattr(request, "query", None) or {}
    if isinstance(query, dict):
        q = {k: ([v] if isinstance(v, str) else list(v)) for k, v in query.items()}
    elif isinstance(query, (list, tuple)):
        q = {}
        for k, v in query:
            q.setdefault(k, []).append(v)
    else:
        q = {}
    headers_raw = getattr(request, "headers", {}) or {}
    cab = {str(k).lower(): str(v) for k, v in headers_raw.items()}
    body = getattr(request, "body", b"") or b""
    if isinstance(body, str):
        body = body.encode()

    # Path original (antes do rewrite do Vercel)
    caminho = _path_original(headers_raw)

    # --- health ---
    if caminho == "/health":
        return _resposta(200, [("Content-Type", "text/plain; charset=utf-8")], "ok")

    # --- API de cálculo: sem backend jar no Vercel ---
    if caminho.startswith("/api/"):
        return _resposta(
            502,
            [("Content-Type", "application/json; charset=utf-8")],
            {"erro": "backend de cálculo não disponível no Vercel"},
        )

    # --- rotas de conta (entrar, sair, /conta/*) ---
    if caminho.startswith(ROTAS_CONTA):
        CONTA = _build_conta()
        if CONTA is None:
            return _resposta(
                503,
                [("Content-Type", "application/json; charset=utf-8")],
                {"erro": {"codigo": "conta_desligada", "mensagem": "cadastro e emissão ainda não estão ligados neste servidor"}},
            )
        consulta = ""
        if "?" in getattr(request, "url", "") or "?" in str(getattr(request, "path", "")):
            raw_path = str(getattr(request, "path", "/"))
            if "?" in raw_path:
                consulta = raw_path.split("?", 1)[1]
        r = CONTA.tratar(method, caminho, consulta, cab, body)
        hdrs = [("Cache-Control", "no-store")]
        for c in r.cookies:
            hdrs.append(("Set-Cookie", c))
        if r.redirecionar:
            hdrs.append(("Location", r.redirecionar))
            return _resposta(r.status, hdrs, "")
        if r.bruto is not None:
            hdrs.append(("Content-Type", r.tipo))
            return _resposta(r.status, hdrs, r.bruto)
        dados = json.dumps(r.corpo, ensure_ascii=False).encode()
        hdrs.append(("Content-Type", "application/json; charset=utf-8"))
        return _resposta(r.status, hdrs, dados)

    # --- MCP ---
    if caminho == "/mcp" and method == "POST":
        try:
            corpo_json = json.loads(body or b"null")
        except ValueError:
            return _resposta(
                400,
                [("Content-Type", "application/json")],
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}},
            )

        def chamar(metodo, api, dados):
            return 502, {"erro": "motor de cálculo não disponível no Vercel"}

        conta_cb = None
        aut = cab.get("authorization", "")
        if aut.lower().startswith("bearer "):
            CONTA = _build_conta()
            if CONTA is not None:

                def cb(metodo, caminho_c, pedido):
                    corpo_conta = json.dumps(pedido).encode() if pedido is not None else b""
                    r = CONTA.tratar(metodo, caminho_c, "", cab, corpo_conta)
                    return r.status, r.corpo

                conta_cb = cb

        status, resp = mcp.responder(corpo_json, chamar, conta_cb) if mcp else (503, {"erro": "módulos de conta não disponíveis"})
        if resp is None:
            return _resposta(status, [("Content-Length", "0")], "")
        return _resposta(status, [("Content-Type", "application/json; charset=utf-8")], resp)

    # --- arquivos estáticos (fallback; o outputDirectory do Vercel já serve web/) ---
    nome = "index.html" if caminho in ("/", "") else caminho.lstrip("/")
    arq = os.path.join(ROOT, "web", nome)
    if os.path.isfile(arq):
        with open(arq, "rb") as f:
            dados = f.read()
        tipo, _ = mimetypes.guess_type(arq)
        if tipo is None:
            tipo = "application/octet-stream"
        if nome.endswith(".html"):
            tipo = "text/html; charset=utf-8"
        elif nome.endswith(".css"):
            tipo = "text/css; charset=utf-8"
        elif nome.endswith(".js"):
            tipo = "text/javascript; charset=utf-8"
        return _resposta(200, [("Content-Type", tipo)], dados)

    return _resposta(404, [("Content-Type", "application/json; charset=utf-8")], {"erro": "não encontrado"})
