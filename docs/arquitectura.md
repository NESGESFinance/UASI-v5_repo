# Arquitectura de UASI v5 Enterprise (5.1.0-enterprise)

## 1. Visión general

UASI v5 es un servicio **FastAPI** monolítico y modular (`src/uasi_v5/main.py`) con tres planos:

| Plano | Componentes | Función |
|---|---|---|
| **Control y seguridad** | `LimiteTamanoPayloadMiddleware`, SlowAPI, `require_auth`, `GuardiánGobernanzaSeguridad` | Limitar, autenticar, autorizar por intención y sanear los datos de entrada. |
| **Cómputo (ML)** | Agentes legacy, `AgenteCerebralProfundo`, `AgenteVectorialEvolutivo`, `AgenteEnsambladoExplicable`, `MotorStreamingContinuo`, `AgenteAnalistaDatosMetacognitivo`, `OrquestadorSemanticoUASI` | Entrenar y evaluar modelos con métricas honestas. |
| **Auditoría y confianza** | `LedgerAuditoriaCriptografica`, `MerkleOpenTimestampsManager` | Registro inmutable, raíz de Merkle diaria y anclaje en Bitcoin L1. |

## 2. Ciclo de vida de una petición

```text
Petición HTTP
  │
  ├─ 1. LimiteTamanoPayloadMiddleware (ASGI, el más externo) ── > MAX_PAYLOAD_BYTES ──► 413
  ├─ 2. SlowAPIMiddleware ── > RATE_LIMIT_PER_MINUTE por IP ───────────────────────► 429
  ├─ 3. Pydantic v2 (extra=forbid, tipos y validadores de modelo) ─────────────────► 422
  ├─ 4. require_auth ── SECRET_JWT_KEY ausente ► 503 · token ausente/inválido/expirado ► 401
  ├─ 5. Guardián ── intención fuera de la allow-list o en la deny-list ─────────────► 403
  │                 sanitización NaN/±Inf/None + validación dimensional ──────────► 422
  ├─ 6. run_in_threadpool(agente.entrenar | orquestador.ejecutar | motor.ingerir)
  │        └─ persistencia: joblib + checksum/HMAC, RLock + FileLock, os.replace
  ├─ 7. run_in_threadpool(ledger.registrar(evento, datos, actor=claims["sub"]))
  └─ 8. Respuesta JSON: id_ejecucion, métricas, bloque_ledger, hash_bloque
```

Las excepciones de dominio se traducen a HTTP mediante manejadores globales:

| Excepción | HTTP |
|---|---|
| `ErrorValidacionDatos` | 422 |
| `ErrorGobernanza` | 403 |
| `ConflictoClasesStreaming` | 409 |
| `IntegridadModeloError` | 500 |
| `Exception` (no controlada) | 500 genérico, sin filtrar detalles |

## 3. Seguridad

- **JWT** (`PyJWT`): HS256 por defecto, con `iss`, `sub`, `iat`, `exp` y `jti` obligatorios. El `sub`
  queda registrado como `actor` en el ledger.
- **Falla cerrado**: si falta `SECRET_JWT_KEY`, los endpoints protegidos devuelven 503, no se abren.
- **Gobernanza**: el texto se normaliza con Unicode NFKD y se compara con la allow-list de intenciones;
  la deny-list actúa como segunda barrera. Los metadatos de contexto también se validan.
- **Integridad de modelos**: un checksum SHA-256 (o HMAC-SHA256 si `UASI_MODEL_HMAC_KEY` está definida)
  se verifica antes de `joblib.load`. Si no coincide, se lanza `IntegridadModeloError` y no se deserializa nada.
- **Concurrencia**: `threading.RLock` dentro del proceso y `filelock.FileLock` entre workers de Gunicorn.

## 4. Plano de cómputo

### 4.1 Endpoint v1 (legacy)
| `tipo_agente` | Clase | Modelo |
|---|---|---|
| `red_neuronal` | `AgenteRedNeuronalLegacy` | `MLPClassifier(16, 8)` |
| `svm` | `AgenteSVMLegacy` | `StandardScaler + SVC(rbf)` con `_GridSearchSeguro` (StratifiedKFold / LeaveOneOut) |
| `arbol_decision` | `AgenteArbolDecisionLegacy` | `DecisionTreeClassifier(max_depth=5)` |

### 4.2 Orquestador semántico (master)
El guardián clasifica `prompt_intencion` en categorías. El orquestador elige el plan según este orden
de prioridad:

| Prioridad | Categoría | Efecto real |
|---|---|---|
| 1 | `DIAGNOSTICO` | *Health check* detallado. |
| 2 | `AUDITORIA` | Integridad del ledger + raíz de Merkle del día. |
| 3 | `APRENDIZAJE_ONLINE_CONTINUO` | Fuerza la ruta de streaming. |
| 4 | `EXPLICABILIDAD` | Gradient Boosting con importancia de características. |
| 5 | `OPTIMIZACION_EVOLUTIVA` | SVM evolutivo. |
| 6 | `APRENDIZAJE_PROFUNDO` | Red profunda. |
| 7 | `ANALISIS_METACOGNITIVO_AUTO` | La ruta la decide el analista metacognitivo. |

### 4.3 Metacognición (entropía de Shannon)
`AgenteAnalistaDatosMetacognitivo` calcula:

- **H_norm de clases** = H / log2(K). Si es menor que `UASI_UMBRAL_ENTROPIA_DESBALANCE` (0,85), hay
  desbalance y se usa **Gradient Boosting**.
