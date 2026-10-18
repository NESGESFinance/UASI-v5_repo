"""Ledger de auditoría append-only hash-encadenado (JSONL) [P3-01]."""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Any, Dict, List, Optional

from filelock import FileLock

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
