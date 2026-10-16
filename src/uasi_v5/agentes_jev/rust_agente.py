"""Agente JEV para Rust: genera un `struct` con derive de serde y renombrado de campos."""

from __future__ import annotations

import json
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase

_RESERVADAS_RUST = frozenset({
    "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum", "extern", "false",
    "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return",
    "self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while",
})


class AgenteRust(AgenteJEVBase):
    lenguaje: ClassVar[str] = "rust"
    extension: ClassVar[str] = ".rs"
    estilo_campo: ClassVar[str] = "snake"
    reservadas: ClassVar[frozenset[str]] = _RESERVADAS_RUST
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "bool",
        "int": "i64",
        "float": "f64",
        "str": "String",
        "null": "Option<serde_json::Value>",
        "object": "serde_json::Map<String, serde_json::Value>",
        "any": "serde_json::Value",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"Vec<{tipo_interno}>"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = [
            "use serde::{Deserialize, Serialize};",
            "",
            f"/// Payload JEV generado automáticamente por UASI ({len(campos)} campos).",
            "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]",
            f"pub struct {nombre} {{",
        ]
        for original, ident, tipo in campos:
            if ident != original:
                lineas.append(f"    #[serde(rename = {json.dumps(original, ensure_ascii=False)})]")
            lineas.append(f"    pub {ident}: {tipo},")
        lineas.append("}")
        return "\n".join(lineas) + "\n"
