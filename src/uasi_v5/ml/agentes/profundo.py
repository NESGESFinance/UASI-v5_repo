"""Agente cerebral profundo (red numpy de 2 capas) [P2-03] [P2-04] [P2-05]."""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
from filelock import FileLock
from sklearn.model_selection import train_test_split

from uasi_v5.core.excepciones import ErrorValidacionDatos
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.ml.evaluacion import calcular_metricas_clasificacion, dividir_holdout
from uasi_v5.persistencia.almacen_modelos import cargar_modelo_seguro, guardar_modelo_seguro, ruta_modelo


class AgenteCerebralProfundo:
    """
    Red densa X → tanh(16) → sigmoide, entrenada con Adam sobre BCE.

    Coherencia matemática (P2-03):
        L    = -mean( y·log(a2+ε) + (1-y)·log(1-a2+ε) )
        dL/dz2 = (a2 - y) / n          ← gradiente exacto de BCE con sigmoide
    Todos los parámetros (w1, b1, w2, b2) se actualizan con Adam (P2-04) y el
    estado de Adam completo se persiste para continuar el entrenamiento en
    llamadas posteriores (warm start). Early stopping real con restauración de
    la instantánea con mejor pérdida de validación (P2-05).
    """

    PARAMS = ("w1", "b1", "w2", "b2")

    def __init__(self, n_ocultas: int = 16, lr: float = 0.01, epocas_max: int = 1500, paciencia: int = 60,
                 l2: float = 1e-4, beta1: float = 0.9, beta2: float = 0.999, eps_adam: float = 1e-8) -> None:
        self.ruta = ruta_modelo("cerebral_profundo")
        self.n_ocultas, self.lr, self.epocas_max, self.paciencia = n_ocultas, lr, epocas_max, paciencia
        self.l2, self.beta1, self.beta2, self.eps_adam = l2, beta1, beta2, eps_adam
        self.eps_log = 1e-12

    # --- Inicialización / estado --------------------------------------------
    def _estado_inicial(self, n_features: int, rng: np.random.Generator) -> Dict[str, Any]:
        h = self.n_ocultas
        estado: Dict[str, Any] = {
            "w1": rng.normal(0, np.sqrt(1.0 / n_features), (n_features, h)),
            "b1": np.zeros((1, h)),
            "w2": rng.normal(0, np.sqrt(1.0 / h), (h, 1)),
            "b2": np.zeros((1, 1)),
            "t": 0,
            "n_features": n_features,
            "n_ocultas": h,
            "version": 0,
        }
        for p in self.PARAMS:  # mw1, vw1, mb1, vb1, mw2, vw2, mb2, vb2
            estado[f"m{p}"] = np.zeros_like(estado[p])
            estado[f"v{p}"] = np.zeros_like(estado[p])
        return estado

    @staticmethod
    def _sigmoide(z: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))

    def _forward(self, X: np.ndarray, e: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        a1 = np.tanh(X @ e["w1"] + e["b1"])
        a2 = self._sigmoide(a1 @ e["w2"] + e["b2"])
        return a1, a2

    def _bce(self, a2: np.ndarray, y: np.ndarray) -> float:
        return float(-np.mean(y * np.log(a2 + self.eps_log) + (1 - y) * np.log(1 - a2 + self.eps_log)))

    def _paso_adam(self, X: np.ndarray, y: np.ndarray, e: Dict[str, Any]) -> None:
        n = len(X)
        a1, a2 = self._forward(X, e)
        dz2 = (a2 - y) / n                                 # BCE + sigmoide
        grads = {
            "w2": a1.T @ dz2 + self.l2 * e["w2"],
            "b2": dz2.sum(axis=0, keepdims=True),
        }
        dz1 = (dz2 @ e["w2"].T) * (1.0 - a1 ** 2)          # derivada de tanh
        grads["w1"] = X.T @ dz1 + self.l2 * e["w1"]
        grads["b1"] = dz1.sum(axis=0, keepdims=True)
        e["t"] += 1
        t = e["t"]
        for p in self.PARAMS:                               # Adam también en biases
            g = grads[p]
            e[f"m{p}"] = self.beta1 * e[f"m{p}"] + (1 - self.beta1) * g
            e[f"v{p}"] = self.beta2 * e[f"v{p}"] + (1 - self.beta2) * (g ** 2)
            m_hat = e[f"m{p}"] / (1 - self.beta1 ** t)
            v_hat = e[f"v{p}"] / (1 - self.beta2 ** t)
            e[p] = e[p] - self.lr * m_hat / (np.sqrt(v_hat) + self.eps_adam)

    def predecir_proba(self, X: np.ndarray, e: Dict[str, Any]) -> np.ndarray:
        Xn = (X - e["media"]) / e["desv"]
        return self._forward(Xn, e)[1].ravel()

    # --- Entrenamiento ------------------------------------------------------
    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        if not set(np.unique(y).tolist()).issubset({0, 1}) or len(np.unique(y)) < 2:
            raise ErrorValidacionDatos("El agente profundo requiere targets binarios con ambas clases presentes.")
        rng = np.random.default_rng(semilla)
        with FileLock(f"{self.ruta}.train.lock", timeout=600):
            previo = cargar_modelo_seguro(self.ruta)
            warm_start = bool(previo is not None and previo.get("n_features") == X.shape[1]
                              and previo.get("n_ocultas") == self.n_ocultas)
            if warm_start:
                e = previo
            else:
                e = self._estado_inicial(X.shape[1], rng)
                e["media"] = X.mean(axis=0)
                desv = X.std(axis=0)
                e["desv"] = np.where(desv > 1e-12, desv, 1.0)

            X_tr, X_te, y_tr, y_te, conjunto = dividir_holdout(X, y, semilla)
            # Validación interna para early stopping (sin tocar el conjunto de test)
            usa_val = len(y_tr) >= 30 and np.bincount(y_tr).min() >= 4
            if usa_val:
                X_fit, X_val, y_fit, y_val = train_test_split(X_tr, y_tr, test_size=0.15, stratify=y_tr, random_state=semilla)
            else:
                X_fit, X_val, y_fit, y_val = X_tr, X_tr, y_tr, y_tr
            norm = lambda A: (A - e["media"]) / e["desv"]  # noqa: E731
            Xf, Xv = norm(X_fit), norm(X_val)
            yf, yv = y_fit.reshape(-1, 1).astype(float), y_val.reshape(-1, 1).astype(float)

            mejor_loss, mejor_epoca, sin_mejora = float("inf"), 0, 0
            mejor_estado: Dict[str, Any] = {}
            epoca = 0
            for epoca in range(1, self.epocas_max + 1):
                self._paso_adam(Xf, yf, e)
                loss_monitor = self._bce(self._forward(Xv, e)[1], yv)
                if loss_monitor < mejor_loss - 1e-6:
                    mejor_loss, mejor_epoca, sin_mejora = loss_monitor, epoca, 0
                    # Instantánea de pesos + estado Adam (best_w1, best_b1, best_w2, best_b2, ...)
                    mejor_estado = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in e.items()}
                else:
                    sin_mejora += 1
                    if sin_mejora >= self.paciencia:
                        break
            detenido_temprano = sin_mejora >= self.paciencia
            e.update(mejor_estado)  # restauración de los mejores pesos
            e["version"] = int(e.get("version", 0)) + 1
            e["entrenado_en"] = ahora_utc().isoformat()

            bce_train = self._bce(self._forward(Xf, e)[1], yf)
            proba_te = self.predecir_proba(X_te, e)
            metricas = calcular_metricas_clasificacion(y_te, (proba_te >= 0.5).astype(int), proba_te)
            metricas.update({
                "bce_loss_final": round(bce_train, 6),
                "bce_loss_monitor_mejor": round(mejor_loss, 6),
                "bce_loss_evaluacion": round(self._bce(proba_te.reshape(-1, 1), y_te.reshape(-1, 1).astype(float)), 6),
                "conjunto_evaluacion": conjunto,
                "monitor_early_stopping": "validacion_interna_15pct" if usa_val else "entrenamiento",
            })
            guardar_modelo_seguro(e, self.ruta)

        return {
            "agente": "cerebral_profundo",
            "version_modelo": e["version"],
            "warm_start_desde_estado_persistido": warm_start,
            "entrenamiento": {"epocas_ejecutadas": epoca, "mejor_epoca": mejor_epoca,
                              "early_stopping_activado": detenido_temprano, "paso_adam_t": int(e["t"])},
            "metricas": metricas,
        }
