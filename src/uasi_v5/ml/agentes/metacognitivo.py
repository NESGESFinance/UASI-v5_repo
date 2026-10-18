"""Metacognición operativa: perfilado por entropía y decisión de ruta [P3-06]."""

from __future__ import annotations

import math
from typing import Any, Dict, Tuple

import numpy as np

from uasi_v5.core.config import UMBRAL_ENTROPIA_DESBALANCE, UMBRAL_ENTROPIA_FEATURES
from uasi_v5.core.excepciones import ErrorValidacionDatos


class AgenteAnalistaDatosMetacognitivo:
    """
    Perfila el dataset y DECIDE la ruta (la entropía de Shannon influye de verdad):
      · Entropía normalizada de la distribución de clases H_norm = H / log2(K).
        H_norm < UMBRAL_ENTROPIA_DESBALANCE ⇒ datos desbalanceados ⇒ se prefiere
        Gradient Boosting (robusto al desbalance) sobre SVM.
      · Entropía media normalizada de las características (histograma 10 bins).
        Valor alto (> UMBRAL_ENTROPIA_FEATURES) con n ≥ 100 ⇒ distribución
        compleja/dispersa ⇒ Gradient Boosting.
      · n ≥ 200 y binario ⇒ red profunda; en otro caso ⇒ SVM evolutivo.
    """

    @staticmethod
    def _entropia_bits(probabilidades: np.ndarray) -> float:
        p = probabilidades[probabilidades > 0]
        return float(-(p * np.log2(p)).sum())

    def analizar(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        clases, conteos = np.unique(y, return_counts=True)
        k = len(clases)
        h_clases = self._entropia_bits(conteos / conteos.sum())
        h_norm = h_clases / math.log2(k) if k > 1 else 0.0
        ent_features = []
        for j in range(X.shape[1]):
            col = X[:, j]
            if np.ptp(col) <= 1e-12:
                ent_features.append(0.0)
                continue
            hist, _ = np.histogram(col, bins=10)
            ent_features.append(self._entropia_bits(hist / hist.sum()) / math.log2(10))
        return {
            "n_muestras": int(X.shape[0]),
            "n_caracteristicas": int(X.shape[1]),
            "clases": clases.tolist(),
            "distribucion_clases": {str(c): int(n) for c, n in zip(clases, conteos, strict=False)},
            "entropia_shannon_bits": round(h_clases, 6),
            "entropia_clases_normalizada": round(h_norm, 6),
            "ratio_desbalance": round(float(conteos.max() / conteos.min()), 4),
            "entropia_media_caracteristicas_normalizada": round(float(np.mean(ent_features)), 6),
            "binario": set(clases.tolist()).issubset({0, 1}) and k == 2,
        }

    def decidir_ruta(self, perfil: Dict[str, Any]) -> Tuple[str, str]:
        if len(perfil["clases"]) < 2:
            raise ErrorValidacionDatos("Se requieren al menos 2 clases para entrenar.")
        if perfil["entropia_clases_normalizada"] < UMBRAL_ENTROPIA_DESBALANCE:
            return "ensamblado_explicable", (
                f"Entropía de clases {perfil['entropia_clases_normalizada']:.3f} < {UMBRAL_ENTROPIA_DESBALANCE} "
                "⇒ desbalance ⇒ Gradient Boosting preferido sobre SVM."
            )
        if perfil["entropia_media_caracteristicas_normalizada"] > UMBRAL_ENTROPIA_FEATURES and perfil["n_muestras"] >= 100:
            return "ensamblado_explicable", (
                f"Entropía media de características {perfil['entropia_media_caracteristicas_normalizada']:.3f} > "
                f"{UMBRAL_ENTROPIA_FEATURES} ⇒ alta complejidad ⇒ Gradient Boosting."
            )
        if perfil["n_muestras"] >= 200 and perfil["binario"]:
            return "cerebral_profundo", "Volumen suficiente (n ≥ 200) y problema binario ⇒ red profunda."
        return "vectorial_evolutivo", "Datos equilibrados y volumen moderado ⇒ SVM con búsqueda evolutiva."
