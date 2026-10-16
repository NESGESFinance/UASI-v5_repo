"""Agente JEV para Java: genera una clase inmutable con constructor y getters."""

from __future__ import annotations

import json
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase

_RESERVADAS_JAVA = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue",
    "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if",
    "implements", "import", "instanceof", "int", "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false", "null", "record",
})


class AgenteJava(AgenteJEVBase):
    lenguaje: ClassVar[str] = "java"
    extension: ClassVar[str] = ".java"
    estilo_campo: ClassVar[str] = "camel"
    reservadas: ClassVar[frozenset[str]] = _RESERVADAS_JAVA
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "boolean",
        "int": "long",
        "float": "double",
        "str": "String",
        "null": "Object",
        "object": "Map<String, Object>",
        "any": "Object",
    }
    TIPOS_GENERICOS: ClassVar[dict[str, str]] = {"bool": "Boolean", "int": "Long", "float": "Double"}

    def formato_lista(self, tipo_interno: str) -> str:
        return f"List<{tipo_interno}>"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = ["package jev;", "", "import java.util.List;", "import java.util.Map;", "",
                  f"/** Payload JEV generado automáticamente por UASI ({len(campos)} campos). */",
                  f"public final class {nombre} {{"]
        for original, ident, tipo in campos:
            lineas.append(f"    private final {tipo} {ident}; // JSON: {json.dumps(original, ensure_ascii=True)}")
        params = ", ".join(f"{tipo} {ident}" for _o, ident, tipo in campos)
        lineas += ["", f"    public {nombre}({params}) {{"]
        lineas += [f"        this.{ident} = {ident};" for _o, ident, _t in campos]
        lineas.append("    }")
        for _o, ident, tipo in campos:
            getter = ("is" if tipo == "boolean" else "get") + ident[0].upper() + ident[1:]
            lineas += ["", f"    public {tipo} {getter}() {{", f"        return {ident};", "    }"]
        lineas.append("}")
        return "\n".join(lineas) + "\n"
