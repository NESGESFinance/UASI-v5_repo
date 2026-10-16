"""Agente ensamblado explicable (Gradient Boosting)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from filelock import FileLock
from sklearn.ensemble import GradientBoostingClassifier

from uasi_v5.ml.evaluacion import entrenar_y_evaluar_sklearn
from uasi_v5.persistencia.almacen_modelos import cargar_modelo_seguro, guardar_modelo_seguro, ruta_modelo


class AgenteEnsambladoExplicable:
    """Gradient Boosting con importancia de características (explicabilidad global)."""

    def __init__(self) -> None:
        self.ruta = ruta_modelo("ensamblado_gb")

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42,
                 nombres: Optional[List[str]] = None) -> Dict[str, Any]:
        fabrica = lambda: GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.1,  # noqa: E731
                                                     subsample=0.9, random_state=semilla)
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            modelo, metricas = entrenar_y_evaluar_sklearn(fabrica, X, y, semilla)
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1
            guardar_modelo_seguro({"modelo": modelo, "version": version, "n_features": int(X.shape[1])}, self.ruta)
        nombres = nombres if nombres and len(nombres) == X.shape[1] else [f"x{i}" for i in range(X.shape[1])]
        importancias = sorted(zip(nombres, modelo.feature_importances_.tolist()), key=lambda t: -t[1])
        return {
            "agente": "ensamblado_explicable_gb",
            "version_modelo": version,
            "metricas": metricas,
            "explicabilidad": {
                "metodo": "feature_importances_ (reducción de impureza)",
                "importancias_top": [{"caracteristica": n, "importancia": round(v, 6)} for n, v in importancias[:15]],
            },
        }
