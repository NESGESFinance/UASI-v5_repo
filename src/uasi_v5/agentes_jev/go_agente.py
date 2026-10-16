"""Agente JEV para Go: genera un `struct` exportado con etiquetas `json`."""

from __future__ import annotations

import re
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase


class AgenteGo(AgenteJEVBase):
    lenguaje: ClassVar[str] = "go"
    extension: ClassVar[str] = ".go"
    estilo_campo: ClassVar[str] = "pascal"  # exportado
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "bool",
        "int": "int64",
        "float": "float64",
        "str": "string",
        "null": "*json.RawMessage",
        "object": "map[string]any",
        "any": "any",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"[]{tipo_interno}"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = ["package jev", ""]
        if any("json." in tipo for _o, _i, tipo in campos):
            lineas += ['import "encoding/json"', ""]
        lineas += [f"// {nombre} es el payload JEV generado automáticamente por UASI ({len(campos)} campos).",
                   f"type {nombre} struct {{"]
        for original, ident, tipo in campos:
            etiqueta = re.sub(r"[\"`\\\s]", "_", original)
            lineas.append(f'\t{ident} {tipo} `json:"{etiqueta}"`')
        lineas.append("}")
        return "\n".join(lineas) + "\n"
