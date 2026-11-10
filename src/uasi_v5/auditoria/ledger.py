"""
Ledgers de auditoría append-only hash-encadenados [P3-01].

- `LedgerInmutableSQL` (v5.3.0, por defecto): tabla SQLAlchemy `ledger_entries` con
  hash_actual = SHA-256(hash_anterior | timestamp | evento | actor | datos_json),
  génesis con hash_anterior = "0" * 64, unicidad de id y hash_anterior (sin bifurcaciones)
  y triggers de base de datos que prohíben UPDATE/DELETE.
- `LedgerAuditoriaCriptografica` (heredado): JSONL con FileLock (UASI_LEDGER_BACKEND=jsonl).
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Dict, List, Optional

from filelock import FileLock
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from uasi_v5.core.config import ARCHIVO_LEDGER, VERSION_UASI
from uasi_v5.core.utilidades import ahora_utc, json_canonico, sha256_hex


class LedgerAuditoriaCriptografica:
    """
    Ledger append-only hash-encadenado en JSONL (una línea = un bloque).
    Se elige JSONL en lugar de un volcado joblib completo porque un registro de
    auditoría debe ser legible por humanos, anexable sin reescribir y auditable
    con herramientas estándar. Escrituras bajo RLock + FileLock (multi-worker).

    hash_bloque = SHA-256( JSON canónico de {indice, timestamp, fecha, evento,
                           actor, datos, hash_datos, hash_previo} )
    """

    HASH_GENESIS = "0" * 64

    def __init__(self, ruta: str = ARCHIVO_LEDGER) -> None:
        self.ruta = ruta
        self._lock = threading.RLock()

    def _filelock(self) -> FileLock:
        return FileLock(f"{self.ruta}.lock", timeout=60)

    @staticmethod
    def _calcular_hash(bloque: Dict[str, Any]) -> str:
        contenido = {k: bloque[k] for k in ("indice", "timestamp", "fecha", "evento", "actor",
                                            "datos", "hash_datos", "hash_previo")}
        return sha256_hex(json_canonico(contenido).encode())

    def _leer_ultimo_bloque(self) -> Optional[Dict[str, Any]]:
        """Lee la última línea desde el final del archivo (O(1) en tamaño del ledger)."""
        if not os.path.exists(self.ruta) or os.path.getsize(self.ruta) == 0:
            return None
        with open(self.ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buffer = b""
            while pos > 0:
                paso = min(4096, pos)
                pos -= paso
                f.seek(pos)
                buffer = f.read(paso) + buffer
                lineas = buffer.rstrip(b"\n").split(b"\n")
                if len(lineas) > 1 or pos == 0:
                    return json.loads(lineas[-1].decode("utf-8"))
        return None

    def inicializar(self) -> None:
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        with self._lock, self._filelock():
            if self._leer_ultimo_bloque() is None:
                self._anexar_sin_lock("GENESIS", {"version": VERSION_UASI}, "sistema")

    def _anexar_sin_lock(self, evento: str, datos: Dict[str, Any], actor: str) -> Dict[str, Any]:
        ultimo = self._leer_ultimo_bloque()
        datos_json = json.loads(json_canonico(datos))  # normaliza a tipos JSON puros
        ts = ahora_utc()
        bloque = {
            "indice": (ultimo["indice"] + 1) if ultimo else 0,
            "timestamp": ts.isoformat(),
            "fecha": ts.date().isoformat(),
            "evento": evento,
            "actor": actor,
            "datos": datos_json,
            "hash_datos": sha256_hex(json_canonico(datos_json).encode()),
            "hash_previo": ultimo["hash_bloque"] if ultimo else self.HASH_GENESIS,
        }
        bloque["hash_bloque"] = self._calcular_hash(bloque)
        with open(self.ruta, "a", encoding="utf-8") as f:
            f.write(json_canonico(bloque) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return bloque

    def registrar(self, evento: str, datos: Dict[str, Any], actor: str = "sistema") -> Dict[str, Any]:
        with self._lock, self._filelock():
            return self._anexar_sin_lock(evento, datos, actor)

    def iterar(self):
        if not os.path.exists(self.ruta):
            return
        with open(self.ruta, encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    yield json.loads(linea)

    def contar(self) -> int:
        return sum(1 for _ in self.iterar())

    def paginar(self, pagina: int, tamano: int) -> Dict[str, Any]:
        inicio, fin = (pagina - 1) * tamano, pagina * tamano
        items, total = [], 0
        for i, b in enumerate(self.iterar()):
            if inicio <= i < fin:
                items.append(b)
            total += 1
        return {"pagina": pagina, "tamano_pagina": tamano, "total_bloques": total,
                "total_paginas": max(1, math.ceil(total / tamano)), "bloques": items}

    def bloques_de_fecha(self, fecha: str) -> List[Dict[str, Any]]:
        return [b for b in self.iterar() if b.get("fecha") == fecha]

    def verificar_integridad(self) -> Dict[str, Any]:
        previo, n = self.HASH_GENESIS, 0
        try:
            for esperado_idx, b in enumerate(self.iterar()):
                n += 1
                if b["indice"] != esperado_idx:
                    return {"integro": False, "bloques": n, "error": f"Índice discontinuo en bloque {esperado_idx}."}
                if b["hash_previo"] != previo:
                    return {"integro": False, "bloques": n, "error": f"Encadenamiento roto en bloque {b['indice']}."}
                if sha256_hex(json_canonico(b["datos"]).encode()) != b["hash_datos"]:
                    return {"integro": False, "bloques": n, "error": f"hash_datos alterado en bloque {b['indice']}."}
                if self._calcular_hash(b) != b["hash_bloque"]:
                    return {"integro": False, "bloques": n, "error": f"hash_bloque alterado en bloque {b['indice']}."}
                previo = b["hash_bloque"]
        except (json.JSONDecodeError, KeyError) as exc:
            return {"integro": False, "bloques": n, "error": f"Ledger corrupto: {type(exc).__name__}."}
        return {"integro": True, "bloques": n, "ultimo_hash": previo}


PATRON_EVENTO = re.compile(r"^[A-Z0-9_]{1,64}$")
HASH_GENESIS = "0" * 64


def calcular_hash_entrada(hash_anterior: str, timestamp: str, evento: str, actor: str, datos_json: str) -> str:
    """
    hash_actual = SHA-256(hash_anterior | timestamp | evento | actor | datos_json).

    Se usa "|" como separador explícito: hash_anterior es hex de 64, timestamp es ISO-8601,
    evento cumple ^[A-Z0-9_]+$ y el actor no puede contener "|", por lo que la
    concatenación es inyectiva (no hay dos entradas distintas con la misma preimagen).
    """
    preimagen = "|".join((hash_anterior, timestamp, evento, actor, datos_json))
    return sha256_hex(preimagen.encode("utf-8"))


class LedgerInmutableSQL:
    """
    Ledger append-only sobre SQLAlchemy con la MISMA interfaz pública que
    `LedgerAuditoriaCriptografica` (inicializar, registrar, iterar, contar, paginar,
    bloques_de_fecha, verificar_integridad), de modo que Merkle/OTS, el orquestador y la
    capa JEV funcionan sin cambios.

    Cada bloque devuelto contiene los campos canónicos {id, timestamp, evento, datos,
    hash_anterior, hash_actual} y alias de compatibilidad {indice, fecha, actor,
    hash_previo, hash_bloque, hash_datos}.
    """

    HASH_GENESIS = HASH_GENESIS
    REINTENTOS = 8

    def __init__(self, fabrica_sesiones: Optional[Callable[[], Session]] = None) -> None:
        self._fabrica = fabrica_sesiones
        self._lock = threading.RLock()

    def _sesion(self) -> Session:
        if self._fabrica is not None:
            return self._fabrica()
        from uasi_v5.db.sesion import FabricaSesiones, inicializar_bd

        inicializar_bd()
        return FabricaSesiones()

    @staticmethod
    def _a_bloque(fila: Any) -> Dict[str, Any]:
        datos = json.loads(fila.datos)
        return {
            "id": fila.id, "indice": fila.id, "timestamp": fila.timestamp, "fecha": fila.fecha,
            "evento": fila.evento, "actor": fila.actor, "datos": datos,
            "hash_datos": sha256_hex(fila.datos.encode("utf-8")),
            "hash_anterior": fila.hash_anterior, "hash_previo": fila.hash_anterior,
            "hash_actual": fila.hash_actual, "hash_bloque": fila.hash_actual,
        }

    def inicializar(self) -> None:
        from uasi_v5.db.modelos import LedgerEntry

        with self._lock, self._sesion() as s:
            existe = s.execute(select(LedgerEntry.id).limit(1)).first()
        if existe is None:
            try:
                self.registrar("GENESIS", {"version": VERSION_UASI}, "sistema")
            except IntegrityError:  # otro worker creó el génesis a la vez
                pass

    def registrar(self, evento: str, datos: Dict[str, Any], actor: str = "sistema") -> Dict[str, Any]:
        """Anexa una entrada encadenada. Reintenta si otro worker anexó en paralelo."""
        from uasi_v5.db.modelos import LedgerEntry

        if not PATRON_EVENTO.match(evento):
            raise ValueError(f"Evento de ledger inválido: {evento!r} (use ^[A-Z0-9_]{{1,64}}$).")
        actor = (actor or "sistema").replace("|", "_")[:128]
        datos_json = json_canonico(json.loads(json_canonico(datos)))  # normaliza a tipos JSON puros
        ultimo_error: Optional[Exception] = None
        for intento in range(self.REINTENTOS):
            with self._lock, self._sesion() as s:
                cabeza = s.execute(select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(1)).scalar_one_or_none()
                ts = ahora_utc()
                timestamp = ts.isoformat()
                hash_anterior = cabeza.hash_actual if cabeza else HASH_GENESIS
                fila = LedgerEntry(
                    id=(cabeza.id + 1) if cabeza else 0, timestamp=timestamp, fecha=ts.date().isoformat(),
                    evento=evento, actor=actor, datos=datos_json, hash_anterior=hash_anterior,
                    hash_actual=calcular_hash_entrada(hash_anterior, timestamp, evento, actor, datos_json),
                )
                s.add(fila)
                try:
                    s.commit()
                    return self._a_bloque(fila)
                except IntegrityError as exc:  # carrera con otro worker: reintentar sobre la nueva cabeza
                    s.rollback()
                    ultimo_error = exc
            time.sleep(0.005 * (intento + 1))
        raise RuntimeError("No se pudo anexar al ledger por concurrencia persistente.") from ultimo_error

    def iterar(self, lote: int = 500) -> Iterator[Dict[str, Any]]:
        from uasi_v5.db.modelos import LedgerEntry

        desde = -1
        while True:
            with self._sesion() as s:
                filas = s.execute(select(LedgerEntry).where(LedgerEntry.id > desde)
                                  .order_by(LedgerEntry.id).limit(lote)).scalars().all()
            if not filas:
                return
            for f in filas:
                yield self._a_bloque(f)
            desde = filas[-1].id

    def contar(self) -> int:
        from uasi_v5.db.modelos import LedgerEntry

        with self._sesion() as s:
            return int(s.execute(select(func.count()).select_from(LedgerEntry)).scalar_one())

    def paginar(self, pagina: int, tamano: int) -> Dict[str, Any]:
        from uasi_v5.db.modelos import LedgerEntry

        with self._sesion() as s:
            total = int(s.execute(select(func.count()).select_from(LedgerEntry)).scalar_one())
            filas = s.execute(select(LedgerEntry).order_by(LedgerEntry.id)
                              .offset((pagina - 1) * tamano).limit(tamano)).scalars().all()
        return {"pagina": pagina, "tamano_pagina": tamano, "total_bloques": total,
                "total_paginas": max(1, math.ceil(total / tamano)), "bloques": [self._a_bloque(f) for f in filas]}

    def bloques_de_fecha(self, fecha: str) -> List[Dict[str, Any]]:
        from uasi_v5.db.modelos import LedgerEntry

        with self._sesion() as s:
            filas = s.execute(select(LedgerEntry).where(LedgerEntry.fecha == fecha)
                              .order_by(LedgerEntry.id)).scalars().all()
        return [self._a_bloque(f) for f in filas]

    def verificar_integridad(self) -> Dict[str, Any]:
        """Recorre la cadena completa y detecta cualquier alteración, inserción o hueco."""
        from uasi_v5.db.modelos import LedgerEntry

        previo, n, desde = HASH_GENESIS, 0, -1
        while True:
            with self._sesion() as s:
                filas = s.execute(select(LedgerEntry).where(LedgerEntry.id > desde)
                                  .order_by(LedgerEntry.id).limit(1000)).scalars().all()
            if not filas:
                break
            for f in filas:
                if f.id != n:
                    return {"integro": False, "bloques": n + 1, "error": f"Índice discontinuo en bloque {n}."}
                n += 1
                if f.hash_anterior != previo:
                    return {"integro": False, "bloques": n, "error": f"Encadenamiento roto en bloque {f.id}."}
                if f.fecha != f.timestamp[:10]:
                    return {"integro": False, "bloques": n, "error": f"fecha alterada en bloque {f.id}."}
                if calcular_hash_entrada(f.hash_anterior, f.timestamp, f.evento, f.actor, f.datos) != f.hash_actual:
                    return {"integro": False, "bloques": n, "error": f"hash_actual alterado en bloque {f.id}."}
                previo = f.hash_actual
            desde = filas[-1].id
        return {"integro": True, "bloques": n, "ultimo_hash": previo}
