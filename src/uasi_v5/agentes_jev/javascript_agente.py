"""Agente JEV para JavaScript: genera un `@typedef` JSDoc y un objeto por defecto congelado."""

from __future__ import annotations

import json
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase

_VALOR_DEFECTO = {"boolean": "false", "number": "0", "string": '""', "null": "null", "Object": "{}", "*": "null"}


class AgenteJavaScript(AgenteJEVBase):
    lenguaje: ClassVar[str] = "javascript"
    extension: ClassVar[str] = ".js"
    estilo_campo: ClassVar[str] = "camel"
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "boolean",
        "int": "number",
        "float": "number",
        "str": "string",
        "null": "null",
        "object": "Object",
        "any": "*",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"Array<{tipo_interno}>"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = ["/**", f" * Payload JEV generado automáticamente por UASI ({len(campos)} campos).",
                  f" * @typedef {{Object}} {nombre}"]
        for original, _ident, tipo in campos:
            lineas.append(f" * @property {{{tipo}}} {json.dumps(original, ensure_ascii=False)}")
        lineas += [" */", "", f"/** @type {{Readonly<{nombre}>}} */", f"export const {nombre}Defecto = Object.freeze({{"]
        for original, _ident, tipo in campos:
            valor = "[]" if tipo.startswith("Array<") else _VALOR_DEFECTO.get(tipo, "null")
            lineas.append(f"  {json.dumps(original, ensure_ascii=False)}: {valor},")
        lineas.append("});")
        return "\n".join(lineas) + "\n"
