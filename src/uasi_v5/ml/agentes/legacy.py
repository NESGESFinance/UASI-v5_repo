"""Agentes legacy v1: red neuronal, SVM y árbol de decisión."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List

import numpy as np
from filelock import FileLock
from sklearn.model_selection import GridSearchCV
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.ml.evaluacion import construir_cv_seguro, entrenar_y_evaluar_sklearn
from uasi_v5.persistencia.almacen_modelos import cargar_modelo_seguro, guardar_modelo_seguro, ruta_modelo


class _AgenteLegacyBase:
    nombre_modelo = "legacy"

    def __init__(self) -> None:
        self.ruta = ruta_modelo(self.nombre_modelo)

    def _fabrica(self, semilla: int) -> Callable[[], Any]:  # pragma: no cover - abstracto
        raise NotImplementedError

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1 if isinstance(previo, dict) else 1
            modelo, metricas = entrenar_y_evaluar_sklearn(self._fabrica(semilla), X, y, semilla)
            guardar_modelo_seguro(
                {"modelo": modelo, "version": version, "n_features": int(X.shape[1]),
                 "entrenado_en": ahora_utc().isoformat()},
                self.ruta,
            )
        return {"agente": self.nombre_modelo, "version_modelo": version, "metricas": metricas}


class AgenteRedNeuronalLegacy(_AgenteLegacyBase):
    """MLP clásico de scikit-learn con persistencia joblib+checksum."""

    nombre_modelo = "red_neuronal_legacy"

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        from sklearn.pipeline import make_pipeline

        return lambda: make_pipeline(
            StandardScaler(),
            MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=800, early_stopping=False, random_state=semilla),
        )


class AgenteSVMLegacy(_AgenteLegacyBase):
    """SVM con GridSearch y CV estratificada segura [P2-06]."""

    nombre_modelo = "svm_legacy"

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        self._cv = construir_cv_seguro(y, semilla)
        resultado = super().entrenar(X, y, semilla)
        resultado["estrategia_cv"] = type(self._cv).__name__ if self._cv is not None else "sin_cv (clase con 1 muestra)"
        return resultado

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        from sklearn.pipeline import make_pipeline

        cv = self._cv

        def construir() -> Any:
            base = make_pipeline(StandardScaler(), SVC(kernel="rbf", random_state=semilla))
            if cv is None:
                return base
            return _GridSearchSeguro(base, {"svc__C": [0.1, 1.0, 10.0], "svc__gamma": ["scale", 0.1]}, cv)

        return construir


class _GridSearchSeguro:
    """
    Envoltorio que recalcula la CV segura sobre el subconjunto con el que se
    entrena (el hold-out reduce muestras por clase y la CV global podría fallar).
    """

    def __init__(self, estimador: Any, rejilla: Dict[str, List[Any]], cv: Any) -> None:
        self.estimador, self.rejilla, self.cv = estimador, rejilla, cv
        self.modelo_: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> _GridSearchSeguro:
        cv = construir_cv_seguro(y, 42)
        if cv is None:
            self.modelo_ = copy.deepcopy(self.estimador).fit(X, y)
        else:
            gs = GridSearchCV(copy.deepcopy(self.estimador), self.rejilla, cv=cv, scoring="accuracy", n_jobs=1)
            self.modelo_ = gs.fit(X, y).best_estimator_
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.modelo_.predict(X)

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return self.modelo_.decision_function(X)


class AgenteArbolDecisionLegacy(_AgenteLegacyBase):
    """Árbol de decisión interpretable (profundidad acotada para evitar sobreajuste)."""

    nombre_modelo = "arbol_legacy"

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        return lambda: DecisionTreeClassifier(max_depth=5, min_samples_leaf=2, random_state=semilla)
