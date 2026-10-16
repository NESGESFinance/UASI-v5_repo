"""Dispatcher multiagente JEV: reparte una tarea entre los agentes de cada lenguaje en paralelo.

Las dependencias internas de UASI (integridad del ledger, estado de salud) se inyectan como
*callables* desde `uasi_v5.servicios`, de modo que este módulo no importa ni el ledger ni el
contenedor de servicios (sin imports circulares).
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from uasi_v5.agentes_jev.automatizacion import TAREAS_NO_CACHEABLES, CacheResultadosJEV, clave_cache
from uasi_v5.agentes_jev.base import CLAVE_CONTEXTO, AgenteJEVBase, ejecutar_agente
from uasi_v5.agentes_jev.go_agente import AgenteGo
from uasi_v5.agentes_jev.java_agente import AgenteJava
from uasi_v5.agentes_jev.javascript_agente import AgenteJavaScript
from uasi_v5.agentes_jev.python_agente import AgentePython
from uasi_v5.agentes_jev.rust_agente import AgenteRust
from uasi_v5.agentes_jev.solidity_agente import AgenteSolidity
from uasi_v5.agentes_jev.typescript_agente import AgenteTypeScript
from uasi_v5.esquemas.jev import ResultadoAgenteJEV

ProveedorContexto = Callable[[], dict[str, Any]]

REGISTRO_AGENTES: dict[str, type[AgenteJEVBase]] = {
    clase.lenguaje: clase
    for clase in (AgentePython, AgenteTypeScript, AgenteJavaScript, AgenteSolidity, AgenteRust, AgenteGo, AgenteJava)
}


class DispatcherMultiagenteJEV:
    """Ejecuta los agentes solicitados en un ThreadPoolExecutor con aislamiento de errores."""

    def __init__(
        self,
        max_workers: int = 4,
        verificar_ledger: ProveedorContexto | None = None,
        salud: ProveedorContexto | None = None,
        registro: dict[str, type[AgenteJEVBase]] | None = None,
        cache: CacheResultadosJEV | None = None,
    ) -> None:
        self.max_workers = max(1, int(max_workers))
        self._verificar_ledger = verificar_ledger
        self._salud = salud
        self._registro: dict[str, type[AgenteJEVBase]] = dict(registro if registro is not None else REGISTRO_AGENTES)
        self.cache = cache  # None ⇒ sin caché

    def _clave(self, lang: str, tipo_tarea: str, datos: dict[str, Any]) -> str | None:
        """Clave de caché o None si la tarea depende del estado vivo de UASI."""
        if self.cache is None or not self.cache.activa or tipo_tarea in TAREAS_NO_CACHEABLES:
            return None
        clase = self._registro[lang]
        return clave_cache(f"{clase.__module__}.{clase.__qualname__}", tipo_tarea, datos)

    @property
    def lenguajes(self) -> list[str]:
        return list(self._registro)

    def registrar_agente(self, clase: type[AgenteJEVBase]) -> None:
        """Extensión: registra (o reemplaza) el agente de un lenguaje."""
        if not clase.lenguaje:
            raise ValueError("El agente debe declarar el atributo 'lenguaje'.")
        self._registro[clase.lenguaje] = clase

    def _contexto(self, tipo_tarea: str, errores: list[str]) -> dict[str, Any]:
        """Contexto interno calculado UNA vez por despacho (no por agente)."""
        proveedores: dict[str, tuple[str, ProveedorContexto | None]] = {
            "AUDITORIA_LEDGER": ("integridad_ledger", self._verificar_ledger),
            "EVALUACION_MODELO": ("salud", self._salud),
        }
        clave, proveedor = proveedores.get(tipo_tarea, ("", None))
        if proveedor is None:
            return {}
        try:
            return {clave: proveedor()}
        except Exception as exc:
            errores.append(f"No se pudo obtener '{clave}': {type(exc).__name__}: {exc}"[:500])
            return {}

    def despachar(
        self,
        tipo_tarea: str,
        lenguajes: Iterable[str],
        datos: dict[str, Any],
        metadatos: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Despacha la tarea y devuelve estado global, resultados por lenguaje y errores globales."""
        inicio = time.perf_counter()
        errores: list[str] = []
        pedidos = list(dict.fromkeys(lenguajes))
        meta = dict(metadatos or {})
        meta[CLAVE_CONTEXTO] = self._contexto(tipo_tarea, errores)  # sobrescribe cualquier valor externo

        resultados: dict[str, ResultadoAgenteJEV] = {}
        conocidos = [lang for lang in pedidos if lang in self._registro]
        for lang in pedidos:
            if lang not in self._registro:
                resultados[lang] = ResultadoAgenteJEV(
                    lenguaje=lang, estado="error", mensaje_error=f"Lenguaje no soportado: {lang!r}"
                )

        claves = {lang: self._clave(lang, tipo_tarea, datos) for lang in conocidos}
        pendientes: list[str] = []
        for lang in conocidos:
            clave = claves[lang]
            cacheado = self.cache.obtener(clave) if (self.cache is not None and clave) else None
            if cacheado is not None:
                resultados[lang] = cacheado
            else:
                pendientes.append(lang)

        if pendientes:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(pendientes)),
                                    thread_name_prefix="jev-agente") as pool:
                futuros = {
                    lang: pool.submit(
                        ejecutar_agente, self._registro[lang](), tipo_tarea, copy.deepcopy(datos), copy.deepcopy(meta)
                    )
                    for lang in pendientes
                }
                for lang, futuro in futuros.items():
                    try:
                        resultados[lang] = futuro.result()
                    except Exception as exc:  # defensa en profundidad: ejecutar_agente ya aísla
                        resultados[lang] = ResultadoAgenteJEV(
                            lenguaje=lang, estado="error", mensaje_error=f"{type(exc).__name__}: {exc}"[:500]
                        )
                    clave = claves[lang]
                    if self.cache is not None and clave:
                        self.cache.guardar(clave, resultados[lang])

        ordenados = {lang: resultados[lang] for lang in pedidos}
        exitos = sum(1 for r in ordenados.values() if r.estado == "exito")
        if ordenados and exitos == len(ordenados) and not errores:
            estado = "exito"
        elif exitos == 0:
            estado = "error"
        else:
            estado = "error_parcial"
        return {
            "estado": estado,
            "lenguajes_ejecutados": [lang for lang, r in ordenados.items() if r.estado == "exito"],
            "resultados_por_lenguaje": ordenados,
            "errores_globales": errores,
            "duracion_total_ms": round((time.perf_counter() - inicio) * 1000, 3),
        }
