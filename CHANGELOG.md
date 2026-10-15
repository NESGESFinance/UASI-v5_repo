# Changelog

Todos los cambios relevantes de UASI se documentan en este archivo.
El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
sigue [Versionado Semántico](https://semver.org/lang/es/).

---

## [5.1.0-enterprise] - 2026-10-12

Revisión correctiva **P1 + P2 + P3**, basada en el informe técnico ejecutivo sobre la v5.0.0.

### Seguridad (P1 · crítico)
- **P1-01** Autenticación JWT Bearer (HS256, con `iss`, `sub`, `iat`, `exp` y `jti`) obligatoria en
  todos los endpoints salvo `/api/v5/health`. Si falta `SECRET_JWT_KEY`, el sistema falla cerrado (503).
- **P1-04** `GuardiánGobernanzaSeguridad` en todos los endpoints (v1, master y streaming): allow-list
  de intenciones, con deny-list como defensa en profundidad.
- **P1-05** Sanitización de NaN, ±Inf y None en todos los flujos, incluido el streaming. La imputación
  por columna se documenta en la respuesta (`sanitizacion`).
- **P1-06** Validación dimensional (matriz rectangular, `len(inputs)==len(targets)`, `n_features`
  coherente con el modelo persistido) y de binariedad.
- **P1-09** Rate limiting por IP con slowapi (`RATE_LIMIT_PER_MINUTE`, 30 por defecto, con soporte para Redis).
- **P1-10** Límite de payload a nivel ASGI (`MAX_PAYLOAD_BYTES`, 10 MB) → 413.

### Corregido (P1)
- **P1-02** El trabajo CPU-bound se mueve a `run_in_threadpool`, de modo que ya no bloquea el event loop (CWE-400).
- **P1-03** Se eliminan las *race conditions*: `threading.RLock` + `filelock.FileLock` y escritura atómica
  con `os.replace`.
- **P1-07** `datetime.utcnow()` (obsoleto) se sustituye por `datetime.now(timezone.utc)`.
- **P1-08** El streaming admite clases dinámicas (`np.unique(y)` + `clases_declaradas`); ya no hay `[0, 1]` fijo.
  Las clases nuevas en un modelo existente devuelven 409.

### Corregido (P2 · corto plazo)
- **P2-01** Métricas honestas sobre un hold-out estratificado (accuracy, precision, recall, F1 y ROC-AUC).
  Si no hay datos suficientes, se etiquetan como `entrenamiento_optimista`.
- **P2-02** `pickle` se sustituye por `joblib` + checksum SHA-256 (HMAC-SHA256 con `UASI_MODEL_HMAC_KEY`),
  que se verifica **antes** de deserializar.
- **P2-03** Red profunda: se reporta la BCE que realmente se optimiza (antes se reportaba MSE); el
  gradiente es coherente (`dz2 = (a2 - y)/n`).
- **P2-04** Adam se aplica también a los biases y su estado completo (`mw1, vw1, mw2, vw2, mb1, vb1, mb2, vb2, t`) se persiste.
- **P2-05** *Early stopping* real, con restauración de los mejores pesos y de su estado de Adam.
- **P2-06** CV segura en SVM: `StratifiedKFold(min(3, min_clase))`, con LeaveOneOut como alternativa.
- **P2-07** Batería de tests pytest (`tests/test_uasi_v5.py`, 18 tests).

### Añadido (P3 · estratégico)
- **P3-01** Ledger JSONL append-only, hash-encadenado y con locks.
- **P3-02** Raíz de Merkle diaria con separación de dominio entre hoja (`0x00`) y nodo (`0x01`).
- **P3-03** Anclaje en **Bitcoin L1** mediante OpenTimestamps (`GET /api/v5/merkle-anchor`), con una
  prueba `.ots` por anclaje.
- **P3-04** Verificación de las pruebas (`GET /api/v5/verify-ots/{fecha}`): se recalcula la raíz, se hace
  *upgrade* contra los calendarios y se contrasta la cabecera de bloque con Esplora.
- **P3-05** Gobernanza declarativa y logging estructurado en JSON.
- **P3-06** Metacognición operativa: la entropía de Shannon decide de verdad la ruta del orquestador.
- `GET /api/v5/health` detallado (workspace, checksums de modelos, integridad del ledger, autenticación
  y OTS) y `GET /api/v5/token` solo para desarrollo.

### Repositorio
- Estructura de paquete `src/uasi_v5`, `pyproject.toml`, `Makefile`, scripts operativos, Docker
  multi-stage con Compose + Redis y documentación en `docs/`.

---

## [5.0.0-ENTERPRISE-UNIFIED]

### Añadido
- `AgenteOrquestadorSemantico`: interpreta un prompt en lenguaje natural y selecciona el agente.
- `AgenteAnalistaDatosMetacognitivo`: perfila el dataset con la entropía de Shannon.
- Endpoints `/api/v5/uasi-master-execute`, `/api/v5/streaming-ingest` y `/api/v5/cryptographic-ledger`.
- `GuardiánGobernanzaSeguridad`: filtrado de intenciones mediante deny-list.
- Unificación de los agentes v1–v4 en un solo servicio.

### Problemas conocidos (corregidos en 5.1.0)
- Ningún endpoint tenía autenticación.
- El entrenamiento síncrono bloqueaba el event loop (CWE-400).
- La persistencia con `pickle` permitía RCE y había *race conditions* entre workers.
- La deny-list se podía evadir (Unicode o sinónimos).
- La red profunda calculaba BCE pero reportaba MSE y no persistía el estado de Adam.
- Las métricas eran *in-sample* (sobre los datos de entrenamiento).
- El ledger podía recomputarse entero porque no tenía anclaje externo.

---

## [4.0]

### Añadido
- `MotorStreamingContinuo`: aprendizaje online incremental con `SGDClassifier.partial_fit`.

---

## [3.0]

### Añadido
- `AgenteCerebralProfundo`: red profunda con optimizador Adam y activación Swish.
- `AgenteEnsambladoExplicable`: Gradient Boosting con importancia de características.
- `AgenteVectorialEvolutivo`: SVM con `RandomizedSearchCV`.
- Ledger de auditoría con encadenamiento SHA-256.

---

## [2.0]

### Añadido
- Agente SVM con optimización de hiperparámetros mediante `GridSearchCV`.

---

## [1.0]

### Añadido
- `AgenteRedNeuronalLegacy`: MLP con retropropagación y activación sigmoide.
- Agente de Árbol de Decisión.
- Endpoint `POST /api/v1/procesar`.
- `GuardiánAuditoríaLegacy`: registro en JSON plano, sin encadenamiento hash.

[5.1.0-enterprise]: #510-enterprise---2026-10-12
[5.0.0-ENTERPRISE-UNIFIED]: #500-enterprise-unified
[4.0]: #40
[3.0]: #30
[2.0]: #20
[1.0]: #10