- **Entropía media de características** (histograma de 10 bins, normalizada). Si es mayor que
  `UASI_UMBRAL_ENTROPIA_FEATURES` (0,90) y n ≥ 100, la distribución es compleja y se usa **Gradient Boosting**.
- Si n ≥ 200 y el problema es binario, se usa la **red profunda**. En cualquier otro caso, el **SVM evolutivo**.

### 4.4 Red profunda (`AgenteCerebralProfundo`)
Implementación en NumPy: capa oculta Swish y salida sigmoide; pérdida BCE con gradiente coherente
`dz2 = (a2 - y)/n`; Adam en pesos **y biases**, con su estado persistido; *early stopping* con
restauración de los mejores pesos.

### 4.5 Streaming (`MotorStreamingContinuo`)
`StandardScaler.partial_fit` + `SGDClassifier(loss="log_loss").partial_fit`, con clases dinámicas y
`version_modelo` incremental. Antes de actualizar se calcula la métrica *prequential* (predecir y
luego aprender). Las clases nuevas en un modelo existente devuelven 409; `reiniciar_modelo=true`
empieza desde cero.

### 4.6 Métricas honestas
Si hay datos suficientes, se usa un hold-out estratificado y `conjunto_evaluacion` lo indica. Si no,
las métricas se calculan sobre el entrenamiento y se etiquetan como
`entrenamiento_optimista (datos insuficientes para hold-out)`.

## 5. Plano de auditoría

### 5.1 Ledger
Es un archivo JSONL append-only (`PATH_WORKSPACE/ledger/ledger_auditoria.jsonl`) con un bloque por
línea y un bloque GENESIS al arrancar:

```text
hash_datos  = SHA256(JSON canónico de datos)
hash_bloque = SHA256(JSON canónico de {indice, timestamp, fecha, evento, actor, datos, hash_datos, hash_previo})
```

Eventos: `GENESIS`, `PROCESAMIENTO_V1`, `EJECUCION_MAESTRA_V5`, `INGESTA_STREAMING` y `ANCLAJE_MERKLE_OTS`.
`verificar_integridad()` comprueba la continuidad de los índices, el encadenamiento, `hash_datos` y `hash_bloque`.

### 5.2 Merkle + OpenTimestamps
```text
hoja_i = SHA256(0x00 ‖ hash_bloque_i)
nodo   = SHA256(0x01 ‖ izq ‖ der)        (el nodo impar se promociona sin duplicarse)
```
La raíz del día se sella con los calendarios OTS. El número de bloques cubiertos (`n_bloques`) se
guarda en `<fecha>.json`, de modo que la verificación recalcula la raíz exactamente sobre esos bloques,
aunque después se hayan añadido otros (por ejemplo, el propio evento `ANCLAJE_MERKLE_OTS`).
Más detalles en [`bitcoin_opentimestamps.md`](bitcoin_opentimestamps.md).

## 6. Persistencia (`PATH_WORKSPACE`)

```text
PATH_WORKSPACE/
├── modelos/      # *.joblib + checksum (SHA-256/HMAC) + .lock
├── ledger/       # ledger_auditoria.jsonl (+ .lock)
└── ots_proofs/   # <fecha>.ots · <fecha>_<sello>.ots · <fecha>.json
```

## 7. Despliegue

- **Gunicorn + UvicornWorker** (`uasi_v5.main:app`, `PYTHONPATH=src`), con 4 workers por defecto.
- Con varios workers, `RATE_LIMIT_STORAGE_URI=redis://...` hace que el límite se comparta entre ellos.
- **Docker**: imagen multi-stage `python:3.11-slim`, usuario no root (uid 10001), `tini`, HEALTHCHECK
  sobre `/api/v5/health` y volumen `/data`. Compose añade Redis 7.4, `read_only` y `cap_drop: [ALL]`.
- Guía completa: [`../DEPLOY_README.md`](../DEPLOY_README.md).

## 8. Variables de entorno principales

| Variable | Defecto | Uso |
|---|---|---|
| `UASI_ENV` | `development` | Con `production` se desactiva `/api/v5/token`. |
| `SECRET_JWT_KEY` | *(vacía)* | Obligatoria; sin ella los endpoints devuelven 503. |
| `JWT_ALGORITHM` / `JWT_ISSUER` / `JWT_EXP_MINUTES` | `HS256` / `uasi-v5` / `60` | Parámetros del JWT. |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_STORAGE_URI` | `30` / `memory://` | Rate limiting. |
| `MAX_PAYLOAD_BYTES` | `10485760` | Límite de payload. |
| `UASI_MAX_MUESTRAS` / `UASI_MAX_FEATURES` | `50000` / `512` | Límites del dataset. |
| `UASI_MODEL_HMAC_KEY` | *(vacía)* | Activa HMAC-SHA256 para los modelos. |
| `PATH_WORKSPACE` | `./uasi_workspace` | Raíz de la persistencia. |
| `BITCOIN_NETWORK` | `mainnet` | `mainnet` o `testnet`. |
| `OTS_CALENDARS` / `OTS_MIN_CALENDARS` / `OTS_TIMEOUT_SECONDS` | 3 calendarios públicos / `1` / `10` | OpenTimestamps. |
| `ESPLORA_API_URL` | `https://blockstream.info/api` | Contraste de las cabeceras de bloque. |
| `UASI_UMBRAL_ENTROPIA_DESBALANCE` / `UASI_UMBRAL_ENTROPIA_FEATURES` | `0.85` / `0.90` | Umbrales metacognitivos. |
| `LOG_LEVEL` | `INFO` | Nivel del log JSON. |

La lista completa y comentada está en [`../.env.example`](../.env.example).
