"""Integración de solo lectura con la plataforma NESGESFinanceTrust.

UASI *observa* (nunca escribe) el estado on-chain del ecosistema NESGESFinance a
través de la API REST pública de NESGESFinanceTrust: la runa NGF•BTC•AM (Runes),
las inscripciones Ordinals y los activos tokenizados del mundo real (RWA) con su
cumplimiento MiCA.

El cliente es DEFENSIVO por diseño: si la plataforma no responde (red, DNS, 5xx,
timeout), cada método devuelve ``{"conectado": False, "error": ...}`` en lugar de
propagar la excepción, para que la API de UASI permanezca operativa aunque el
sistema externo esté caído o aún no desplegado.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from uasi_v5.core.config import (
    NGF_TRUST_API_URL,
    NGF_TRUST_RUNA_PRINCIPAL,
    NGF_TRUST_TIMEOUT_SECONDS,
)
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc

# Clases de activos bajo el marco MiCA (estáticas: definen el modelo de cumplimiento).
CLASES_MICA: tuple[dict[str, str], ...] = (
    {"clase": "EMT", "descripcion": "E-Money Token (referenciado a dinero fiat)"},
    {"clase": "ART", "descripcion": "Asset-Referenced Token (cesta de activos)"},
    {"clase": "UTILITY", "descripcion": "Token de utilidad fungible (Runes)"},
    {"clase": "SECURITY", "descripcion": "Token de valor no fungible (Ordinals)"},
)


class ClienteNESGESFinanceTrust:
    """Cliente HTTP asíncrono y defensivo para la API de NESGESFinanceTrust."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or NGF_TRUST_API_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else NGF_TRUST_TIMEOUT_SECONDS
        # ``transport`` permite inyectar httpx.MockTransport en las pruebas.
        self._transport = transport

    async def _get(self, ruta: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET defensivo: nunca lanza; devuelve {conectado, datos, error}."""
        url = f"{self.base_url}{ruta}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport, follow_redirects=True
            ) as cliente:
                respuesta = await cliente.get(url, params=params)
            respuesta.raise_for_status()
            return {"conectado": True, "datos": respuesta.json(), "error": None}
        except Exception as exc:  # noqa: BLE001 - degradación controlada ante cualquier fallo de red
            logger.warning("NESGESFinanceTrust no accesible (%s): %s", ruta, exc)
            return {"conectado": False, "datos": None, "error": str(exc)}

    async def salud(self) -> Dict[str, Any]:
        """GET /health de la plataforma."""
        return await self._get("/health")

    async def runes(self, limite: int = 20) -> Dict[str, Any]:
        """GET /runes (listado de runas indexadas)."""
        return await self._get("/runes", params={"limit": limite})

    async def runa_principal(self) -> Dict[str, Any]:
        """GET /runes/:name de la runa principal del ecosistema (NGF•BTC•AM)."""
        return await self._get(f"/runes/{NGF_TRUST_RUNA_PRINCIPAL}")

    async def activos_rwa(self, limite: int = 20) -> Dict[str, Any]:
        """GET /rwa/assets (activos del mundo real tokenizados)."""
        return await self._get("/rwa/assets", params={"limit": limite})

    @staticmethod
    def _resumen_rwa(datos: Any) -> Dict[str, Any]:
        """Resume una respuesta de activos RWA (lista o {assets:[...]}) sin fallar."""
        activos: List[Any] = []
        if isinstance(datos, list):
            activos = datos
        elif isinstance(datos, dict):
            for clave in ("assets", "activos", "items", "data"):
                valor = datos.get(clave)
                if isinstance(valor, list):
                    activos = valor
                    break
        clases: Dict[str, int] = {}
        for activo in activos:
            if isinstance(activo, dict):
                clase = str(activo.get("class") or activo.get("clase") or activo.get("tipo") or "DESCONOCIDA")
                clases[clase] = clases.get(clase, 0) + 1
        return {"total": len(activos), "por_clase": clases, "muestra": activos[:5]}

    async def resumen(self) -> Dict[str, Any]:
        """Vista agregada y legible del estado on-chain para el panel de UASI.

        Combina salud, runa principal y activos RWA en un único objeto. Siempre
        responde (``conectado`` indica si la plataforma externa está accesible).
        """
        salud = await self.salud()
        runa = await self.runa_principal()
        rwa = await self.activos_rwa()
        conectado = bool(salud["conectado"] or runa["conectado"] or rwa["conectado"])
        errores = [
            f"health: {salud['error']}" if not salud["conectado"] else None,
            f"runa: {runa['error']}" if not runa["conectado"] else None,
            f"rwa: {rwa['error']}" if not rwa["conectado"] else None,
        ]
        return {
            "conectado": conectado,
            "api_url": self.base_url,
            "timestamp": ahora_utc().isoformat(),
            "runa_principal_nombre": NGF_TRUST_RUNA_PRINCIPAL,
            "salud": salud["datos"],
            "runa_principal": runa["datos"],
            "activos_rwa": self._resumen_rwa(rwa["datos"]) if rwa["conectado"] else None,
            "cumplimiento_mica": list(CLASES_MICA),
            "errores": [e for e in errores if e],
        }


def obtener_cliente_trust() -> ClienteNESGESFinanceTrust:
    """Dependencia FastAPI: devuelve un cliente nuevo (sobreescribible en pruebas)."""
    return ClienteNESGESFinanceTrust()
