"""Agente vectorial evolutivo (SVM + búsqueda evolutiva)."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
from filelock import FileLock
from sklearn.metrics import accuracy_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from uasi_v5.ml.evaluacion import (
    calcular_metricas_clasificacion,
    construir_cv_seguro,
    entrenar_y_evaluar_sklearn,
    puntuaciones_modelo,
)
from uasi_v5.persistencia.almacen_modelos import cargar_modelo_seguro, guardar_modelo_seguro, ruta_modelo


class AgenteVectorialEvolutivo:
    """
    Algoritmo evolutivo sobre (log10 C, log10 gamma) de un SVM RBF.
    Fitness = accuracy media de CV segura (reportada como `accuracy_cv`,
    NO como "precision"). Además precision/recall/F1 sobre el conjunto de
    entrenamiento con los mejores parámetros (etiquetado como tal) y métricas
    de hold-out cuando hay datos suficientes.
    """

    def __init__(self, poblacion: int = 8, generaciones: int = 5) -> None:
        self.ruta = ruta_modelo("vectorial_evolutivo")
        self.poblacion, self.generaciones = poblacion, generaciones

    @staticmethod
    def _modelo(genes: np.ndarray, semilla: int) -> Any:
        from sklearn.pipeline import make_pipeline

        return make_pipeline(StandardScaler(), SVC(C=10 ** genes[0], gamma=10 ** genes[1], random_state=semilla))

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        rng = np.random.default_rng(semilla)
        # Muestra acotada para la búsqueda (SVC es O(n²))
        if len(y) > 2000:
            idx, _ = train_test_split(np.arange(len(y)), train_size=2000, stratify=y, random_state=semilla)
            Xb, yb = X[idx], y[idx]
        else:
            Xb, yb = X, y
        cv = construir_cv_seguro(yb, semilla)
        limites = np.array([[-2.0, 3.0], [-4.0, 1.0]])

        def fitness(genes: np.ndarray) -> float:
            modelo = self._modelo(genes, semilla)
            if cv is None:
                return float(accuracy_score(yb, modelo.fit(Xb, yb).predict(Xb)))
            return float(np.mean(cross_val_score(modelo, Xb, yb, cv=cv, scoring="accuracy")))

        pob = rng.uniform(limites[:, 0], limites[:, 1], size=(self.poblacion, 2))
        apt = np.array([fitness(g) for g in pob])
        historial = [round(float(apt.max()), 6)]
        for _ in range(self.generaciones):
            orden = np.argsort(-apt)
            elite = pob[orden[: self.poblacion // 2]]
            hijos = []
            while len(hijos) < self.poblacion - len(elite):
                p1, p2 = elite[rng.integers(len(elite))], elite[rng.integers(len(elite))]
                hijo = (p1 + p2) / 2 + rng.normal(0, 0.3, 2)  # cruce aritmético + mutación gaussiana
                hijos.append(np.clip(hijo, limites[:, 0], limites[:, 1]))
            pob = np.vstack([elite, np.array(hijos)])
            apt = np.array([fitness(g) for g in pob])
            historial.append(round(float(apt.max()), 6))
        mejor = pob[int(np.argmax(apt))]

        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            modelo, metricas_holdout = entrenar_y_evaluar_sklearn(lambda: self._modelo(mejor, semilla), X, y, semilla)
            pred_train = modelo.predict(X)
            m_train = calcular_metricas_clasificacion(y, pred_train, puntuaciones_modelo(modelo, X))
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1
            guardar_modelo_seguro({"modelo": modelo, "genes": mejor, "version": version,
                                   "n_features": int(X.shape[1])}, self.ruta)
        return {
            "agente": "vectorial_evolutivo",
            "version_modelo": version,
            "mejores_hiperparametros": {"C": round(float(10 ** mejor[0]), 6), "gamma": round(float(10 ** mejor[1]), 6)},
            "evolucion_fitness_accuracy_cv": historial,
            "metricas": {
                "accuracy_cv": round(float(apt.max()), 6),
                "estrategia_cv": type(cv).__name__ if cv is not None else "sin_cv (accuracy de entrenamiento)",
                "precision_entrenamiento": m_train["precision"],
                "recall_entrenamiento": m_train["recall"],
                "f1_entrenamiento": m_train["f1_score"],
                "holdout": metricas_holdout,
            },
        }
