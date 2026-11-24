"""Plano de cada conta: grátis por padrão, pago via AbacatePay (PIX ou boleto).

Cada plano pago tem um preço em reais/mês. A assinatura é feita via checkout
transparente da AbacatePay: o usuário escolhe PIX ou boleto, paga, e o webhook
`transparent.completed` confirma o pagamento e libera o plano.

Sem `ABACATEPAY_KEY`, a conta fica no grátis: nunca se dá plano pago por falha.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / "planos.json"
ABACATEPAY_BASE = os.environ.get("ABACATEPAY_BASE", "https://api.abacatepay.com/v2")
# Mantido para compatibilidade com testes e código legado (Genesis Pagar / Stripe)
CACHE_SEGUNDOS = 300
PAGAR = os.environ.get("PAGAR_URL", "https://pagar.genesisinnovation.io")


def link_checkout(plano: dict) -> str:
    return f"{PAGAR}/p/{plano['link_pagar']}" if plano.get("link_pagar") else ""


def carregar(arquivo: Path = ARQUIVO) -> list[dict]:
    return json.loads(arquivo.read_text(encoding="utf-8"))["planos"]


def sob_medida(arquivo: Path = ARQUIVO) -> dict:
    return json.loads(arquivo.read_text(encoding="utf-8")).get("sob_medida") or {}


def por_id(planos: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in planos}


class Planos:
    def __init__(self, planos: list[dict] | None = None, consultar=None, relogio=time.monotonic):
        self.lista = planos or carregar()
        self.gratis = self.lista[0]
        self._consultar = consultar or (lambda email: [])
        self.relogio, self._cache = relogio, {}

    def do_email(self, email: str) -> dict:
        agora = self.relogio()
        guardado = self._cache.get(email)
        if guardado and agora - guardado[0] < CACHE_SEGUNDOS:
            return guardado[1]
        acessos = self._consultar(email)
        # Suporta ambos os formatos: lista de dicts (Genesis Pagar) ou lista de IDs (AbacatePay)
        if acessos and isinstance(acessos[0], dict):
            links = {a.get("link") for a in acessos if a.get("acesso")}
            pagos = [p for p in self.lista if p.get("link_pagar") and p["link_pagar"] in links]
        else:
            pagos = [p for p in self.lista if p["id"] in (acessos or [])]
        plano = max(pagos, key=lambda p: p["preco_mes"]) if pagos else self.gratis
        self._cache[email] = (agora, plano)
        return plano

    def esquecer(self, email: str) -> None:
        self._cache.pop(email, None)

    def preco_centavos(self, plano_id: str) -> int:
        for p in self.lista:
            if p["id"] == plano_id:
                return int(round(p["preco_mes"] * 100))
        return 0
