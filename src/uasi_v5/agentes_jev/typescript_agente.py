"""Agente JEV para TypeScript: genera una `export interface` de solo lectura."""

from __future__ import annotations

import json
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase


class AgenteTypeScript(AgenteJEVBase):
    lenguaje: ClassVar[str] = "typescript"
    extension: ClassVar[str] = ".ts"
    estilo_campo: ClassVar[str] = "camel"
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "boolean",
        "int": "number",
        "float": "number",
        "str": "string",
        "null": "null",
        "object": "Record<string, unknown>",
        "any": "unknown",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"Array<{tipo_interno}>"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        # En TS se conserva la clave JSON original (entrecomillada) para que el tipo
        # describa exactamente el payload emitido por JEV.
        lineas = [f"/** Payload JEV generado automáticamente por UASI ({len(campos)} campos). */"]
        lineas.append(f"export interface {nombre} {{")
        for original, _ident, tipo in campos:
            lineas.append(f"  readonly {json.dumps(original, ensure_ascii=False)}: {tipo};")
        lineas.append("}")
        return "\n".join(lineas) + "\n"
