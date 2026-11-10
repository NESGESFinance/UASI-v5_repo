"""Privacidad diferencial: mecanismo gaussiano (ε=1.0, δ=1e-5) en las métricas publicadas."""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest


@pytest.fixture
def evaluacion(uasi):
    return sys.modules["uasi_v5.ml.evaluacion"]


def test_sigma_calibrada(evaluacion):
    mp = evaluacion.MetricasPrivadas(epsilon=1.0, delta=1e-5)
    assert mp.sigma(0.01) == pytest.approx(0.01 * math.sqrt(2 * math.log(1.25 / 1e-5)), rel=1e-12)


def test_ruido_gaussiano_dentro_del_rango_esperado(evaluacion):
    mp = evaluacion.MetricasPrivadas(epsilon=1.0, delta=1e-5, semilla=123)
    sens = 1 / 1000
    sigma = mp.sigma(sens)
    muestras = np.array([mp.publicar(0.5, sens, acotar=None) for _ in range(4000)])
    ruido = muestras - 0.5
    assert abs(ruido.mean()) < 4 * sigma / math.sqrt(len(ruido))  # insesgado
    assert ruido.std() == pytest.approx(sigma, rel=0.08)          # escala correcta
    assert np.all(np.abs(ruido) < 6 * sigma)                      # colas gaussianas
    assert mp.epsilon_consumido == pytest.approx(4000 * 1.0)


def test_recorte_en_rango_valido(evaluacion):
    mp = evaluacion.MetricasPrivadas(semilla=0)
    valores = [mp.publicar(0.999, 0.5) for _ in range(200)]
    assert all(0.0 <= v <= 1.0 for v in valores)


def test_parametros_invalidos(evaluacion):
    with pytest.raises(ValueError):
        evaluacion.MetricasPrivadas(epsilon=0)
    with pytest.raises(ValueError):
        evaluacion.MetricasPrivadas(delta=1.5)
    with pytest.raises(ValueError):
        evaluacion.MetricasPrivadas().publicar(0.5, 0)


def test_privatizar_resultado_elimina_pesos_gradientes_e_historiales(evaluacion):
    bruto = {"agente": "x", "metricas": {"accuracy": 0.9, "f1_score": 0.8, "bce_loss_final": 0.1, "n_evaluadas": 50},
             "pesos_capa1": [[1, 2]], "gradientes": [0.1], "entrenamiento": {"paso_adam_t": 10, "epocas": 3},
             "evolucion_fitness_accuracy_cv": [0.5, 0.6]}
    pub = evaluacion.privatizar_resultado(bruto, 200, activa=True)
    assert "pesos_capa1" not in pub and "gradientes" not in pub and "evolucion_fitness_accuracy_cv" not in pub
    assert "bce_loss_final" not in pub["metricas"] and "paso_adam_t" not in pub["entrenamiento"]
    dp = pub["privacidad_diferencial"]
    assert dp["metricas_privatizadas"] == 2 and dp["sensibilidad"] == pytest.approx(1 / 50)
    assert dp["epsilon_por_metrica"] == 1.0 and dp["delta"] == 1e-5


def test_endpoint_v1_publica_metricas_privadas(cliente, cabeceras, dataset_binario):
    X, y = dataset_binario(80)
    r = cliente.post("/api/v1/procesar", json={"tipo_agente": "arbol_decision", "inputs": X.tolist(),
                                                "targets": y.tolist()}, headers=cabeceras)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["privacidad_diferencial"]["activa"] is True
    assert cuerpo["privacidad_diferencial"]["metricas_privatizadas"] >= 4
    assert 0.0 <= cuerpo["metricas"]["accuracy"] <= 1.0
