"""Agente JEV para Solidity: genera un `struct` (los decimales se representan en punto fijo 1e18)."""

from __future__ import annotations

import json
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase

_RESERVADAS_SOL = frozenset({
    "address", "bool", "break", "bytes", "calldata", "constant", "continue", "contract", "delete", "do",
    "else", "emit", "enum", "event", "external", "false", "for", "function", "if", "import", "indexed",
    "int", "interface", "internal", "library", "mapping", "memory", "modifier", "new", "payable", "pragma",
    "private", "public", "pure", "return", "returns", "storage", "string", "struct", "true", "uint", "using",
    "view", "while",
})


class AgenteSolidity(AgenteJEVBase):
    lenguaje: ClassVar[str] = "solidity"
    extension: ClassVar[str] = ".sol"
    estilo_campo: ClassVar[str] = "camel"
    reservadas: ClassVar[frozenset[str]] = _RESERVADAS_SOL
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "bool",
        "int": "int256",
        "float": "int256",  # punto fijo: valor * 1e18
        "str": "string",
        "null": "bytes",
        "object": "bytes",  # objeto anidado ABI-encoded
        "any": "bytes",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"{tipo_interno}[]"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = [
            "// SPDX-License-Identifier: MIT",
            "pragma solidity ^0.8.20;",
            "",
            f"/// @notice Payload JEV generado automáticamente por UASI ({len(campos)} campos).",
            "/// @dev Los decimales se codifican como int256 en punto fijo (x 1e18); objetos como bytes ABI.",
            f"struct {nombre} {{",
        ]
        for original, ident, tipo in campos:
            lineas.append(f"    {tipo} {ident}; // JSON: {json.dumps(original, ensure_ascii=True)}")
        if not campos:
            lineas.append("    bool vacio; // Solidity no admite structs vacíos")
        lineas.append("}")
        return "\n".join(lineas) + "\n"
