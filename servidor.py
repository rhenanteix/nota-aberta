"""nota-aberta: a calculadora oficial da Reforma Tributária do Consumo (CBS, IBS e IS) aberta ao público.

O cálculo NÃO é nosso: é o motor que a Receita Federal publicou em MIT
(github.com/ReceitaFederal/calculadora-rtc-regime-regular), compilado sem alteração no Dockerfile
a partir de um commit fixo. Reescrever a fórmula seria uma segunda cópia da regra fiscal, que
diverge da oficial na primeira atualização; rodando o jar deles, o número é o deles por construção.

Este processo é só a porta:
  - sobe o jar oficial em 127.0.0.1:8081 e morre junto se ele morrer (o pod reinicia);
  - serve a página (web/) em / e a sonda em /health;
  - repassa /api/* ao jar, só nas rotas de leitura e de cálculo (lista PERMITIDAS), com corpo
    limitado e teto de pedidos por IP, porque o serviço é público e sem login.

Só biblioteca padrão: a imagem já carrega uma JVM, não precisa de mais nada.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mcp

AQUI = Path(__file__).resolve().parent
WEB = AQUI / "web"
BACKEND = os.environ.get("RTC_BACKEND", "http://127.0.0.1:8081")
PORTA = int(os.environ.get("PORT", "8080"))
CORPO_MAX = 256 * 1024          # uma nota com centenas de itens cabe; um upload de lixo não
TETO_POR_MINUTO = int(os.environ.get("RTC_TETO_MINUTO", "120"))
TEMPO_BACKEND = 30

# (método, regex do caminho depois de /api). O resto do jar (actuator, xml de exemplo) não sai.
PERMITIDAS = [
    ("GET", re.compile(r"^/calculadora/dados-abertos/[A-Za-z0-9/_\-]+$")),
    ("GET", re.compile(r"^/calculadora/xml/[A-Za-z0-9/_\-]+$")),
    ("POST", re.compile(r"^/calculadora/(observabilidade/)?regime-geral$")),
    ("POST", re.compile(r"^/calculadora/base-calculo/[A-Za-z0-9_\-]+$")),
    ("POST", re.compile(r"^/calculadora/nfse/[A-Za-z0-9/_\-]+$")),
    ("POST", re.compile(r"^/calculadora/xml/(generate|validate)$")),
    ("GET", re.compile(r"^/(swagger-ui\.html|swagger-ui/[A-Za-z0-9._\-/]+|api-docs(/[A-Za-z0-9._\-]+)?)$")),
]
TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8",
         ".json": "application/json", ".ico": "image/x-icon", ".xml": "application/xml"}
CABECALHOS_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}
# A página só carrega o próprio JS/CSS; o Swagger do jar usa script e estilo inline, por isso
# a CSP estrita vale só para a nossa página.
CSP_PAGINA = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "connect-src 'self' https://viacep.com.br; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def permitida(metodo: str, caminho_api: str) -> bool:
    if ".." in caminho_api or "//" in caminho_api:
        return False
    return any(m == metodo and r.match(caminho_api) for m, r in PERMITIDAS)


class Teto:
    """Janela de 60 s por IP. Em memória: um pod só, e reiniciar zera (aceitável para um teto)."""

    def __init__(self, por_minuto: int, relogio=time.monotonic):
        self.por_minuto, self.relogio, self.janelas, self.trava = por_minuto, relogio, {}, threading.Lock()

    def aceita(self, ip: str) -> bool:
        agora = self.relogio()
        with self.trava:
            inicio, n = self.janelas.get(ip, (agora, 0))
            if agora - inicio >= 60:
                inicio, n = agora, 0
            if n >= self.por_minuto:
                self.janelas[ip] = (inicio, n)
                return False
            self.janelas[ip] = (inicio, n + 1)
            if len(self.janelas) > 50_000:  # não deixa a tabela crescer sem fim
                self.janelas = {k: v for k, v in self.janelas.items() if agora - v[0] < 60}
            return True


TETO = Teto(TETO_POR_MINUTO)
CONTA = None  # conta_web.Conta quando o servidor tem banco e segredo (montar_conta); sem eles, só a calculadora
MOTOR_PRONTO = False
ROTAS_CONTA = ("/entrar/", "/sair", "/conta/")


def montar_conta(env: dict | None = None):
    """A conta só sobe com `NOTA_DB` (arquivo SQLite no disco persistente) e `NOTA_SEGREDO_SESSAO` (32+ bytes)."""
    env = env if env is not None else os.environ
    if not env.get("NOTA_DB") or len(env.get("NOTA_SEGREDO_SESSAO", "")) < 32:
        return None
    import conta_web
    import contas
    return conta_web.Conta(contas.Contas(env["NOTA_DB"], env["NOTA_SEGREDO_SESSAO"].encode()),
                           base=env.get("NOTA_BASE_URL", "https://nota-aberta.onrender.com"), env=env)


def chamar_motor(metodo: str, api: str, dados: bytes | None) -> tuple[int, object]:
    """Uma chamada ao jar oficial, devolvida como (status, JSON). Usada pelo MCP; a API repassa os bytes crus."""
    req = urllib.request.Request(BACKEND + "/api" + api, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_BACKEND) as r:
            status, corpo = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, corpo = e.code, e.read()
    except (urllib.error.URLError, OSError):
        return 502, None
    try:
        return status, json.loads(corpo or b"null")
    except ValueError:
        return status, {"bruto": corpo[:500].decode("utf-8", "replace")}


def backend_vivo() -> bool:
    try:
        with urllib.request.urlopen(BACKEND + "/api/calculadora/dados-abertos/versao", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


class Porta(BaseHTTPRequestHandler):
    server_version = "nota-aberta"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    # Sem timeout, sockets que mandam meio cabeçalho seguram uma thread cada para sempre (slowloris; medido no
    # agenda-clinica, que é o mesmo servidor da biblioteca padrão).
    timeout = 30

    def log_message(self, fmt, *args):  # sem IP no log: não precisamos dele guardado
        sys.stderr.write(f"{self.command} {self.path.split('?')[0]}\n")

    def _ip(self) -> str:
        return (self.headers.get("CF-Connecting-IP") or self.client_address[0] or "?").strip()

    def _enviar(self, status: int, corpo: bytes, tipo: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in {**CABECALHOS_SEGURANCA, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, status: int, obj) -> None:
        self._enviar(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                     {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        caminho = self.path.split("?")[0]
        if caminho == "/health":
            ok = MOTOR_PRONTO
            sys.stderr.write(f"HEALTH CHECK: {'OK' if ok else 'FAIL'} motor_pronto={ok}\n")
            return self._enviar(200 if ok else 503, b"ok" if ok else b"motor fora", "text/plain; charset=utf-8",
                                {"Cache-Control": "no-store"})
        if caminho.startswith("/api/"):
            return self._repassar()
        if caminho.startswith(ROTAS_CONTA):
            return self._conta()
        return self._estatico(caminho)

    def do_POST(self):
        caminho = self.path.split("?")[0]
        if caminho.startswith("/api/"):
            return self._repassar()
        if caminho == "/mcp":
            return self._mcp()
        if caminho.startswith(ROTAS_CONTA):
            return self._conta()
        self._json(405, {"erro": "método não permitido"})

    def do_DELETE(self):
        if self.path.split("?")[0].startswith("/conta/"):
            return self._conta()
        self._json(405, {"erro": "método não permitido"})

    def _conta(self):
        if CONTA is None:
            return self._json(503, {"erro": {"codigo": "conta_desligada", "mensagem": "cadastro e emissão ainda não estão ligados neste servidor"}})
        if not TETO.aceita(self._ip()):
            return self._json(429, {"erro": f"mais de {TETO.por_minuto} pedidos por minuto deste IP"})
        try:
            n = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return self._json(400, {"erro": "Content-Length inválido"})
        if n > CORPO_MAX:
            return self._json(413, {"erro": f"corpo acima de {CORPO_MAX // 1024} KB"})
        corpo = self.rfile.read(n) if n else b""
        caminho, _, consulta = self.path.partition("?")
        cab = {k.lower(): v for k, v in self.headers.items()}
        r = CONTA.tratar(self.command, caminho, consulta, cab, corpo)
        self.send_response(r.status)
        for c in r.cookies:
            self.send_header("Set-Cookie", c)
        for k, v in {**CABECALHOS_SEGURANCA, "Cache-Control": "no-store"}.items():
            self.send_header(k, v)
        if r.bruto is not None:
            self.send_header("Content-Type", r.tipo)
            self.send_header("Content-Length", str(len(r.bruto)))
            self.end_headers()
            self.wfile.write(r.bruto)
            return None
        if r.redirecionar:
            self.send_header("Location", r.redirecionar)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        dados = json.dumps(r.corpo, ensure_ascii=False).encode()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)
        return None

    def _mcp(self):
        try:
            n = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return self._json(400, {"erro": "Content-Length inválido"})
        if n > CORPO_MAX:
            return self._json(413, {"erro": f"corpo acima de {CORPO_MAX // 1024} KB"})
        try:
            corpo = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}})
        ip = self._ip()

        def chamar(metodo: str, api: str, dados: bytes | None):
            # O teto vale por chamada ao motor, não por requisição: um lote de MCP não dribla o limite por IP.
            if not TETO.aceita(ip):
                return 429, {"erro": f"mais de {TETO.por_minuto} pedidos por minuto deste IP"}
            return chamar_motor(metodo, api, dados)

        conta = None
        aut = self.headers.get("Authorization", "")
        if CONTA is not None and aut.lower().startswith("bearer "):
            cab_conta = {"authorization": aut}

            def conta(metodo: str, caminho: str, pedido: dict | None):
                # Mesmo teto por IP da API: cada chamada à conta pelo MCP conta como um pedido.
                if not TETO.aceita(ip):
                    return 429, {"erro": f"mais de {TETO.por_minuto} pedidos por minuto deste IP"}
                corpo_conta = json.dumps(pedido).encode() if pedido is not None else b""
                r = CONTA.tratar(metodo, caminho, "", cab_conta, corpo_conta)
                return r.status, r.corpo

        status, resp = mcp.responder(corpo, chamar, conta)
        if resp is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._json(status, resp)

    def _estatico(self, caminho: str):
        nome = "index.html" if caminho in ("/", "") else caminho.lstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+(\.[A-Za-z0-9]+)", nome):
            return self._json(404, {"erro": "não encontrado"})
        arq = WEB / nome
        if not arq.is_file():
            return self._json(404, {"erro": "não encontrado"})
        extra = {"Cache-Control": "no-cache"}
        if arq.suffix == ".html":
            extra["Content-Security-Policy"] = CSP_PAGINA
        self._enviar(200, arq.read_bytes(), TIPOS.get(arq.suffix, "application/octet-stream"), extra)

    def _repassar(self):
        caminho, _, consulta = self.path.partition("?")
        api = caminho[len("/api"):]
        if not permitida("GET" if self.command == "HEAD" else self.command, api):
            return self._json(404, {"erro": "rota fora da API pública", "rota": api})
        if not TETO.aceita(self._ip()):
            return self._json(429, {"erro": f"mais de {TETO.por_minuto} pedidos por minuto deste IP; espere um minuto"})
        corpo = None
        if self.command == "POST":
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return self._json(400, {"erro": "Content-Length inválido"})
            if n > CORPO_MAX:
                return self._json(413, {"erro": f"corpo acima de {CORPO_MAX // 1024} KB"})
            corpo = self.rfile.read(n)
        req = urllib.request.Request(BACKEND + "/api" + api + (("?" + consulta) if consulta else ""),
                                     data=corpo, method=self.command)
        for h in ("Content-Type", "Accept"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=TEMPO_BACKEND) as r:
                status, dados, tipo = r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, dados, tipo = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, OSError):
            return self._json(502, {"erro": "o motor de cálculo não respondeu; tente de novo em instantes"})
        extra = {"Cache-Control": "public, max-age=3600" if "/dados-abertos/" in api and status == 200 else "no-store",
                 "Access-Control-Allow-Origin": "*"}
        self._enviar(status, dados, tipo, extra)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST",
                     "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400",
                     "Content-Length": "0"}.items():
            self.send_header(k, v)
        self.end_headers()


def subir_motor() -> subprocess.Popen | None:
    """Sobe o jar oficial. RTC_JAR vazio = motor externo (testes, dev)."""
    jar = os.environ.get("RTC_JAR", "")
    if not jar:
        return None
    flags = os.environ.get("RTC_JAVA_OPTS", "").split()
    return subprocess.Popen(["java", *flags, "-jar", jar, "--spring.profiles.active=offline",
                             "--server.port=8081", "--server.address=127.0.0.1"], cwd=os.environ.get("RTC_DIR", "/rtc"))


def subir_danfse() -> subprocess.Popen | None:
    """DANFSe Service da Receita (Go) em 127.0.0.1:8093, se a imagem o trouxer (RTC_DANFSE)."""
    binario = os.environ.get("RTC_DANFSE", "")
    if not binario:
        return None
    env = {**os.environ, "SERVER_PORT": "8093", "OTEL_SDK_DISABLED": "true"}
    os.environ.setdefault("DANFSE_URL", "http://127.0.0.1:8093")
    return subprocess.Popen([binario], env=env)


def vigiar(motor: subprocess.Popen, servidor: ThreadingHTTPServer) -> None:
    motor.wait()
    sys.stderr.write(f"motor saiu com código {motor.returncode}; encerrando para o pod reiniciar\n")
    servidor.shutdown()


def main() -> int:
    global CONTA, MOTOR_PRONTO
    sys.stderr.write(f"INICIANDO nota-aberta PORT={PORTA} BACKEND={BACKEND} RTC_JAR={os.environ.get('RTC_JAR', '')}\n")
    danfse = subir_danfse()
    CONTA = montar_conta()
    motor = subir_motor()
    if motor:
        def esperar_motor():
            global MOTOR_PRONTO
            for i in range(60):
                if backend_vivo():
                    sys.stderr.write(f"motor vivo após {i+1}s\n")
                    MOTOR_PRONTO = True
                    return
                time.sleep(1)
            sys.stderr.write("motor não subiu a tempo\n")
        threading.Thread(target=esperar_motor, daemon=True).start()
    else:
        sys.stderr.write("AVISO: RTC_JAR vazio, motor não iniciado\n")
        MOTOR_PRONTO = True
    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Porta)
    servidor.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=servidor.shutdown).start())
    if motor:
        threading.Thread(target=vigiar, args=(motor, servidor), daemon=True).start()
    sys.stderr.write(f"nota-aberta em :{PORTA}, motor em {BACKEND}\n")
    servidor.serve_forever()
    if danfse and danfse.poll() is None:
        danfse.terminate()
    if motor and motor.poll() is None:
        motor.terminate()
        try:
            motor.wait(10)
        except subprocess.TimeoutExpired:
            motor.kill()
    return 1 if motor and motor.returncode not in (None, 0, 143) else 0


if __name__ == "__main__":
    sys.exit(main())
