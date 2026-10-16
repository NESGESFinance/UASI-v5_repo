"""Agente JEV para Python: genera un `@dataclass(frozen=True)` tipado."""

from __future__ import annotations

import keyword
from typing import ClassVar

from uasi_v5.agentes_jev.base import AgenteJEVBase


class AgentePython(AgenteJEVBase):
    lenguaje: ClassVar[str] = "python"
    extension: ClassVar[str] = ".py"
    estilo_campo: ClassVar[str] = "snake"
    reservadas: ClassVar[frozenset[str]] = frozenset(keyword.kwlist)
    TIPOS: ClassVar[dict[str, str]] = {
        "bool": "bool",
        "int": "int",
        "float": "float",
        "str": "str",
        "null": "Optional[Any]",
        "object": "Dict[str, Any]",
        "any": "Any",
    }

    def formato_lista(self, tipo_interno: str) -> str:
        return f"List[{tipo_interno}]"

    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        lineas = [
            "from dataclasses import dataclass",
            "from typing import Any, Dict, List, Optional",
            "",
            "",
            "@dataclass(frozen=True)",
            f"class {nombre}:",
            f'    """Payload JEV generado automáticamente por UASI ({len(campos)} campos)."""',
            "",
        ]
        for original, ident, tipo in campos:
            sufijo = f"  # JSON: {original!r}" if ident != original else ""
            lineas.append(f"    {ident}: {tipo}{sufijo}")
        if not campos:
            lineas.append("    pass")
        return "\n".join(lineas) + "\n"
