"""Servidor de Gemini FALSO para las pruebas: responde lo que se le indica, sin salir a internet.

Se usa con PREDIO_API_GEMINI=<url> (como PREDIO_API_GITHUB en el actualizador). Guarda cada pedido que recibe para poder revisarlo.
"""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAVE_DE_PRUEBA = "AIzaSyClaveDePruebaQueNuncaDebeAparecerEnUnLog123"


def respuesta_gemini(items) -> dict:
    """Lo que devuelve Gemini cuando se le pide JSON: el JSON va como texto dentro de candidates[0].content.parts[0].text."""
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps({"items": items}, ensure_ascii=False)}]}, "finishReason": "STOP"}]}


def error_gemini(codigo: int, estado: str, mensaje: str, detalles=None) -> dict:
    e = {"code": codigo, "message": mensaje, "status": estado}
    if detalles:
        e["details"] = detalles
    return {"error": e}


class GeminiFalso:
    def __init__(self, items=None, clave=CLAVE_DE_PRUEBA):
        self.clave = clave
        self.items = list(items or [])
        self.pedidos = []                 # [{"ruta", "cabeceras", "cuerpo"}]
        self.forzadas = []                # cola de (estado, cuerpo: dict|str, retardo) que se usan antes que la respuesta normal
        self.retardo = 0.0
        self._lock = threading.Lock()
        falso = self

        class Manejador(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):          # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                crudo = self.rfile.read(n)
                try:
                    cuerpo = json.loads(crudo)
                except ValueError:
                    cuerpo = None
                with falso._lock:
                    falso.pedidos.append({"ruta": self.path, "cabeceras": {k.lower(): v for k, v in self.headers.items()}, "cuerpo": cuerpo})
                    forzada = falso.forzadas.pop(0) if falso.forzadas else None
                    retardo = falso.retardo
                if forzada:
                    estado, cuerpo_resp, demora = forzada
                    time.sleep(demora)
                elif self.headers.get("x-goog-api-key") != falso.clave:
                    estado, cuerpo_resp = 400, error_gemini(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
                else:
                    time.sleep(retardo)
                    estado, cuerpo_resp = 200, respuesta_gemini(falso.items)
                datos = (cuerpo_resp if isinstance(cuerpo_resp, str) else json.dumps(cuerpo_resp)).encode("utf-8")
                try:
                    self.send_response(estado)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(datos)))
                    self.end_headers()
                    self.wfile.write(datos)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        ThreadingHTTPServer.daemon_threads = True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.puerto = self.httpd.server_address[1]
        self.hilo = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/v1beta"

    def iniciar(self):
        self.hilo.start()
        return self

    def parar(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def forzar(self, estado, cuerpo, demora=0.0):
        self.forzadas.append((estado, cuerpo, demora))

    # ---- para revisar lo que llegó ----
    def ultimo(self) -> dict:
        return self.pedidos[-1]

    def imagen_recibida(self) -> bytes:
        partes = self.ultimo()["cuerpo"]["contents"][0]["parts"]
        return base64.b64decode(next(p["inline_data"]["data"] for p in partes if "inline_data" in p))

    def prompt_recibido(self) -> str:
        partes = self.ultimo()["cuerpo"]["contents"][0]["parts"]
        return next(p["text"] for p in partes if "text" in p)
