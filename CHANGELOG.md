# Changelog

Todos los cambios relevantes de UASI se documentan en este archivo.
El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
sigue [Versionado Semántico](https://semver.org/lang/es/).

---

## [5.2.2-enterprise] - 2026-10-18

Estado JEV compartido entre workers, integración continua y limpieza de lint. No cambia la API.

### Añadido
- `agentes_jev/automatizacion_redis.py` con `CacheJEVRedis`, `MetricasJEVRedis` y `RegistroIdempotenciaJEVRedis`,
  con la misma interfaz que las clases en memoria:
  - caché `uasi:jev:cache:{clave}` (JSON, TTL con EX) con LRU en el sorted set `uasi:jev:cache:lru`;
  - métricas con incrementos atómicos bajo `uasi:jev:metricas:*`, agregadas entre todos los workers;
  - idempotencia con `SET NX EX` sobre `uasi:jev:idem:{id_solicitud}`: un reintento que cae en otro worker ya no
    se re-ejecuta.
- `crear_backend_jev(uri)`: usa Redis si `RATE_LIMIT_STORAGE_URI` es `redis://` y responde a PING; si no, vuelve a
  las clases en memoria y registra un aviso. `servicios.py` crea así `cache_jev`, `metricas_jev` e `idempotencia_jev`.
- `/api/v5/jev/metricas` → `cache.backend` indica `redis` o `memoria`.
- CI en GitHub Actions (`.github/workflows/ci.yml`): `pip-audit`, `black --check`, `ruff`, `mypy` y `pytest` en
  Python 3.11 para push y pull request sobre `main`. Badge de CI en el README.

### Cambiado
- `redis==8.1.0` pasa a `requirements.txt` (antes solo en desarrollo). El Dockerfile deja de instalarlo aparte.
- `requirements-dev.txt` añade `fakeredis==2.40.0` (tests del backend Redis) y `pip-audit==2.10.1` (CI).
- El dispatcher acepta cualquier caché que cumpla el protocolo `CacheJEV`.
- Ruff: los 14 avisos heredados quedan corregidos (11 automáticos: UP015, UP017, UP037, UP009, I001; 3 manuales:
  B905 con `zip(..., strict=False)` en `merkle_ots.py`, `ensamblado.py` y `metacognitivo.py`).
  `ruff check src tests clientes` → *All checks passed!*

### Docker
- Construcción de la imagen no verificada en esta versión: el entorno de desarrollo no dispone de daemon Docker.
  Se validó el mismo comando de arranque (gunicorn + 4 UvicornWorker) sobre un entorno limpio con
  `requirements.txt`.

### Tests
- Nuevos: `test_backend_redis_no_disponible_usa_memory`, `test_cache_redis_compartida_y_lru`,
  `test_metricas_redis_agregadas_entre_workers` y `test_idempotencia_redis_atomica_entre_workers` (dos «workers»
  sobre el mismo Redis simulado). Total: **52 passed, 1 skipped**.

---

## [5.2.1-enterprise] - 2026-10-18

Versión de seguridad. No cambia la API: los tokens HS256 emitidos antes siguen siendo válidos.

### Seguridad
- `python-jose` se sustituye por **PyJWT 2.15.1**. Así se eliminan dos vulnerabilidades sin parche:
  - CVE-2026-85394 / GHSA-3qf3-8w2g-rqmx (`python-jose` ≤ 3.5.0, confusión de clave asimétrica en HMAC).
  - CVE-2024-23342 / GHSA-wj6h-64fc-37mp (`ecdsa`, ataque de tiempo Minerva). Era una dependencia transitiva de `python-jose`.
- `JWT_ALGORITHM` solo admite algoritmos HMAC (HS256/HS384/HS512). Con cualquier otro valor (RS\*, ES\*, `none`):
  - `require_auth` responde 503 (fail-closed).
  - `emitir_token` lanza `RuntimeError`.
- La validación exige los claims `exp`, `iat`, `sub` e `iss`. Las excepciones de PyJWT se encadenan con `from None`
  para no filtrar detalles internos.
- Se eliminan `passlib[bcrypt]` y `bcrypt`: no se usaban y reducían la superficie de ataque.
- `pip-audit -r requirements.txt` → *No known vulnerabilities found*.

### Tests
- Nuevos: `test_jwt_rechaza_alg_none_y_sin_claims` (alg `none`, emisor ajeno, sin `sub`, algoritmo distinto) y
  `test_jwt_algoritmo_no_hmac_falla_cerrado`. Total: **48 passed, 1 skipped**.

---

## [5.2.0-enterprise] - 2026-10-16

Arquitectura modular por capas y nueva capa multiagente **JEV → UASI → JEV**. Es compatible con la
v5.1.0: los endpoints existentes y `from uasi_v5.main import ...` se mantienen.

### Cambiado
- El antiguo `main.py` único se divide en el paquete `src/uasi_v5/` con subpaquetes `core`, `esquemas`,
  `seguridad`, `persistencia`, `ml`, `orquestacion`, `auditoria`, `agentes_jev` y `api`. Los singletons
  se cablean en `servicios.py`; `main.py` queda como fachada de reexportación.
- Versión 5.2.0-enterprise en `pyproject.toml`, `__init__`, configuración, Makefile y Docker.

### Añadido
- **Capa JEV**: `POST /api/v5/jev-dispatch` recibe una `SolicitudJEV` firmada, la despacha en paralelo a
  agentes especializados (`python`, `typescript`, `javascript`, `solidity`, `rust`, `go`, `java`) y devuelve
  una `RespuestaJEV` firmada con inferencia de tipos, código binding y resumen por lenguaje.
- `GET /api/v5/jev/agentes`: lenguajes y tipos de tarea disponibles.
- `DispatcherMultiagenteJEV`: `ThreadPoolExecutor` (`JEV_MAX_WORKERS`), aislamiento de errores por agente,
  estado global `exito` / `error_parcial` / `error` e inyección de la integridad del ledger
  (`AUDITORIA_LEDGER`) y del health check (`EVALUACION_MODELO`).
- Agentes deterministas que **no ejecutan código**; `hash_resultado` = SHA-256 del JSON canónico.
- Evento de ledger `DISPATCH_JEV` con los hashes de datos y de resultados.
- Variables `JEV_HMAC_KEY` y `JEV_MAX_WORKERS`; aviso al arrancar si falta `JEV_HMAC_KEY`.
- `tests/test_jev.py` (12 tests).
- **Automatización JEV:** `POST /api/v5/jev-dispatch/lote` (lotes de hasta `JEV_LOTE_MAX` solicitudes, cada una
  firmada y registrada por separado) y `GET /api/v5/jev/metricas` (contadores por lenguaje, tarea, rechazos,
  caché y callbacks).
- **Optimización JEV:** caché determinista LRU + TTL de resultados de agentes (`JEV_CACHE_MAX`, `JEV_CACHE_TTL`;
  campo `desde_cache`), excluyendo `AUDITORIA_LEDGER` y `EVALUACION_MODELO`.
- **Idempotencia** por `id_solicitud`: un reintento idéntico devuelve la misma respuesta firmada sin re-ejecutar.
- **Callback automático** opcional a JEV (`metadatos.url_callback`) con reintentos, backoff y evento `CALLBACK_JEV`.
- SDK de referencia `clientes/python/jev_cliente.py` (stdlib) y `clientes/typescript/jevCliente.ts` (Node ≥ 18),
  compatibles byte a byte con el JSON canónico del servidor.
- `tests/test_jev_automatizacion.py` (17 tests). Total: 46 pasan, 1 omitido (requiere red).

### Seguridad
- `seguridad/jev_firma.py`: HMAC-SHA256 sobre el JSON canónico sin campos de firma, comparación en tiempo
  constante (`hmac.compare_digest`) y fail-closed: sin clave → 503, firma inválida → 401. Además del JWT.
- `seguridad/jev_replay.py`: ventana temporal del `timestamp` (`JEV_VENTANA_SEGUNDOS`, 401 fuera de ventana) y
  409 si un `id_solicitud` se reutiliza con otro cuerpo.
- Callbacks deny-by-default contra SSRF: lista blanca `JEV_CALLBACK_HOSTS`, solo `https` (salvo localhost),
  sin credenciales en la URL y sin redirecciones.

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

[5.2.2-enterprise]: #522-enterprise---2026-10-18
[5.2.1-enterprise]: #521-enterprise---2026-10-18
[5.2.0-enterprise]: #520-enterprise---2026-10-16
[5.1.0-enterprise]: #510-enterprise---2026-10-12
[5.0.0-ENTERPRISE-UNIFIED]: #500-enterprise-unified
[4.0]: #40
[3.0]: #30
[2.0]: #20
[1.0]: #10
