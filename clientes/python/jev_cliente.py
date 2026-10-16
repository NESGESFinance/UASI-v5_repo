"""Cliente de referencia JEV → UASI (solo biblioteca estándar, Python ≥ 3.9).

Uso:
    from jev_cliente import ClienteJEV
    cliente = ClienteJEV("https://uasi.ejemplo.com", clave_hmac=os.environ["JEV_HMAC_KEY"], token_jwt=jwt)
    respuesta = cliente.despachar("GENERACION_BINDINGS", ["typescript", "solidity"], {"activo_id": "RWA-1"})

Protocolo de firma (idéntico al servidor):
    firma = HMAC-SHA256(clave, json.dumps(cuerpo_sin_firma, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
donde `cuerpo_sin_firma` excluye `firma_hmac` (solicitud) o `firma_respuesta` (respuesta).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

CAMPOS_FIRMA = ("firma_hmac", "firma_respuesta")


class ErrorJEV(Exception):
    """Error HTTP o de verificación devuelto al llamar a UASI."""

    def __init__(self, mensaje: str, codigo_http: Optional[int] = None, detalle: Any = None) -> None:
        super().__init__(mensaje)
        self.codigo_http = codigo_http
        self.detalle = detalle


def json_canonico(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def firmar(cuerpo: dict, clave: str) -> str:
    """HMAC-SHA256 hex del JSON canónico del cuerpo sin los campos de firma."""
    sin_firma = {k: v for k, v in cuerpo.items() if k not in CAMPOS_FIRMA}
    return hmac.new(clave.encode("utf-8"), json_canonico(sin_firma).encode("utf-8"), hashlib.sha256).hexdigest()


def verificar(cuerpo: dict, clave: str, campo: str = "firma_respuesta") -> bool:
    recibida = cuerpo.get(campo)
    return isinstance(recibida, str) and hmac.compare_digest(firmar(cuerpo, clave), recibida.lower())


def construir_solicitud(
    clave: str,
    tipo_tarea: str,
    lenguajes: list,
    datos: dict,
    metadatos: Optional[dict] = None,
    id_jev: Optional[str] = None,
    id_solicitud: Optional[str] = None,
    version_jev: str = "1.0",
    incluir_timestamp: bool = True,
) -> dict:
    """Construye y firma una SolicitudJEV. `id_solicitud` habilita la idempotencia en UASI."""
    cuerpo: dict = {
        "id_jev": id_jev or f"jev-{uuid.uuid4()}",
        "version_jev": version_jev,
        "tipo_tarea": tipo_tarea,
        "lenguajes_destino": list(lenguajes),
        "datos": datos,
        "metadatos": metadatos or {},
        "id_solicitud": id_solicitud or str(uuid.uuid4()),
    }
    if incluir_timestamp:
        cuerpo["timestamp"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017 (compatible con Python 3.9)
    cuerpo["firma_hmac"] = firmar(cuerpo, clave)
    return cuerpo


class ClienteJEV:
    """Envía solicitudes firmadas a UASI y verifica la firma de cada respuesta."""

    def __init__(self, url_base: str, clave_hmac: str, token_jwt: str, timeout: float = 30.0) -> None:
        if not clave_hmac:
            raise ValueError("clave_hmac es obligatoria")
        self.url_base = url_base.rstrip("/")
        self.clave = clave_hmac
        self.token = token_jwt
        self.timeout = timeout

    def _post(self, ruta: str, cuerpo: dict) -> dict:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        peticion = urllib.request.Request(
            self.url_base + ruta,
            data=datos,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self.timeout) as r:  # noqa: S310 (URL de configuración)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detalle = json.loads(exc.read().decode("utf-8"))
            except ValueError:
                detalle = None
            raise ErrorJEV(f"UASI respondió HTTP {exc.code}", exc.code, detalle) from exc

    def _verificada(self, respuesta: dict) -> dict:
        if not verificar(respuesta, self.clave):
            raise ErrorJEV("Firma de respuesta UASI inválida", detalle=respuesta)
        return respuesta

    def despachar(self, tipo_tarea: str, lenguajes: list, datos: dict, **opciones: Any) -> dict:
        cuerpo = construir_solicitud(self.clave, tipo_tarea, lenguajes, datos, **opciones)
        return self._verificada(self._post("/api/v5/jev-dispatch", cuerpo))

    def despachar_lote(self, solicitudes: list) -> dict:
        """`solicitudes`: cuerpos ya firmados con `construir_solicitud`."""
        resumen = self._verificada(self._post("/api/v5/jev-dispatch/lote", {"solicitudes": solicitudes}))
        for entrada in resumen.get("resultados", []):
            if entrada.get("codigo_http") == 200 and not verificar(entrada["respuesta"], self.clave):
                raise ErrorJEV(f"Firma inválida en la respuesta del elemento {entrada.get('indice')}")
        return resumen
