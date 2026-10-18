"""Raíz de Merkle diaria y anclaje en Bitcoin L1 vía OpenTimestamps [P3-02..04]."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from filelock import FileLock
from starlette.concurrency import run_in_threadpool

from uasi_v5.auditoria.ledger import LedgerAuditoriaCriptografica
from uasi_v5.core.config import (
    BITCOIN_NETWORK,
    ESPLORA_API_URL,
    OTS_CALENDARS,
    OTS_MIN_CALENDARS,
    OTS_TIMEOUT_SECONDS,
    PATH_OTS,
    VERSION_UASI,
)
from uasi_v5.core.excepciones import ErrorValidacionDatos
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.persistencia.almacen_modelos import _LOCK_PROCESO, _escritura_atomica_texto

# --- OpenTimestamps (fallback gracioso si no está instalada) -----------------
OTS_DISPONIBLE = False
OTS_ERROR_IMPORT: Optional[str] = None
try:
    try:  # Compatibilidad: algunos entornos exponen un wrapper "api"
        import opentimestamps.api as ots_api  # type: ignore  # noqa: F401
    except ImportError:
        ots_api = None  # La librería oficial no incluye este módulo
    from opentimestamps.calendar import (
        DEFAULT_CALENDAR_WHITELIST,
        CommitmentNotFoundError,
        RemoteCalendar,
    )
    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation,
        PendingAttestation,
    )
    from opentimestamps.core.op import OpAppend, OpSHA256
    from opentimestamps.core.serialize import (
        StreamDeserializationContext,
        StreamSerializationContext,
    )
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    OTS_DISPONIBLE = True
except Exception as _exc:  # pragma: no cover - depende del entorno
    OTS_ERROR_IMPORT = f"{type(_exc).__name__}: {_exc}"

try:
    import httpx  # Para contrastar cabeceras de bloque con un explorador público

    HTTPX_DISPONIBLE = True
except ImportError:  # pragma: no cover
    HTTPX_DISPONIBLE = False


class MerkleOpenTimestampsManager:
    """
    Raíz de Merkle diaria + anclaje en Bitcoin L1 mediante OpenTimestamps.

    Árbol de Merkle (estilo RFC 6962, con separación de dominio):
        hoja  = SHA256(0x00 || hash_bloque)
        nodo  = SHA256(0x01 || izq || der)
        nodo impar en un nivel ⇒ se promociona sin duplicar (evita CVE-2012-2459)

    Flujo de anclaje:
        1. La raíz (32 bytes) es el "digest" de un DetachedTimestampFile.
        2. Se añade un nonce aleatorio (privacidad) y se aplica SHA256.
        3. El resultado se envía en paralelo (asyncio) a varios calendarios OTS.
        4. Los calendarios agregan miles de compromisos y los publican en una
           transacción Bitcoin con OP_RETURN (≈ cada pocas horas).
        5. Se guarda el .ots (prueba "pendiente"). Tras la confirmación,
           /verify-ots hace "upgrade" y obtiene la atestación de bloque Bitcoin.
    """

    PATRON_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def __init__(self, ledger_ref: LedgerAuditoriaCriptografica, directorio: str = PATH_OTS) -> None:
        self.ledger = ledger_ref
        self.directorio = directorio

    # --- Merkle -------------------------------------------------------------
    @staticmethod
    def calcular_raiz_merkle(bloques: List[Dict[str, Any]]) -> str:
        if not bloques:
            raise ErrorValidacionDatos("No hay bloques para calcular la raíz de Merkle.")
        nivel = [hashlib.sha256(b"\x00" + bytes.fromhex(b["hash_bloque"])).digest() for b in bloques]
        while len(nivel) > 1:
            siguiente = [hashlib.sha256(b"\x01" + nivel[i] + nivel[i + 1]).digest() for i in range(0, len(nivel) - 1, 2)]
            if len(nivel) % 2 == 1:
                siguiente.append(nivel[-1])
            nivel = siguiente
        return nivel[0].hex()

    def _rutas(self, fecha: str) -> Tuple[str, str]:
        if not self.PATRON_FECHA.match(fecha):  # evita path traversal
            raise ErrorValidacionDatos("Fecha inválida; formato esperado YYYY-MM-DD.")
        datetime.strptime(fecha, "%Y-%m-%d")
        return os.path.join(self.directorio, f"{fecha}.ots"), os.path.join(self.directorio, f"{fecha}.json")

    @staticmethod
    def _estado_no_disponible() -> Dict[str, Any]:
        return {"estado": "opentimestamps_no_disponible",
                "detalle": f"Instale 'opentimestamps' para anclar en Bitcoin. Error de importación: {OTS_ERROR_IMPORT}"}

    @staticmethod
    def _url_permitida(url: str) -> bool:
        return url in OTS_CALENDARS or url in DEFAULT_CALENDAR_WHITELIST  # type: ignore[operator]

    @staticmethod
    def _serializar(dtf: Any, ruta: str) -> None:
        tmp = f"{ruta}.tmp-{uuid4().hex}"
        with open(tmp, "wb") as f:
            dtf.serialize(StreamSerializationContext(f))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)

    @staticmethod
    def _deserializar(ruta: str) -> Any:
        with open(ruta, "rb") as f:
            return DetachedTimestampFile.deserialize(StreamDeserializationContext(f))

    # --- Anclaje ------------------------------------------------------------
    async def anclar_en_bitcoin(self, raiz_merkle: str, fecha: str, n_bloques: Optional[int] = None) -> Dict[str, Any]:
        if not OTS_DISPONIBLE:
            return {**self._estado_no_disponible(), "raiz_merkle": raiz_merkle, "fecha": fecha}
        ruta_ots, ruta_meta = self._rutas(fecha)
        os.makedirs(self.directorio, exist_ok=True)

        digest = bytes.fromhex(raiz_merkle)
        dtf = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
        tip = dtf.timestamp.ops.add(OpAppend(os.urandom(16))).ops.add(OpSHA256())

        async def enviar(url: str) -> Tuple[str, Any]:
            calendario = RemoteCalendar(url, user_agent=f"UASI/{VERSION_UASI}")
            return url, await asyncio.to_thread(calendario.submit, tip.msg, OTS_TIMEOUT_SECONDS)

        resultados = await asyncio.gather(*(enviar(u) for u in OTS_CALENDARS), return_exceptions=True)
        aceptados, errores = [], []
        for url, res in zip(OTS_CALENDARS, resultados, strict=False):
            if isinstance(res, BaseException):
                errores.append({"calendario": url, "error": f"{type(res).__name__}: {str(res)[:200]}"})
            else:
                tip.merge(res[1])
                aceptados.append(url)

        if len(aceptados) < OTS_MIN_CALENDARS:
            logger.error("anclaje_ots_fallido", extra={"fecha": fecha, "errores": errores})
            return {"estado": "error_anclaje", "raiz_merkle": raiz_merkle, "fecha": fecha,
                    "calendarios_aceptados": aceptados, "errores": errores,
                    "detalle": f"Se requieren al menos {OTS_MIN_CALENDARS} calendario(s) OTS."}

        sellado = ahora_utc().isoformat()
        meta = {"fecha": fecha, "raiz_merkle": raiz_merkle, "n_bloques": n_bloques, "sellado_en": sellado,
                "calendarios": aceptados, "red_bitcoin_configurada": BITCOIN_NETWORK, "version": VERSION_UASI}
        def persistir() -> None:
            # Bloqueante (locks + fsync) → se ejecuta en un hilo, nunca en el event loop
            with _LOCK_PROCESO, FileLock(f"{ruta_ots}.lock", timeout=60):
                self._serializar(dtf, ruta_ots)
                # Histórico inmutable de cada anclaje del día (la raíz cambia al crecer el ledger)
                sufijo = sellado.replace(":", "").replace("-", "").split(".")[0].split("+")[0]
                self._serializar(dtf, os.path.join(self.directorio, f"{fecha}_{sufijo}.ots"))
                _escritura_atomica_texto(ruta_meta, json.dumps(meta, indent=2, ensure_ascii=False))

        await asyncio.to_thread(persistir)
        logger.info("anclaje_ots_enviado", extra={"fecha": fecha, "raiz": raiz_merkle, "calendarios": aceptados})
        return {"estado": "pendiente_confirmacion_bitcoin", **meta, "archivo_ots": os.path.basename(ruta_ots),
                "errores_calendarios": errores,
                "nota": "Los calendarios publican la transacción OP_RETURN en Bitcoin en pocas horas; "
                        "consulte /api/v5/verify-ots/{fecha} para obtener la atestación de bloque."}

    # --- Verificación -------------------------------------------------------
    def _upgrade(self, timestamp: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        """Consulta a los calendarios las atestaciones pendientes (bloqueante → threadpool)."""
        def con_atestaciones(ts: Any):
            if ts.attestations:
                yield ts
            for sub in ts.ops.values():
                yield from con_atestaciones(sub)

        cambiado, incidencias = False, []
        for sub in list(con_atestaciones(timestamp)):
            for att in list(sub.attestations):
                if not isinstance(att, PendingAttestation):
                    continue
                if not self._url_permitida(att.uri):
                    incidencias.append({"calendario": att.uri, "estado": "no_en_lista_blanca"})
                    continue
                try:
                    mejorado = RemoteCalendar(att.uri, user_agent=f"UASI/{VERSION_UASI}").get_timestamp(
                        sub.msg, timeout=OTS_TIMEOUT_SECONDS)
                    sub.merge(mejorado)
                    cambiado = True
                except CommitmentNotFoundError:
                    incidencias.append({"calendario": att.uri, "estado": "aun_pendiente"})
                except Exception as exc:  # red, timeout, etc.
                    incidencias.append({"calendario": att.uri, "estado": f"error: {type(exc).__name__}"})
        return cambiado, incidencias

    @staticmethod
    def _contrastar_bloque(altura: int, merkle_root_esperada: str) -> Dict[str, Any]:
        """Compara la merkle root del bloque Bitcoin (vía Esplora) con la de la prueba."""
        if not HTTPX_DISPONIBLE:
            return {"contrastado": False, "detalle": "httpx no disponible"}
        try:
            with httpx.Client(timeout=OTS_TIMEOUT_SECONDS) as cliente:
                r = cliente.get(f"{ESPLORA_API_URL}/block-height/{altura}")
                r.raise_for_status()
                hash_bloque = r.text.strip()
                info = cliente.get(f"{ESPLORA_API_URL}/block/{hash_bloque}")
                info.raise_for_status()
                datos = info.json()
            return {
                "contrastado": True,
                "explorador": ESPLORA_API_URL,
                "hash_bloque": hash_bloque,
                "coincide_merkle_root": datos.get("merkle_root") == merkle_root_esperada,
                "fecha_bloque_utc": datetime.fromtimestamp(int(datos["timestamp"]), UTC).isoformat(),
            }
        except Exception as exc:
            return {"contrastado": False, "detalle": f"{type(exc).__name__}: {str(exc)[:200]}"}

    async def verificar_prueba_ots(self, archivo_ots: str) -> Dict[str, Any]:
        if not OTS_DISPONIBLE:
            return self._estado_no_disponible()
        if not os.path.exists(archivo_ots):
            raise FileNotFoundError(archivo_ots)
        ruta_meta = archivo_ots[: -len(".ots")] + ".json"
        meta: Dict[str, Any] = {}
        if os.path.exists(ruta_meta):
            with open(ruta_meta, encoding="utf-8") as f:
                meta = json.load(f)

        dtf = await asyncio.to_thread(self._deserializar, archivo_ots)
        digest_prueba = dtf.file_digest.hex()

        # 1) Coherencia ledger ↔ prueba: se recalcula la raíz con los mismos n bloques
        coherencia: Dict[str, Any] = {"raiz_en_prueba": digest_prueba}
        if meta.get("fecha"):
            bloques = await run_in_threadpool(self.ledger.bloques_de_fecha, meta["fecha"])
            n = meta.get("n_bloques") or len(bloques)
            if len(bloques) >= n and n > 0:
                raiz_recalculada = self.calcular_raiz_merkle(bloques[:n])
                coherencia.update({"raiz_recalculada_desde_ledger": raiz_recalculada, "n_bloques": n,
                                   "coincide": raiz_recalculada == digest_prueba == meta.get("raiz_merkle")})
            else:
                coherencia.update({"coincide": False, "detalle": "El ledger tiene menos bloques que los anclados."})

        # 2) Upgrade contra calendarios (bloqueante → hilo)
        cambiado, incidencias = await asyncio.to_thread(self._upgrade, dtf.timestamp)
        if cambiado:
            def persistir() -> None:
                with _LOCK_PROCESO, FileLock(f"{archivo_ots}.lock", timeout=60):
                    self._serializar(dtf, archivo_ots)

            await asyncio.to_thread(persistir)

        # 3) Inventario de atestaciones y contraste con la cadena de bloques
        atestaciones: List[Dict[str, Any]] = []
        confirmada = contrastada_ok = False
        for msg, att in dtf.timestamp.all_attestations():
            if isinstance(att, BitcoinBlockHeaderAttestation):
                confirmada = True
                esperado = msg[::-1].hex()  # Esplora muestra la merkle root en orden de visualización
                item = {"tipo": "bitcoin_block_header", "altura_bloque": att.height, "merkle_root_bloque": esperado}
                item["contraste_explorador"] = await asyncio.to_thread(self._contrastar_bloque, att.height, esperado)
                contrastada_ok = contrastada_ok or bool(item["contraste_explorador"].get("coincide_merkle_root"))
                atestaciones.append(item)
            elif isinstance(att, PendingAttestation):
                atestaciones.append({"tipo": "pendiente", "calendario": att.uri})
            else:
                atestaciones.append({"tipo": type(att).__name__})

        if confirmada and contrastada_ok:
            estado = "confirmada_en_bitcoin_y_contrastada"
        elif confirmada:
            estado = "confirmada_en_bitcoin (contraste con explorador no disponible)"
        else:
            estado = "pendiente_confirmacion_bitcoin"
        return {"estado": estado, "archivo_ots": os.path.basename(archivo_ots), "metadatos_anclaje": meta,
                "coherencia_ledger": coherencia, "prueba_actualizada": cambiado,
                "atestaciones": atestaciones, "incidencias_calendarios": incidencias,
                "red_bitcoin_configurada": BITCOIN_NETWORK}
