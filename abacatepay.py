"""Integração com a AbacatePay (checkout transparente: PIX e boleto).

API: https://api.abacatepay.com/v2
Auth: Bearer <api-key>
Criar cobrança: POST /transparents/create
Webhook: transparent.completed
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_BASE = os.environ.get("ABACATEPAY_BASE", "https://api.abacatepay.com/v2")


def _chave() -> str:
    return os.environ.get("ABACATEPAY_KEY", "")


def _request(metodo: str, caminho: str, corpo: dict | None = None) -> tuple[int, dict]:
    url = API_BASE + caminho
    data = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=data, method=metodo,
                                 headers={"Authorization": f"Bearer {_chave()}",
                                          "Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "User-Agent": "nota-aberta/1.0 (Python)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": str(e)}
    except (urllib.error.URLError, OSError):
        return 502, {"error": "abacatepay indisponível"}


def criar_cobranca_pix(amount: int, external_id: str, descricao: str = "", customer: dict | None = None, metadata: dict | None = None) -> dict:
    """Cria cobrança PIX. amount em centavos. Retorna dict com id, brCode, brCodeBase64, status, etc."""
    corpo = {"method": "PIX", "data": {"amount": amount}}
    if descricao:
        corpo["data"]["description"] = descricao
    if external_id:
        corpo["data"]["externalId"] = external_id
    if customer:
        corpo["data"]["customer"] = customer
    if metadata:
        corpo["data"]["metadata"] = metadata
    st, resp = _request("POST", "/transparents/create", corpo)
    if st >= 400:
        raise RuntimeError(f"abacatepay pix erro {st}: {resp}")
    return resp.get("data", resp)


def criar_cobranca_boleto(amount: int, external_id: str, descricao: str = "", customer: dict | None = None, metadata: dict | None = None, due_date: str | None = None) -> dict:
    """Cria cobrança boleto. amount em centavos. Retorna dict com id, barCode, url, brCode, brCodeBase64, etc."""
    corpo = {"method": "BOLETO", "data": {"amount": amount}}
    if descricao:
        corpo["data"]["description"] = descricao
    if external_id:
        corpo["data"]["externalId"] = external_id
    if customer:
        corpo["data"]["customer"] = customer
    if metadata:
        corpo["data"]["metadata"] = metadata
    if due_date:
        corpo["data"]["dueDate"] = due_date
    st, resp = _request("POST", "/transparents/create", corpo)
    if st >= 400:
        raise RuntimeError(f"abacatepay boleto erro {st}: {resp}")
    return resp.get("data", resp)


def verificar_cobranca(cobranca_id: str) -> dict:
    """Verifica status de uma cobrança. Retorna dict com status, etc."""
    st, resp = _request("GET", f"/transparents/get?id={cobranca_id}")
    if st >= 400:
        raise RuntimeError(f"abacatepay get erro {st}: {resp}")
    return resp.get("data", resp)


def listar_cobrancas() -> list:
    """Lista cobranças transparentes."""
    st, resp = _request("GET", "/transparents/list")
    if st >= 400:
        raise RuntimeError(f"abacatepay list erro {st}: {resp}")
    return resp.get("data", [])
