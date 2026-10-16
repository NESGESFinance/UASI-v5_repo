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
