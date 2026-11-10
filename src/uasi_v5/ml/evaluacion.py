"""Utilidades de evaluación honesta (holdout, CV seguro, métricas) [P2-01]."""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import LeaveOneOut, StratifiedKFold, train_test_split


def calcular_metricas_clasificacion(
    y_true: np.ndarray, y_pred: np.ndarray, y_score: Optional[np.ndarray] = None
) -> Dict[str, Any]:
    """accuracy, precision, recall, F1 y ROC-AUC (binario) o macro (multiclase)."""
    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)
    clases = np.unique(np.concatenate([y_true, y_pred]))
    binario = set(clases.tolist()).issubset({0, 1})
    promedio = "binary" if binario else "macro"
    metricas: Dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "precision": round(float(precision_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "recall": round(float(recall_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "f1_score": round(float(f1_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "promedio": promedio,
        "n_evaluadas": int(len(y_true)),
    }
    if y_score is not None and binario and len(np.unique(y_true)) == 2:
        metricas["auc_roc"] = round(float(roc_auc_score(y_true, y_score)), 6)
    else:
        metricas["auc_roc"] = None
        metricas["auc_roc_nota"] = "No calculable (una sola clase en evaluación, multiclase o sin puntuaciones)."
    return metricas


def dividir_holdout(X: np.ndarray, y: np.ndarray, semilla: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    """Hold-out estratificado 25 % si hay datos suficientes; si no, evaluación optimista explícita."""
    conteos = np.bincount(y)
    min_clase = int(conteos[conteos > 0].min())
    n_clases = int((conteos > 0).sum())
    n_test = int(math.ceil(0.25 * len(y)))
    if len(y) >= 10 and min_clase >= 2 and n_test >= n_clases and len(y) - n_test >= n_clases:
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, stratify=y, random_state=semilla)
        return X_tr, X_te, y_tr, y_te, "holdout_estratificado_25pct"
    return X, X, y, y, "entrenamiento_optimista (datos insuficientes para hold-out)"


def puntuaciones_modelo(modelo: Any, X: np.ndarray) -> Optional[np.ndarray]:
    """Puntuación continua para ROC-AUC (probabilidad de clase 1 o decision_function)."""
    try:
        if hasattr(modelo, "predict_proba"):
            proba = modelo.predict_proba(X)
            if proba.ndim == 2 and proba.shape[1] == 2:
                return proba[:, 1]
        if hasattr(modelo, "decision_function"):
            dec = modelo.decision_function(X)
            if np.ndim(dec) == 1:
                return dec
    except Exception:  # pragma: no cover - modelos sin soporte
        return None
    return None


def construir_cv_seguro(y: np.ndarray, semilla: int) -> Optional[Any]:
    """
    [P2-06] cv = StratifiedKFold(min(3, min(np.bincount(y)))) si cada clase tiene
    ≥ 3 muestras; LeaveOneOut si alguna clase tiene 2 (cada fold conserva
    ambas clases); None (sin CV) si alguna clase tiene 1 sola muestra.
    """
    conteos = np.bincount(y)
    min_clase = int(conteos[conteos > 0].min())
    if min_clase >= 3:
        return StratifiedKFold(n_splits=min(3, min_clase), shuffle=True, random_state=semilla)
    if min_clase == 2:
        return LeaveOneOut()
    return None


def semilla_desde_contexto(meta: Dict[str, Any]) -> int:
    """metadatos_contexto['semilla'] controla la reproducibilidad (uso real de los metadatos)."""
    try:
        return int(meta.get("semilla", 42)) % (2**32 - 1)
    except (TypeError, ValueError):
        return 42


def entrenar_y_evaluar_sklearn(fabrica: Callable[[], Any], X: np.ndarray, y: np.ndarray, semilla: int) -> Tuple[Any, Dict[str, Any]]:
    """Evalúa en hold-out y después reentrena con TODOS los datos para el modelo final."""
    X_tr, X_te, y_tr, y_te, conjunto = dividir_holdout(X, y, semilla)
    modelo_eval = fabrica().fit(X_tr, y_tr)
    metricas = calcular_metricas_clasificacion(y_te, modelo_eval.predict(X_te), puntuaciones_modelo(modelo_eval, X_te))
    metricas["conjunto_evaluacion"] = conjunto
    modelo_final = fabrica().fit(X, y) if conjunto.startswith("holdout") else modelo_eval
    return modelo_final, metricas


# --- Privacidad diferencial (mecanismo gaussiano) -------------------------------------------
# Claves que NUNCA salen por la API: parámetros, gradientes, estados de optimizador e
# historiales de entrenamiento (memorizan información de las muestras de entrenamiento).
_FRAGMENTOS_PROHIBIDOS = ("peso", "weight", "gradiente", "gradient", "coef", "intercept", "adam",
                          "estado_optimizador", "historial", "evolucion_fitness", "loss", "perdida")
# Métricas acotadas en [0, 1] que se publican con ruido calibrado.
_METRICAS_ACOTADAS = ("accuracy", "precision", "recall", "f1", "auc", "exactitud")


class MetricasPrivadas:
    """
    Publicación (ε, δ)-diferencialmente privada de métricas escalares (mecanismo gaussiano).

        σ = Δ · √(2 · ln(1.25 / δ)) / ε          (Dwork & Roth 2014, Teorema A.1, ε ∈ (0, 1])

    `sensibilidad` (Δ) es la sensibilidad L2 de la métrica: para una proporción calculada
    sobre n muestras (accuracy, precision, recall…) Δ = 1/n. Cada llamada a `publicar`
    consume ε del presupuesto (composición básica); `epsilon_consumido` lo contabiliza.
    """

    def __init__(self, epsilon: float = 1.0, delta: float = 1e-5, semilla: Optional[int] = None) -> None:
        if not 0 < epsilon <= 1.0:
            raise ValueError("El mecanismo gaussiano clásico requiere 0 < ε ≤ 1.")
        if not 0 < delta < 1:
            raise ValueError("δ debe estar en (0, 1).")
        self.epsilon = float(epsilon)
        self.delta = float(delta)
        self.epsilon_consumido = 0.0
        self.publicaciones = 0
        self._rng = np.random.default_rng(semilla)  # semilla=None ⇒ entropía del sistema operativo

    def sigma(self, sensibilidad: float) -> float:
        return float(sensibilidad) * math.sqrt(2.0 * math.log(1.25 / self.delta)) / self.epsilon

    def publicar(self, metrica: float, sensibilidad: float, acotar: Optional[Tuple[float, float]] = (0.0, 1.0)) -> float:
        """Devuelve `metrica + N(0, σ²)`; el recorte posterior a [0, 1] no consume privacidad."""
        if sensibilidad <= 0:
            raise ValueError("La sensibilidad debe ser positiva.")
        ruidosa = float(metrica) + float(self._rng.normal(0.0, self.sigma(sensibilidad)))
        self.epsilon_consumido += self.epsilon
        self.publicaciones += 1
        if acotar is not None:
            ruidosa = min(max(ruidosa, acotar[0]), acotar[1])
        return round(ruidosa, 6)


def _clave_prohibida(clave: str) -> bool:
    c = clave.lower()
    return any(f in c for f in _FRAGMENTOS_PROHIBIDOS)


def _es_metrica_acotada(clave: str, valor: Any) -> bool:
    c = clave.lower()
    return (isinstance(valor, (int, float)) and not isinstance(valor, bool) and 0.0 <= float(valor) <= 1.0
            and any(f in c for f in _METRICAS_ACOTADAS))


def privatizar_resultado(resultado: Any, n_muestras: int, epsilon: Optional[float] = None,
                         delta: Optional[float] = None, activa: Optional[bool] = None) -> Any:
    """
    Prepara un resultado de agente para publicarlo por la API (y en el ledger, que también se expone):
    - Elimina recursivamente pesos, gradientes, estados de optimizador, pérdidas e historiales.
    - Añade ruido gaussiano calibrado (Δ = 1/n) a toda métrica acotada en [0, 1].
    - Añade `privacidad_diferencial` con ε por métrica, δ y ε total (composición básica).
    """
    from uasi_v5.core import config

    activa = config.UASI_DP_ACTIVA if activa is None else activa
    if not isinstance(resultado, dict):
        return resultado
    mecanismo = MetricasPrivadas(epsilon=config.UASI_DP_EPSILON if epsilon is None else epsilon,
                                 delta=config.UASI_DP_DELTA if delta is None else delta)
    metricas = resultado.get("metricas")
    n_eval = metricas.get("n_evaluadas") if isinstance(metricas, dict) else None
    # Δ = 1/n sobre el conjunto con el que realmente se calculó la métrica (hold-out si existe).
    sensibilidad = 1.0 / max(1, int(n_eval if isinstance(n_eval, int) and n_eval > 0 else n_muestras))

    def _recorrer(nodo: Any) -> Any:
        if isinstance(nodo, dict):
            salida: Dict[str, Any] = {}
            for k, v in nodo.items():
                if _clave_prohibida(str(k)):
                    continue
                if activa and _es_metrica_acotada(str(k), v):
                    salida[k] = mecanismo.publicar(float(v), sensibilidad)
                else:
                    salida[k] = _recorrer(v)
            return salida
        if isinstance(nodo, list):
            return [_recorrer(x) for x in nodo]
        return nodo

    publico = _recorrer(resultado)
    publico["privacidad_diferencial"] = {
        "activa": bool(activa), "mecanismo": "gaussiano", "epsilon_por_metrica": mecanismo.epsilon,
        "delta": mecanismo.delta, "sensibilidad": round(sensibilidad, 8),
        "metricas_privatizadas": mecanismo.publicaciones,
        "epsilon_total_composicion_basica": round(mecanismo.epsilon_consumido, 6),
    }
    return publico
