"""Motor de aprendizaje en streaming continuo [P1-05] [P1-06] [P1-08]."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from filelock import FileLock
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

from uasi_v5.core.excepciones import ConflictoClasesStreaming, ErrorValidacionDatos
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.ml.evaluacion import calcular_metricas_clasificacion
from uasi_v5.persistencia.almacen_modelos import cargar_modelo_seguro, guardar_modelo_seguro, ruta_modelo


class MotorStreamingContinuo:
    """
    Aprendizaje online con SGDClassifier.partial_fit + StandardScaler.partial_fit.
    - Clases dinámicas: universo = np.unique(y) ∪ clases_declaradas.
    - SGD no admite añadir clases tras inicializarse: si llegan clases nuevas se
      responde 409 salvo que se pida reiniciar_modelo=True.
    - Métricas prequential: se evalúa el lote ANTES de aprender de él
      (estimación honesta) y también después ("accuracy_muestra_actual").
    """

    def __init__(self) -> None:
        self.ruta = ruta_modelo("streaming_sgd")

    def ingerir(self, X: np.ndarray, y: np.ndarray, clases_declaradas: Optional[List[int]],
                reiniciar: bool, semilla: int = 42) -> Dict[str, Any]:
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            estado = None if reiniciar else cargar_modelo_seguro(self.ruta)
            clases_lote = set(np.unique(y).tolist()) | set(clases_declaradas or [])
            metricas_previas: Optional[Dict[str, Any]] = None
            if estado is None:
                clases = np.array(sorted(clases_lote), dtype=int)
                if len(clases) < 2:
                    raise ErrorValidacionDatos(
                        "La inicialización del modelo online requiere ≥ 2 clases "
                        "(en el lote o en clases_declaradas)."
                    )
                estado = {
                    "scaler": StandardScaler(),
                    "modelo": SGDClassifier(loss="log_loss", alpha=1e-4, random_state=semilla),
                    "clases": clases,
                    "n_features": int(X.shape[1]),
                    "muestras_vistas": 0,
                    "lotes": 0,
                    "version": 0,
                }
            else:
                if X.shape[1] != estado["n_features"]:
                    raise ErrorValidacionDatos(
                        f"Dimensionalidad incompatible: el modelo online espera {estado['n_features']} "
                        f"características y el lote tiene {X.shape[1]}."
                    )
                nuevas = sorted(clases_lote - set(estado["clases"].tolist()))
                if nuevas:
                    raise ConflictoClasesStreaming(
                        f"Clases nuevas {nuevas} no presentes en el modelo online (clases={estado['clases'].tolist()}). "
                        "Envíe reiniciar_modelo=true con clases_declaradas para reconstruirlo."
                    )
                # Evaluación prequential (antes de aprender del lote)
                Xs_prev = estado["scaler"].transform(X)
                metricas_previas = calcular_metricas_clasificacion(y, estado["modelo"].predict(Xs_prev))

            estado["scaler"].partial_fit(X)
            Xs = estado["scaler"].transform(X)
            estado["modelo"].partial_fit(Xs, y, classes=estado["clases"])
            estado["muestras_vistas"] += int(len(y))
            estado["lotes"] += 1
            estado["version"] += 1
            estado["actualizado_en"] = ahora_utc().isoformat()
            acc_actual = float(accuracy_score(y, estado["modelo"].predict(Xs)))
            guardar_modelo_seguro(estado, self.ruta)

        return {
            "agente": "streaming_sgd",
            "version_modelo": estado["version"],
            "clases_modelo": estado["clases"].tolist(),
            "muestras_vistas_total": estado["muestras_vistas"],
            "lotes_procesados": estado["lotes"],
            "metricas": {
                "accuracy_muestra_actual": round(acc_actual, 6),
                "accuracy_muestra_actual_nota": "Medida sobre el mismo lote tras aprender de él (optimista).",
                "prequential_antes_de_actualizar": metricas_previas,
            },
        }
