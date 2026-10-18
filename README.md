# UASI v5 Enterprise Production

![versión](https://img.shields.io/badge/versión-5.2.1--enterprise-blue)
![estado](https://img.shields.io/badge/estado-producción-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![tests](https://img.shields.io/badge/tests-48%20passed%20%7C%201%20skipped-success)
![bitcoin](https://img.shields.io/badge/anclaje-Bitcoin%20L1%20·%20OpenTimestamps-F7931A?logo=bitcoin&logoColor=white)
![licencia](https://img.shields.io/badge/licencia-MIT-lightgrey)

**Sistema Unificado de Agentes de Aprendizaje con Supervisión Inteligente.** Es una API FastAPI que
reúne siete agentes de *machine learning* (legacy, profundos, evolutivos, explicables y de *streaming*)
bajo un orquestador semántico con metacognición. Todos los endpoints de negocio requieren autenticación
JWT y todas las peticiones pasan por un guardián de gobernanza. Cada operación queda registrada en un
**ledger criptográfico hash-encadenado**, cuya raíz de Merkle diaria se **ancla en Bitcoin L1** mediante
OpenTimestamps.

> Desde la v5.2.0 el servicio es un **paquete modular por capas** en [`src/uasi_v5/`](src/uasi_v5/)
> (`core`, `esquemas`, `seguridad`, `persistencia`, `ml`, `orquestacion`, `agentes_jev`, `auditoria`, `api`).
> [`src/uasi_v5/main.py`](src/uasi_v5/main.py) es una fachada que reexporta la API pública, de modo que
> `uasi_v5.main:app` y `from uasi_v5.main import ...` siguen funcionando. Incluye la nueva
> [capa multiagente JEV](#capa-multiagente-jev).

---

## Índice

1. [Arquitectura](#arquitectura)
2. [Módulos](#módulos)
3. [Correcciones P1 / P2 / P3](#correcciones-p1--p2--p3)
4. [Requisitos](#requisitos)
5. [Instalación en 5 pasos](#instalación-en-5-pasos)
6. [Uso de la API (curl)](#uso-de-la-api-curl)
7. [Capa multiagente JEV](#capa-multiagente-jev)
8. [Anclaje en Bitcoin L1](#anclaje-en-bitcoin-l1)
9. [Estructura del repositorio](#estructura-del-repositorio)
10. [Desarrollo, tests y Docker](#desarrollo-tests-y-docker)
11. [Documentación adicional](#documentación-adicional)

---

## Arquitectura

```text
                 ┌──────────────────────────────────────────────────────────────┐
  Cliente ──────►│ LimiteTamanoPayloadMiddleware (10 MB) → SlowAPI (30 req/min) │
  (Bearer JWT)   └──────────────────────────────┬───────────────────────────────┘
                                                ▼
                       require_auth (JWT HS256 · iss/sub/iat/exp · falla cerrado)
                                                ▼
                 GuardiánGobernanzaSeguridad (allow-list + deny-list + sanitización
                         NaN/±Inf/None + validación dimensional y de binariedad)
                                                ▼
     ┌────────────────────────┬─────────────────┴───────────┬──────────────────────────┐
     ▼                        ▼                             ▼                          ▼
 /api/v1/procesar   /api/v5/uasi-master-execute   /api/v5/streaming-ingest   /api/v5/cryptographic-ledger
 Agentes legacy     OrquestadorSemanticoUASI      MotorStreamingContinuo     /merkle-anchor · /verify-ots
 (MLP, SVM, Árbol)  + AgenteAnalistaMetacognitivo (SGD · partial_fit)
                    → Cerebral / Vectorial / Ensamblado
     └────────────── run_in_threadpool (CPU-bound fuera del event loop) ─────────────────┘
                                                ▼
          Persistencia de modelos: joblib + checksum SHA-256/HMAC · RLock + FileLock · os.replace
                                                ▼
          LedgerAuditoriaCriptografica (JSONL append-only, SHA-256 encadenado)
                                                ▼
          MerkleOpenTimestampsManager → raíz de Merkle diaria → calendarios OTS → Bitcoin (OP_RETURN)
```

Además, `POST /api/v5/jev-dispatch` (JWT + firma HMAC JEV) alimenta el `DispatcherMultiagenteJEV`; ver
[Capa multiagente JEV](#capa-multiagente-jev).

Hay una descripción más detallada en [`docs/arquitectura.md`](docs/arquitectura.md).

## Módulos

| Módulo / clase | Versión de origen | Responsabilidad |
|---|---|---|
| `require_auth` / `emitir_token` | 5.1 | Emisión y validación de JWT Bearer con PyJWT (solo HS256/HS384/HS512). Responde 503 si falta `SECRET_JWT_KEY` o si `JWT_ALGORITHM` no es HMAC. |
| `LimiteTamanoPayloadMiddleware` | 5.1 | Corta a nivel ASGI los payloads mayores que `MAX_PAYLOAD_BYTES` (413). |
| `GuardiánGobernanzaSeguridad` | 5.0 → 5.1 | Allow-list de intenciones, deny-list, sanitización de matrices, validación de metadatos y clasificación del prompt maestro. |
| `AgenteRedNeuronalLegacy` | 1.0 | MLP clásico (`MLPClassifier`) del endpoint v1. |
| `AgenteSVMLegacy` + `_GridSearchSeguro` | 2.0 | SVM con `GridSearchCV` y CV segura (StratifiedKFold / LeaveOneOut). |
| `AgenteArbolDecisionLegacy` | 1.0 | Árbol de decisión del endpoint v1. |
| `AgenteCerebralProfundo` | 3.0 | Red profunda NumPy: Swish + sigmoide, BCE, Adam completo y *early stopping* real. |
| `AgenteVectorialEvolutivo` | 3.0 → 5.1 | SVM RBF con un algoritmo evolutivo sobre `(log10 C, log10 gamma)`. En la v3 usaba `RandomizedSearchCV`. |
| `AgenteEnsambladoExplicable` | 3.0 | Gradient Boosting con importancia de características (explicabilidad). |
| `MotorStreamingContinuo` | 4.0 | `SGDClassifier.partial_fit` con clases dinámicas y versionado de modelo. |
| `AgenteAnalistaDatosMetacognitivo` | 5.0 | Entropía de Shannon (de las clases y de las características) que condiciona el enrutamiento. |
| `OrquestadorSemanticoUASI` | 5.0 | Traduce el prompt a categorías y a un plan que se ejecuta de verdad. |
| `LedgerAuditoriaCriptografica` | 3.0 → 5.1 | Ledger JSONL append-only, encadenado con SHA-256, paginación y verificación de integridad. |
| `MerkleOpenTimestampsManager` | 5.1 | Raíz de Merkle diaria, sellado OTS, *upgrade* y verificación contra Bitcoin. |
| `estado_salud_detallado` | 5.1 | *Health check* de workspace, modelos (checksum), ledger, autenticación y OTS. |

## Correcciones P1 / P2 / P3

### P1 · Crítico (inmediato)

| ID | Corrección |
|---|---|
| P1-01 | Autenticación JWT Bearer obligatoria en todos los endpoints salvo `/api/v5/health`. Si falta `SECRET_JWT_KEY`, el sistema falla cerrado (503). |
| P1-02 | Todo el trabajo CPU-bound (entrenamiento, CV, verificación de la cadena) se ejecuta en `run_in_threadpool`, sin bloquear el event loop (CWE-400). |
| P1-03 | Exclusión mutua en todas las escrituras con `threading.RLock` y `filelock.FileLock`, y escritura atómica con archivo temporal + `os.replace`. |
| P1-04 | El guardián de gobernanza se aplica a todos los endpoints con allow-list de intenciones y, como defensa en profundidad, deny-list. |
| P1-05 | Sanitización de NaN, ±Inf y None en todos los flujos (también en streaming); la imputación queda documentada en la respuesta. |
| P1-06 | Validación dimensional (matriz rectangular, `len(inputs)==len(targets)`, `n_features` coherente con el modelo persistido) y de binariedad. |
| P1-07 | `datetime.utcnow()` se sustituye por `datetime.now(timezone.utc)`. |
| P1-08 | Streaming con clases dinámicas (`np.unique(y)` + clases declaradas), sin `[0, 1]` fijo en el código. |
| P1-09 | Rate limiting por IP con slowapi (`RATE_LIMIT_PER_MINUTE`, 30 por defecto). |
| P1-10 | Límite de payload a nivel ASGI (10 MB por defecto). |

### P2 · Corto plazo

| ID | Corrección |
|---|---|
| P2-01 | Métricas honestas (accuracy, precision, recall, F1 y ROC-AUC) sobre un hold-out estratificado; si no hay datos suficientes, se etiquetan como `entrenamiento_optimista`. |
| P2-02 | `pickle` se sustituye por `joblib` + checksum SHA-256 (o HMAC-SHA256 con `UASI_MODEL_HMAC_KEY`), que se verifica antes de deserializar. |
| P2-03 | Pérdida y gradiente coherentes en la red profunda (BCE + sigmoide, `dz2 = (a2 - y)/n`). Se reporta BCE, no MSE. |
| P2-04 | Adam también se aplica a los biases y su estado completo (`mw1…vb2, t`) se persiste. |
| P2-05 | *Early stopping* real: se guarda una instantánea y se restauran los mejores pesos junto con su estado de Adam. |
| P2-06 | CV segura en SVM: `StratifiedKFold(min(3, min_clase))`, con LeaveOneOut como alternativa. |
| P2-07 | Batería de tests pytest (`tests/test_uasi_v5.py`). |

### P3 · Estratégico

| ID | Corrección |
|---|---|
| P3-01 | Ledger hash-encadenado append-only (JSONL) con locks. |
| P3-02 | Raíz de Merkle diaria con separación de dominio entre hoja (`0x00`) y nodo (`0x01`). |
| P3-03 | Anclaje de la raíz diaria en Bitcoin L1 mediante OpenTimestamps; se genera un `.ots` por cada anclaje. |
| P3-04 | Verificación de los `.ots`: se recalcula la raíz, se hace *upgrade* contra los calendarios y se contrasta la cabecera de bloque con Esplora. |
| P3-05 | Gobernanza declarativa (allow-list) y logging estructurado en JSON. |
| P3-06 | Metacognición operativa: la entropía de Shannon influye en el enrutamiento y el plan tiene un efecto real. |

## Requisitos

- **Python 3.11 o superior**.
- Dependencias de [`requirements.txt`](requirements.txt): FastAPI, Uvicorn/Gunicorn, NumPy, scikit-learn,
  joblib, PyJWT, slowapi, filelock, pydantic v2 y opentimestamps, entre otras.
- Opcional: Redis (rate limiting compartido entre workers), Docker y Docker Compose, el cliente
  `ots` (`opentimestamps-client`) y un nodo Bitcoin Core para verificar sin depender de terceros.

## Instalación en 5 pasos

```bash
# 1. Clonar el repositorio y entrar en él
git clone <url-del-repositorio> uasi_v5_repo && cd uasi_v5_repo

# 2. Crear el entorno virtual con Python 3.11+
python3.11 -m venv .venv && source .venv/bin/activate

# 3. Instalar las dependencias (las de desarrollo incluyen pytest, ruff, black y mypy)
pip install -r requirements.txt            # o: make dev

# 4. Configurar el entorno y generar los secretos
cp .env.example .env
./scripts/generar_secretos.sh --write      # escribe SECRET_JWT_KEY y UASI_MODEL_HMAC_KEY en .env

# 5. Arrancar el servicio
make run                                   # desarrollo: uvicorn con recarga en :8000
# make run-prod                            # producción: gunicorn + 4 UvicornWorker
```

La comprobación rápida es `curl http://localhost:8000/api/v5/health`. La documentación interactiva
de OpenAPI está en `http://localhost:8000/docs`.

## Uso de la API (curl)

Todos los endpoints salvo `/api/v5/health` requieren la cabecera `Authorization: Bearer <JWT>`.
En desarrollo (`UASI_ENV != production`) se puede obtener un token de prueba así:

```bash
export API=http://localhost:8000
export TOKEN=$(curl -s "$API/api/v5/token?sujeto=auditor&minutos=60" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

**1. Salud del sistema** (sin autenticación; responde 503 si el estado es crítico)
```bash
curl -s $API/api/v5/health
```

**2. Token de desarrollo** (devuelve 404 en producción)
```bash
curl -s "$API/api/v5/token?sujeto=auditor&minutos=60"
```

**3. Procesamiento legacy v1** (`tipo_agente`: `red_neuronal` | `svm` | `arbol_decision`)
```bash
curl -s -X POST $API/api/v1/procesar \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tipo_agente":"arbol_decision","intencion":"clasificacion_binaria",
       "inputs":[[0,0],[0,1],[1,0],[1,1],[0.1,0.2],[0.9,0.8],[0.2,0.1],[0.8,0.9]],
       "targets":[0,0,1,1,0,1,0,1],"metadatos_contexto":{"origen":"curl"}}'
```

**4. Ejecución maestra v5** (el orquestador semántico decide el plan)
```bash
curl -s -X POST $API/api/v5/uasi-master-execute \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt_intencion":"Entrena con aprendizaje profundo y explica el resultado",
       "inputs":[[0,0],[0,1],[1,0],[1,1],[0.1,0.2],[0.9,0.8],[0.2,0.1],[0.8,0.9]],
       "targets":[0,0,1,1,0,1,0,1]}'
```

**5. Ingesta en streaming** (aprendizaje online incremental)
```bash
curl -s -X POST $API/api/v5/streaming-ingest \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"intencion":"aprendizaje_online","inputs":[[0,0],[1,1],[0,1],[1,0]],"targets":[0,1,0,1]}'
```

**6. Ledger criptográfico** (paginado; `verificar=true` recorre la cadena completa)
```bash
curl -s "$API/api/v5/cryptographic-ledger?page=1&page_size=50&verificar=true" \
  -H "Authorization: Bearer $TOKEN"
```

**7. Anclaje de la raíz de Merkle en Bitcoin** (por defecto, el día UTC actual)
```bash
curl -s "$API/api/v5/merkle-anchor?fecha=$(date -u +%F)" -H "Authorization: Bearer $TOKEN"
```

**8. Verificación de la prueba OTS**
```bash
curl -s "$API/api/v5/verify-ots/$(date -u +%F)" -H "Authorization: Bearer $TOKEN"
```

La referencia completa (headers, bodies de request y de response y códigos de error) está en
[`docs/api_reference.md`](docs/api_reference.md).

## Capa multiagente JEV

**JEV → UASI (multiagente especializado) → JEV.** JEV emite datos exactos en una `SolicitudJEV` firmada
con HMAC-SHA256. UASI verifica el JWT y la firma, reparte la tarea en paralelo entre agentes de
`python`, `typescript`, `javascript`, `solidity`, `rust`, `go` y `java`, registra el evento
`DISPATCH_JEV` en el ledger y devuelve una `RespuestaJEV` firmada. Cada agente es determinista y **no
ejecuta código**: devuelve la inferencia de tipos de `datos`, el código binding (interface TS, struct
Solidity/Rust/Go, clase Java, dataclass Python, typedef JSDoc) y un resumen de la tarea.

| Código | Motivo |
|---|---|
| 200 | `estado` = `exito`, `error_parcial` o `error` (los agentes están aislados entre sí) |
| 401 | JWT ausente o inválido, o firma JEV inválida |
| 422 | Esquema inválido (por ejemplo, un lenguaje no soportado) |
| 503 | `JEV_HMAC_KEY` (o `SECRET_JWT_KEY`) no configurada |

Tipos de tarea: `INFERENCIA_MULTILENGUAJE`, `ANALISIS_DATOS`, `EVALUACION_MODELO`, `AUDITORIA_LEDGER`,
`SINCRONIZACION_RWA`, `VALIDACION_CRUZADA` y `GENERACION_BINDINGS`.

**Cómo firma JEV la solicitud (Python):** HMAC-SHA256 del JSON canónico del cuerpo **sin** `firma_hmac`.
```python
import hashlib, hmac, json, os, requests

cuerpo = {
    "id_jev": "jev-2026-10-16-0001",
    "tipo_tarea": "SINCRONIZACION_RWA",
    "lenguajes_destino": ["solidity", "typescript", "rust"],
    "datos": {"red": "bitcoin", "activos": [{"id": "RWA-EC-01", "valor": 1250.5}]},
    "metadatos": {"origen": "jev"},
}
canonico = json.dumps(cuerpo, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
cuerpo["firma_hmac"] = hmac.new(os.environ["JEV_HMAC_KEY"].encode(), canonico.encode(), hashlib.sha256).hexdigest()

r = requests.post(f"{os.environ['API']}/api/v5/jev-dispatch", json=cuerpo,
                  headers={"Authorization": f"Bearer {os.environ['TOKEN']}"}, timeout=30)
respuesta = r.json()

# Verificación de la respuesta: misma regla, sin el campo firma_respuesta.
firma = respuesta.pop("firma_respuesta")
esperada = hmac.new(os.environ["JEV_HMAC_KEY"].encode(),
                    json.dumps(respuesta, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(),
                    hashlib.sha256).hexdigest()
assert hmac.compare_digest(firma, esperada)
```

**Con curl** (el JSON firmado debe enviarse tal cual se firmó):
```bash
CUERPO='{"datos":{"precios":[1.5,2,3.25]},"id_jev":"jev-1","lenguajes_destino":["go","java"],"tipo_tarea":"ANALISIS_DATOS"}'
FIRMA=$(printf '%s' "$CUERPO" | openssl dgst -sha256 -hmac "$JEV_HMAC_KEY" -r | cut -d' ' -f1)
curl -s -X POST "$API/api/v5/jev-dispatch" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "${CUERPO%\}},\"firma_hmac\":\"$FIRMA\"}"

curl -s "$API/api/v5/jev/agentes" -H "Authorization: Bearer $TOKEN"   # lenguajes y tipos de tarea
```
> En el ejemplo curl, `CUERPO` ya está en forma canónica (claves ordenadas y sin espacios), por eso su
> HMAC coincide con el que calcula UASI.

Detalle del flujo, de la tabla de tipos por lenguaje y de la firma: [`docs/arquitectura.md`](docs/arquitectura.md#7-capa-multiagente-jev-jev--uasi--jev).

### Automatización, optimización y SDK

| Endpoint | Uso |
|---|---|
| `POST /api/v5/jev-dispatch` | Una solicitud firmada → una respuesta firmada (con anti-replay, idempotencia y caché). |
| `POST /api/v5/jev-dispatch/lote` | `{"solicitudes": [...]}`, hasta `JEV_LOTE_MAX` (20), cada una con su firma. |
| `GET /api/v5/jev/agentes` | Lenguajes, tipos de tarea y configuración efectiva. |
| `GET /api/v5/jev/metricas` | Contadores por lenguaje y tarea, rechazos, caché y callbacks. |

- **Anti-replay:** si JEV envía `timestamp`, debe estar dentro de ±300 s (`JEV_VENTANA_SEGUNDOS`).
- **Idempotencia:** si JEV envía `id_solicitud`, un reintento idéntico devuelve la misma respuesta
  (`X-JEV-Idempotente: true`) y el mismo id con otro cuerpo devuelve 409.
- **Caché determinista:** resultados repetidos se sirven con `desde_cache: true` (no aplica a
  `AUDITORIA_LEDGER` ni `EVALUACION_MODELO`).
- **Callback automático:** con `metadatos.url_callback` y el host en `JEV_CALLBACK_HOSTS`, UASI envía
  la respuesta firmada a JEV en segundo plano y lo registra como `CALLBACK_JEV`.

**SDK Python (solo stdlib)** — `clientes/python/jev_cliente.py`:

```python
from jev_cliente import ClienteJEV, construir_solicitud

cliente = ClienteJEV("http://localhost:8000", clave_hmac=os.environ["JEV_HMAC_KEY"], token_jwt=TOKEN)
r = cliente.despachar("SINCRONIZACION_RWA", ["solidity", "typescript"],
                      {"activo_id": "NGF-1", "valor": "1250.50"}, id_solicitud="rwa-0001")
print(r["estado"], r["resultados_por_lenguaje"]["solidity"]["datos_resultado"]["codigo_binding"])
```

**SDK TypeScript (Node ≥ 18)** — `clientes/typescript/jevCliente.ts`:

```ts
import { ClienteJEV } from "./jevCliente";
const cliente = new ClienteJEV("http://localhost:8000", process.env.JEV_HMAC_KEY!, token);
const r = await cliente.despachar("GENERACION_BINDINGS", ["rust", "go"], { activo_id: "NGF-1" });
```

Ambos clientes firman la solicitud y **rechazan cualquier respuesta cuya firma no sea válida**.

## Anclaje en Bitcoin L1

El ledger hash-encadenado demuestra que **nadie ha alterado un bloque sin romper la cadena**, pero quien
controle el servidor podría recalcular la cadena entera. Para impedirlo, UASI v5 publica cada día una
huella de su ledger en **Bitcoin L1**, el registro público con más prueba de trabajo acumulada:

1. Se calcula la **raíz de Merkle** de los bloques del día (hoja = `SHA256(0x00‖hash_bloque)`,
   nodo = `SHA256(0x01‖izq‖der)`).
2. La raíz se envía a los **calendarios públicos de OpenTimestamps** (`OTS_CALENDARS`), que agregan
   miles de compromisos en un árbol y publican su raíz en una transacción **`OP_RETURN`** de Bitcoin.
3. Se guarda una prueba `.ots` en `PATH_WORKSPACE/ots_proofs/` (`<fecha>.ots`, una copia con hora
   y los metadatos en `<fecha>.json`).
4. Pasadas unas horas (cuando hay confirmación en Bitcoin), `/api/v5/verify-ots/{fecha}` actualiza la
   prueba con la atestación del bloque, recalcula la raíz desde el ledger y la contrasta con la cabecera
   del bloque a través de Esplora.

```bash
make anchor-btc                  # = ./scripts/anclar_ledger_bitcoin.sh [YYYY-MM-DD]
make verify-ots FECHA=2026-10-12 # = ./scripts/verificar_ots.sh 2026-10-12 [--local]
```

Estados posibles de la verificación:

- `pendiente_confirmacion_bitcoin`
- `confirmada_en_bitcoin_y_contrastada`
- `confirmada_en_bitcoin (contraste con explorador no disponible)`

Los calendarios públicos solo anclan en **mainnet**. Con `BITCOIN_NETWORK=testnet` hace falta un
calendario propio. Cómo funciona todo con detalle, y cómo verificar un `.ots` años después:
[`docs/bitcoin_opentimestamps.md`](docs/bitcoin_opentimestamps.md).

## Estructura del repositorio

```text
uasi_v5_repo/
├── README.md · DEPLOY_README.md · CHANGELOG.md · LICENSE
├── .gitignore · .env.example · requirements.txt · requirements-dev.txt · Makefile · pyproject.toml
├── src/uasi_v5/
│   ├── __init__.py          # versión y exports perezosos
│   ├── main.py              # fachada: app + reexportaciones (__all__)
│   ├── servicios.py         # singletons: ledger, merkle_manager, orquestador, dispatcher_jev
│   ├── core/                # config, excepciones, registro (log JSON), utilidades
│   ├── esquemas/            # solicitudes v1/v5 · jev.py (SolicitudJEV / RespuestaJEV)
│   ├── seguridad/           # JWT, gobernanza, límites, jev_firma.py (HMAC-SHA256)
│   ├── persistencia/        # almacén de modelos (checksum/HMAC + locks)
│   ├── ml/                  # evaluación + agentes ML (legacy, profundo, evolutivo, ensamblado, streaming, metacognitivo)
│   ├── orquestacion/        # OrquestadorSemanticoUASI
│   ├── agentes_jev/         # base, dispatcher y 7 agentes por lenguaje
│   ├── auditoria/           # ledger hash-encadenado + Merkle/OpenTimestamps
│   └── api/                 # app (lifespan), manejadores y rutas (auth, v1, v5, jev, auditoría, salud)
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # path, alias del módulo histórico y fixtures
│   ├── test_uasi_v5.py      # 18 tests (suite original)
│   ├── test_jev.py          # 12 tests de la capa JEV
│   └── test_jev_automatizacion.py  # 17 tests: anti-replay, caché, lotes, métricas, callbacks, SDK
├── clientes/
│   ├── python/jev_cliente.py       # SDK de referencia JEV (stdlib)
│   └── typescript/jevCliente.ts    # SDK de referencia JEV (Node ≥ 18)
├── scripts/
│   ├── generar_secretos.sh
│   ├── anclar_ledger_bitcoin.sh
│   └── verificar_ots.sh
├── docker/
│   ├── Dockerfile           # multi-stage, usuario no root, tini y healthcheck
│   └── docker-compose.yml   # uasi-api + redis
└── docs/
    ├── arquitectura.md
    ├── api_reference.md
    └── bitcoin_opentimestamps.md
```

## Desarrollo, tests y Docker

```bash
make dev          # instala requirements-dev.txt en .venv
make test         # pytest (el test de OTS con red se omite por defecto)
make test-ots     # UASI_TEST_OTS_RED=1: incluye el anclaje real contra los calendarios
make lint         # ruff + mypy
make format       # black
make docker-build && make docker-up   # API en :8000 con Redis para el rate limiting
make help         # lista todos los targets
```

> **Compatibilidad:** `tests/test_uasi_v5.py` importa `uasi_v5_enterprise_production`. `tests/conftest.py`
> redirige ese nombre a `uasi_v5.main`, así que los tests no necesitan modificarse.
> En producción, el módulo ASGI es `uasi_v5.main:app` (con `PYTHONPATH=src`).

## Documentación adicional

- [`DEPLOY_README.md`](DEPLOY_README.md): guía de despliegue en producción.
- [`docs/arquitectura.md`](docs/arquitectura.md): componentes, flujos y decisiones de diseño.
- [`docs/api_reference.md`](docs/api_reference.md): referencia completa de la API.
- [`docs/bitcoin_opentimestamps.md`](docs/bitcoin_opentimestamps.md): OpenTimestamps, OP_RETURN y verificación a largo plazo.
- [`CHANGELOG.md`](CHANGELOG.md): historial de versiones (de la v1.0 a la v5.2.1-enterprise).

## Licencia

MIT. Ver [`LICENSE`](LICENSE).
