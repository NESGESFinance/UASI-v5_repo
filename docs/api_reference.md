# Referencia de la API · UASI v5 Enterprise (5.2.2-enterprise)

- **URL base:** `http://<host>:8000`. Puerto por defecto: `PORT=8000`.
- **Formato:** JSON (`Content-Type: application/json`), con fechas en ISO 8601 UTC.
- **OpenAPI interactivo:** `GET /docs` (Swagger UI) y `GET /openapi.json`.

> Los ejemplos de respuesta se capturaron ejecutando el servicio real. Los identificadores, hashes y
> marcas de tiempo cambian en cada ejecución.

## Índice

| # | Método | Ruta | Auth | Descripción |
|---|---|---|---|---|
| 1 | GET | [`/api/v5/health`](#1-get-apiv5health) | No | Estado de salud detallado. |
| 2 | GET | [`/api/v5/token`](#2-get-apiv5token) | No (solo en desarrollo) | Emite un JWT de prueba. |
| 3 | POST | [`/api/v1/procesar`](#3-post-apiv1procesar) | Bearer | Entrenamiento de agentes legacy. |
| 4 | POST | [`/api/v5/uasi-master-execute`](#4-post-apiv5uasi-master-execute) | Bearer | Orquestador semántico. |
| 5 | POST | [`/api/v5/streaming-ingest`](#5-post-apiv5streaming-ingest) | Bearer | Aprendizaje online incremental. |
| 6 | GET | [`/api/v5/cryptographic-ledger`](#6-get-apiv5cryptographic-ledger) | Bearer | Ledger paginado con verificación opcional. |
| 7 | GET | [`/api/v5/merkle-anchor`](#7-get-apiv5merkle-anchor) | Bearer | Raíz de Merkle diaria y anclaje en Bitcoin vía OTS. |
| 8 | GET | [`/api/v5/verify-ots/{fecha}`](#8-get-apiv5verify-otsfecha) | Bearer | Verificación de la prueba `.ots`. |
| 9 | POST | [`/api/v5/jev-dispatch`](#9-post-apiv5jev-dispatch) | Bearer + HMAC JEV | Despacho multiagente JEV. |
| 10 | POST | [`/api/v5/jev-dispatch/lote`](#10-post-apiv5jev-dispatchlote) | Bearer + HMAC JEV | Lote de solicitudes JEV. |
| 11 | GET | [`/api/v5/jev/agentes`](#11-get-apiv5jevagentes) | Bearer | Lenguajes, tareas y configuración JEV. |
| 12 | GET | [`/api/v5/jev/metricas`](#12-get-apiv5jevmetricas) | Bearer | Métricas de la capa JEV. |

---

## Autenticación

Los endpoints protegidos requieren esta cabecera:

```
Authorization: Bearer <JWT>
```

- Algoritmo `JWT_ALGORITHM` (HS256 por defecto), firmado con `SECRET_JWT_KEY`.
- Claims obligatorios: `iss` (= `JWT_ISSUER`, por defecto `uasi-v5`), `sub`, `iat` y `exp`. También se emiten `scope` y `jti`.
- El `sub` se registra como `actor` en cada bloque del ledger.

Preparación común para los ejemplos:

```bash
export API=http://localhost:8000
export TOKEN=$(curl -s "$API/api/v5/token?sujeto=auditor" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

## Códigos de error comunes

Todos los errores devuelven `{"detail": ...}`. En el caso de 422 de validación, `detail` es una lista
de objetos `{loc, msg, type}` y nunca incluye el valor recibido.

| Código | Causa | Ejemplo de `detail` |
|---|---|---|
| **401** | Falta la cabecera o no usa el esquema Bearer | `"Token Bearer requerido."` |
| **401** | Firma, emisor o formato no válidos | `"Token inválido."` |
| **401** | Token expirado | `"Token expirado."` |
| **403** | Intención fuera de la allow-list, patrón denegado o metadatos no válidos | `"Intención 'borrar todo' no permitida. Permitidas: [...]"` |
| **404** | Recurso inexistente (no hay bloques ni prueba OTS de esa fecha, o `/token` en producción) | `"No existe prueba OTS para 2020-01-01."` |
| **409** | Streaming: clases nuevas en un modelo existente | `"Clases nuevas [2] no presentes en el modelo online (clases=[0, 1]). ..."` |
| **413** | Payload mayor que `MAX_PAYLOAD_BYTES` (10 MB por defecto) | `"Payload excede el límite de 10485760 bytes."` |
| **422** | Error de esquema Pydantic (campos extra, tipos, longitudes, binariedad o coherencia) | `[{"loc":["body"],"msg":"Value error, targets debe contener ambas clases (0 y 1) para entrenar.","type":"value_error"}]` |
| **422** | Error de datos del dominio (dimensionalidad o fecha mal formada) | `"Dimensionalidad incompatible: el modelo online espera 2 características y el lote tiene 3."` |
| **429** | Más de `RATE_LIMIT_PER_MINUTE` peticiones por minuto desde la misma IP | `"Rate limit exceeded: 30 per 1 minute"` |
| **500** | Checksum o HMAC del modelo persistido no válido (`IntegridadModeloError`) | `"Integridad del artefacto '<modelo>.joblib' comprometida: <motivo>."` |
| **500** | Error no controlado (los detalles van solo al log) | `"Error interno. Consulte los logs con el id de correlación."` |
| **503** | El servidor no tiene `SECRET_JWT_KEY` (falla cerrado) | `"Autenticación no configurada en el servidor (SECRET_JWT_KEY ausente)."` |
| **503** | `/api/v5/health` en estado crítico | *(cuerpo completo del health check)* |

### Campos comunes de las peticiones POST

Todos los modelos usan `extra="forbid"`, así que cualquier campo desconocido devuelve 422.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `id_solicitud` | string | No | Identificador de correlación. Por defecto, un UUID4. |
| `timestamp` | datetime ISO 8601 | No | Por defecto, el momento actual en UTC. |
| `metadatos_contexto` | object | No | Como máximo 50 claves que cumplan `^[A-Za-z0-9_.\-]{1,64}$` y con tamaño limitado. Se registra en el ledger y puede fijar la semilla. |

`inputs` debe ser una matriz numérica rectangular: hasta `UASI_MAX_MUESTRAS` filas (50 000) y
`UASI_MAX_FEATURES` columnas (512). Admite `null`, `NaN` e `Infinity`, que el guardián imputa y
documenta en `sanitizacion`.

---

## 1. GET `/api/v5/health`

Estado de salud detallado: workspace, checksum de cada modelo, integridad del ledger, autenticación y
OpenTimestamps. **No requiere autenticación.**

- **Headers:** ninguno.
- **Body:** ninguno.
- **Códigos:** `200` (estado `ok` o `degradado`) · `503` (estado `critico`) · `429`.

```bash
curl -s $API/api/v5/health
```

**Respuesta 200**
```json
{
  "estado": "ok",
  "version": "5.1.0-enterprise",
  "entorno": "development",
  "timestamp": "2026-10-12T02:52:30.468381+00:00",
  "componentes": {
    "workspace": {"ruta": "/data", "escribible": true},
    "modelos": {
      "red_neuronal_legacy": {"estado": "no_entrenado"},
      "arbol_legacy": {"estado": "ok", "checksum": "ok", "tamano_bytes": 879,
                       "modificado_utc": "2026-10-12T02:52:39.060072+00:00"},
      "svm_legacy": {"estado": "no_entrenado"},
      "streaming_sgd": {"estado": "no_entrenado"},
      "cerebral_profundo": {"estado": "no_entrenado"},
      "vectorial_evolutivo": {"estado": "no_entrenado"},
      "ensamblado_gb": {"estado": "no_entrenado"}
    },
    "ledger": {"integro": true, "bloques": 1, "ultimo_hash": "a2eaab55bdeb3190…8ee4"},
    "autenticacion": {"configurada": true},
    "opentimestamps": {
      "disponible": true,
      "calendarios": ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
                      "https://a.pool.eternitywall.com"],
      "red_bitcoin": "mainnet",
      "error_import": null
    }
  }
}
```

Un modelo con checksum alterado aparece como `"estado": "integridad_comprometida"`.

---

## 2. GET `/api/v5/token`

Emite un **JWT de prueba**. Solo existe si `SECRET_JWT_KEY` está definida y `UASI_ENV != production`;
en cualquier otro caso devuelve 404.

- **Headers:** ninguno.
- **Query:**

| Parámetro | Tipo | Defecto | Restricción |
|---|---|---|---|
| `sujeto` | string | `desarrollador` | `^[A-Za-z0-9_.@\-]{1,64}$` |
| `minutos` | int | `JWT_EXP_MINUTES` (60) | 1–1440 |

- **Códigos:** `200` · `404` (en producción o sin clave) · `422` (parámetros no válidos) · `429`.

```bash
curl -s "$API/api/v5/token?sujeto=auditor&minutos=60"
```

**Respuesta 200**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.…",
  "token_type": "bearer",
  "expira_en_minutos": 60,
  "advertencia": "Solo para desarrollo."
}
```

---

## 3. POST `/api/v1/procesar`

Entrena un agente legacy de **clasificación binaria** y registra el evento `PROCESAMIENTO_V1` en el ledger.

- **Headers:** `Authorization: Bearer <JWT>` y `Content-Type: application/json`.
- **Body:**

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `tipo_agente` | `"red_neuronal"` \| `"svm"` \| `"arbol_decision"` | Sí | Agente que se va a entrenar. |
| `intencion` | string (≤ 64) | No (`clasificacion`) | Allow-list: `clasificacion`, `clasificacion_binaria`, `prediccion`, `entrenamiento` y `analisis`. |
| `inputs` | number[][] | Sí | Matriz de características. |
| `targets` | int[] | Sí | Valores 0/1, con **ambas clases** y `len == len(inputs)`. |
| + campos comunes | | | `id_solicitud`, `timestamp`, `metadatos_contexto` |

```bash
curl -s -X POST $API/api/v1/procesar \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tipo_agente":"arbol_decision","intencion":"clasificacion_binaria",
       "inputs":[[0,0],[0,1],[1,0],[1,1],[0.1,0.2],[0.9,0.8],[0.2,0.1],[0.8,0.9]],
       "targets":[0,0,1,1,0,1,0,1],"metadatos_contexto":{"origen":"curl"}}'
```

**Respuesta 200**
```json
{
  "id_ejecucion": "29c4ac3a-5d0e-4e5f-9525-b0c5120d9ce7",
  "id_solicitud": "a5209312-e12c-4b71-b50d-a47a810b5553",
  "timestamp": "2026-10-12T02:52:39.067374+00:00",
  "sanitizacion": {"valores_nan_imputados": 0, "valores_inf_recortados": 0,
                   "estrategia": "media_columna / max-min finito"},
  "agente": "arbol_legacy",
  "version_modelo": 1,
  "metricas": {
    "accuracy": 1.0, "precision": 1.0, "recall": 1.0, "f1_score": 1.0,
    "promedio": "binary", "n_evaluadas": 8, "auc_roc": 1.0,
    "conjunto_evaluacion": "entrenamiento_optimista (datos insuficientes para hold-out)"
  },
  "bloque_ledger": 1,
  "hash_bloque": "26952d41135a285749d0a7b4cbdfb079dcabb227ecd2f5cfaefdf54155597e0e"
}
```

Con suficientes muestras, `conjunto_evaluacion` indica el **hold-out estratificado**.

- **Errores:** `401`, `403` (intención no permitida o patrón denegado), `413`, `422` (targets no binarios
  o con una sola clase, longitudes distintas, matriz no rectangular, campos extra o dimensionalidad
  incompatible con el modelo persistido), `429`, `500` (integridad del modelo) y `503`.

```bash
# Ejemplo de 403
curl -s -X POST $API/api/v1/procesar -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tipo_agente":"svm","intencion":"borrar todo","inputs":[[0],[1]],"targets":[0,1]}'
# → 403 {"detail":"Intención 'borrar todo' no permitida. Permitidas: ['analisis', 'clasificacion', ...]."}
```

---

## 4. POST `/api/v5/uasi-master-execute`

El **orquestador semántico** clasifica el prompt en categorías, elige un plan por prioridad y lo ejecuta.
Registra el evento `EJECUCION_MAESTRA_V5`.

- **Headers:** `Authorization: Bearer <JWT>` y `Content-Type: application/json`.
- **Body:**

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `prompt_intencion` | string (3–2000) | Sí | Instrucción en lenguaje natural. |
| `inputs` | number[][] | No* | Datos de entrenamiento. |
| `targets` | int[] (≥ 0) | No* | Etiquetas; se admite multiclase. |
| `clases_declaradas` | int[] | No | Universo de clases (ruta de streaming). |
| + campos comunes | | | |

\* `inputs` y `targets` deben enviarse **juntos** o no enviarse. Los planes de entrenamiento los necesitan.

**Categorías y planes** (por orden de prioridad):

| Plan | Palabras clave (tras normalizar el texto) | Ruta ejecutada |
|---|---|---|
| `DIAGNOSTICO` | diagnostic, salud, health, estado del sistema… | *Health check* detallado |
| `AUDITORIA` | auditori, ledger, merkle, trazabilidad | Integridad del ledger + raíz de Merkle del día |
| `APRENDIZAJE_ONLINE_CONTINUO` | streaming, online, continuo, incremental, tiempo real | `streaming` |
| `EXPLICABILIDAD` | explica, interpret, importancia, transparen | `ensamblado_explicable` |
| `OPTIMIZACION_EVOLUTIVA` | optimiz, evolut, hiperparam, genetic | `vectorial_evolutivo` |
| `APRENDIZAJE_PROFUNDO` | profund, deep, neuronal | `cerebral_profundo` |
| `ANALISIS_METACOGNITIVO_AUTO` | clasific, predic, entren, analiz, aprend, modela | La decide la entropía de Shannon |

```bash
curl -s -X POST $API/api/v5/uasi-master-execute \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt_intencion":"Explica que variables importan",
       "inputs":[[0,0],[0,1],[1,0],[1,1],[0.1,0.2],[0.9,0.8],[0.2,0.1],[0.8,0.9]],
       "targets":[0,0,1,1,0,1,0,1]}'
```

**Respuesta 200 (plan de entrenamiento)**
```json
{
  "id_ejecucion": "141b7a49-fb89-4eae-b19e-308d908f866e",
  "id_solicitud": "fdc851b6-bab0-4a8c-959e-da758c7a744b",
  "timestamp": "2026-10-12T02:55:35.104329+00:00",
  "plan": "EXPLICABILIDAD",
  "categorias_detectadas": ["EXPLICABILIDAD"],
  "sanitizacion": {"valores_nan_imputados": 0, "valores_inf_recortados": 0,
                   "estrategia": "media_columna / max-min finito"},
  "hash_datos": "2898ee0f07acfa0ec7659604249136795eb961c6ca4a6081a80eb7cbbf8ef547",
  "perfil_metacognitivo": {
    "n_muestras": 8, "n_caracteristicas": 2, "clases": [0, 1],
    "distribucion_clases": {"0": 4, "1": 4},
    "entropia_shannon_bits": 1.0, "entropia_clases_normalizada": 1.0, "ratio_desbalance": 1.0,
    "entropia_media_caracteristicas_normalizada": 0.648912, "binario": true
  },
  "ruta_ejecutada": "ensamblado_explicable",
  "razonamiento": "Ruta impuesta por el plan semántico EXPLICABILIDAD.",
  "resultado": {
    "agente": "ensamblado_explicable_gb",
    "version_modelo": 1,
    "metricas": {"accuracy": 1.0, "precision": 1.0, "recall": 1.0, "f1_score": 1.0,
                 "promedio": "binary", "n_evaluadas": 8, "auc_roc": 1.0,
                 "conjunto_evaluacion": "entrenamiento_optimista (datos insuficientes para hold-out)"},
    "explicabilidad": {
      "metodo": "feature_importances_ (reducción de impureza)",
      "importancias_top": [{"caracteristica": "x0", "importancia": 1.0},
                           {"caracteristica": "x1", "importancia": 0.0}]
    }
  },
  "bloque_ledger": 1,
  "hash_bloque": "2792c8e883b7ed8a826924b65a8782d40db494f95651d1d800eccbf4a91de2c1"
}
```

**Respuesta 200 (plan `DIAGNOSTICO`, por ejemplo `"prompt_intencion":"Diagnostica el estado del sistema"`)**
```json
{
  "id_ejecucion": "…", "id_solicitud": "…", "timestamp": "…",
  "plan": "DIAGNOSTICO",
  "categorias_detectadas": ["DIAGNOSTICO"],
  "ruta_ejecutada": "diagnostico",
  "resultado": { "estado": "ok", "version": "5.1.0-enterprise", "componentes": { "…": "…" } },
  "bloque_ledger": 2,
  "hash_bloque": "…"
}
```

- **Errores:** `401`, `403` (el prompt no corresponde a ninguna categoría permitida o contiene un patrón
  denegado, como `ignora las instrucciones`, `rm -rf`, `drop table` o `<script`), `413`, `422`
  (inputs sin targets, longitudes distintas o datos insuficientes para el plan), `429`, `500` y `503`.

```bash
curl -s -X POST $API/api/v5/uasi-master-execute -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"prompt_intencion":"hola mundo"}'
# → 403 {"detail":"El prompt no corresponde a ninguna intención permitida ([...])."}
```

---

## 5. POST `/api/v5/streaming-ingest`

Aprendizaje **online incremental** (`SGDClassifier.partial_fit`), con clases dinámicas y multiclase.
Registra el evento `INGESTA_STREAMING`.

- **Headers:** `Authorization: Bearer <JWT>` y `Content-Type: application/json`.
- **Body:**

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `intencion` | string (≤ 64) | No (`aprendizaje_online`) | Allow-list: `aprendizaje_online`, `aprendizaje_online_continuo`, `streaming` y `entrenamiento_incremental`. |
| `inputs` | number[][] | Sí | Lote de características. |
| `targets` | int[] (≥ 0, no vacío) | Sí | Etiquetas del lote. |
| `clases_declaradas` | int[] (≥ 0) | No | Universo de clases conocido de antemano. |
| `reiniciar_modelo` | bool | No (`false`) | Descarta el modelo actual y crea uno nuevo. |
| + campos comunes | | | |

```bash
curl -s -X POST $API/api/v5/streaming-ingest \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"intencion":"aprendizaje_online","inputs":[[0,0],[1,1],[0,1],[1,0]],"targets":[0,1,0,1]}'
```

**Respuesta 200**
```json
{
  "id_ejecucion": "c8cd9a32-d6db-4e93-9df0-672fe5bb5d82",
  "id_solicitud": "cd7e5720-23a7-4187-944b-3b3aeed838c9",
  "timestamp": "2026-10-12T02:52:39.094472+00:00",
  "sanitizacion": {"valores_nan_imputados": 0, "valores_inf_recortados": 0,
                   "estrategia": "media_columna / max-min finito"},
  "agente": "streaming_sgd",
  "version_modelo": 1,
  "clases_modelo": [0, 1],
  "muestras_vistas_total": 4,
  "lotes_procesados": 1,
  "metricas": {
    "accuracy_muestra_actual": 1.0,
    "accuracy_muestra_actual_nota": "Medida sobre el mismo lote tras aprender de él (optimista).",
    "prequential_antes_de_actualizar": null
  },
  "bloque_ledger": 3,
  "hash_bloque": "9b14260f4a45b19cd11aa7013bb544a49617ef647838ad11682a1c7062f85b02"
}
```

En los lotes siguientes, `prequential_antes_de_actualizar` contiene la exactitud medida **antes** de
aprender del lote, que es la métrica honesta.

- **Errores:**
  - `401`, `403` (intención no permitida), `413`, `429`, `500` y `503`.
  - `409`: clases nuevas en un modelo existente. Se resuelve con `reiniciar_modelo=true` + `clases_declaradas`.
  - `422`: dimensionalidad distinta de la del modelo, targets negativos o vacíos, o longitudes distintas.

```bash
# 409 tras haber entrenado con clases {0,1}
curl -s -X POST $API/api/v5/streaming-ingest -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"intencion":"streaming","inputs":[[0,0],[1,1]],"targets":[0,2]}'
# → 409 {"detail":"Clases nuevas [2] no presentes en el modelo online (clases=[0, 1]). Envíe reiniciar_modelo=true con clases_declaradas para reconstruirlo."}
```

---

## 6. GET `/api/v5/cryptographic-ledger`

Ledger de auditoría paginado. Si se indica `verificar=true`, recorre la cadena completa (coste O(n)).

- **Headers:** `Authorization: Bearer <JWT>`.
- **Query:**

| Parámetro | Tipo | Defecto | Restricción |
|---|---|---|---|
| `page` | int | 1 | ≥ 1 |
| `page_size` | int | 50 | 1–200 |
| `verificar` | bool | `false` | |

```bash
curl -s "$API/api/v5/cryptographic-ledger?page=1&page_size=2&verificar=true" -H "Authorization: Bearer $TOKEN"
```

**Respuesta 200**
```json
{
  "pagina": 1,
  "tamano_pagina": 2,
  "total_bloques": 4,
  "total_paginas": 2,
  "bloques": [
    {
      "indice": 0, "evento": "GENESIS", "actor": "sistema", "fecha": "2026-10-12",
      "timestamp": "2026-10-12T02:52:23.844041+00:00",
      "datos": {"version": "5.1.0-enterprise"},
      "hash_datos": "76c8708785e42e5879fe223450bcac086923dcc2024be5ea4e35d1ac62186ebf",
      "hash_previo": "0000000000000000000000000000000000000000000000000000000000000000",
      "hash_bloque": "a2eaab55bdeb3190537e5425a968a481e3bcd522b6064fc1212b66d22f5a8ee4"
    },
    {
      "indice": 1, "evento": "PROCESAMIENTO_V1", "actor": "auditor", "fecha": "2026-10-12",
      "timestamp": "2026-10-12T02:52:39.065290+00:00",
      "datos": {"agente": "arbol_legacy", "intencion": "clasificacion_binaria", "n_muestras": 8,
                "n_caracteristicas": 2, "hash_datos": "2898ee0f…f547", "metricas": {"…": "…"},
                "metadatos_contexto": {"origen": "curl"}, "sanitizacion": {"…": "…"},
                "id_solicitud": "a5209312-e12c-4b71-b50d-a47a810b5553"},
      "hash_datos": "4a43f82ff39ee5ca62de4ffb9be79813c31087fb0d978390be3806b5da13bbbd",
      "hash_previo": "a2eaab55bdeb3190537e5425a968a481e3bcd522b6064fc1212b66d22f5a8ee4",
      "hash_bloque": "26952d41135a285749d0a7b4cbdfb079dcabb227ecd2f5cfaefdf54155597e0e"
    }
  ],
  "integridad": {"integro": true, "bloques": 4, "ultimo_hash": "9b14260f…5b02"}
}
```

Si se detecta una manipulación, `integridad` devuelve `{"integro": false, "bloques": n, "error":
"Encadenamiento roto en bloque k."}` (o un error de índice discontinuo, de `hash_datos` o de `hash_bloque`).

- **Errores:** `401`, `422` (parámetros fuera de rango), `429` y `503`.

---

## 7. GET `/api/v5/merkle-anchor`

Calcula la **raíz de Merkle** de los bloques de una fecha UTC y la sella en **Bitcoin L1** a través de
los calendarios de OpenTimestamps. Guarda `<fecha>.ots`, `<fecha>_<sello>.ots` y `<fecha>.json` en
`PATH_WORKSPACE/ots_proofs/` y registra el evento `ANCLAJE_MERKLE_OTS`.

- **Headers:** `Authorization: Bearer <JWT>`.
- **Query:** `fecha` (opcional, `YYYY-MM-DD`; por defecto, el día UTC actual).

```bash
curl -s "$API/api/v5/merkle-anchor?fecha=2026-10-12" -H "Authorization: Bearer $TOKEN"
```

**Respuesta 200**
```json
{
  "fecha": "2026-10-12",
  "raiz_merkle": "0e70959e142f7bb57a7f0d9cb7b9e1d713cbfc9579a6df352e9ac5d6550f87b4",
  "n_bloques": 4,
  "anclaje": {
    "estado": "pendiente_confirmacion_bitcoin",
    "fecha": "2026-10-12",
    "raiz_merkle": "0e70959e142f7bb57a7f0d9cb7b9e1d713cbfc9579a6df352e9ac5d6550f87b4",
    "n_bloques": 4,
    "sellado_en": "2026-10-12T02:52:44.168215+00:00",
    "calendarios": ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
                    "https://a.pool.eternitywall.com"],
    "red_bitcoin_configurada": "mainnet",
    "version": "5.1.0-enterprise",
    "archivo_ots": "2026-10-12.ots",
    "errores_calendarios": [],
    "nota": "Los calendarios publican la transacción OP_RETURN en Bitcoin en pocas horas; consulte /api/v5/verify-ots/{fecha} para obtener la atestación de bloque."
  }
}
```

Otros valores de `anclaje.estado`:

- `error_anclaje`: menos de `OTS_MIN_CALENDARS` calendarios aceptaron la raíz; se devuelven `calendarios_aceptados`, `errores` y `detalle`.
- `opentimestamps_no_disponible`: la librería no está instalada.

- **Errores:** `401`, `404` (no hay bloques ese día), `422` (`fecha` no cumple `^\d{4}-\d{2}-\d{2}$`), `429` y `503`.

---

## 8. GET `/api/v5/verify-ots/{fecha}`

Verifica la prueba `.ots` de una fecha en cuatro pasos:

1. Recalcula la raíz desde el ledger con los `n_bloques` sellados.
2. Hace *upgrade* contra los calendarios para obtener la atestación del bloque Bitcoin.
3. Contrasta la raíz de Merkle de la cabecera del bloque con Esplora.
4. Guarda la prueba actualizada.

- **Headers:** `Authorization: Bearer <JWT>`.
- **Path:** `fecha` (`YYYY-MM-DD`).

```bash
curl -s "$API/api/v5/verify-ots/2026-10-12" -H "Authorization: Bearer $TOKEN"
```

**Respuesta 200 (todavía pendiente)**
```json
{
  "estado": "pendiente_confirmacion_bitcoin",
  "archivo_ots": "2026-10-12.ots",
  "metadatos_anclaje": {"fecha": "2026-10-12", "raiz_merkle": "0e70959e…87b4", "n_bloques": 4,
                        "sellado_en": "2026-10-12T02:52:44.168215+00:00", "calendarios": ["…"],
                        "red_bitcoin_configurada": "mainnet", "version": "5.1.0-enterprise"},
  "coherencia_ledger": {
    "raiz_en_prueba": "0e70959e142f7bb57a7f0d9cb7b9e1d713cbfc9579a6df352e9ac5d6550f87b4",
    "raiz_recalculada_desde_ledger": "0e70959e142f7bb57a7f0d9cb7b9e1d713cbfc9579a6df352e9ac5d6550f87b4",
    "n_bloques": 4,
    "coincide": true
  },
  "prueba_actualizada": false,
  "atestaciones": [
    {"tipo": "pendiente", "calendario": "https://alice.btc.calendar.opentimestamps.org"},
    {"tipo": "pendiente", "calendario": "https://bob.btc.calendar.opentimestamps.org"},
    {"tipo": "pendiente", "calendario": "https://finney.calendar.eternitywall.com"}
  ],
  "incidencias_calendarios": [
    {"calendario": "https://alice.btc.calendar.opentimestamps.org", "estado": "aun_pendiente"}
  ],
  "red_bitcoin_configurada": "mainnet"
}
```

Cuando hay confirmación, `estado` pasa a `confirmada_en_bitcoin_y_contrastada` y `atestaciones`
incluye `{"tipo": "bitcoin_block_header", "altura_bloque": N, "merkle_root_bloque": "…",
"contraste_explorador": {"contrastado": true, "explorador": "…", "hash_bloque": "…",
"coincide_merkle_root": true, "fecha_bloque_utc": "…"}}`. Si Esplora no responde, el estado es
`confirmada_en_bitcoin (contraste con explorador no disponible)`. Si `coherencia_ledger.coincide` es
`false`, el ledger se modificó después del sellado.

- **Errores:** `401`, `404` (no hay prueba para esa fecha), `422` (formato de fecha no válido), `429` y `503`.
  Si OTS no está instalado, devuelve 200 con `estado: opentimestamps_no_disponible`.

> **Limitación conocida (5.1.0):** una fecha con el formato correcto pero imposible en el calendario
> (por ejemplo, `2020-13-45`) devuelve **500**, cuando lo esperable sería 422, porque `datetime.strptime`
> lanza un `ValueError` genérico. No afecta a la seguridad: no se accede a ninguna ruta. Está pendiente
> de corregir en `main.py`.

---

## 9. POST `/api/v5/jev-dispatch`

Cuerpo `SolicitudJEV` firmado por JEV (`firma_hmac` = HMAC-SHA256 del JSON canónico sin ese campo):

```json
{"id_jev": "jev-001", "version_jev": "1.0", "tipo_tarea": "GENERACION_BINDINGS",
 "lenguajes_destino": ["typescript", "solidity"], "datos": {"activo_id": "NGF-1"},
 "metadatos": {"origen": "jev", "url_callback": "https://jev.ejemplo.com/uasi"},
 "id_solicitud": "rwa-0001", "timestamp": "2026-10-16T12:00:00+00:00", "firma_hmac": "<64 hex>"}
```

- `tipo_tarea`: `INFERENCIA_MULTILENGUAJE`, `ANALISIS_DATOS`, `EVALUACION_MODELO`, `AUDITORIA_LEDGER`,
  `SINCRONIZACION_RWA`, `VALIDACION_CRUZADA`, `GENERACION_BINDINGS`.
- `lenguajes_destino`: `python`, `typescript`, `javascript`, `solidity`, `rust`, `go`, `java`.
- `id_solicitud` y `timestamp` son opcionales; si se envían activan idempotencia y anti-replay.
- **Respuesta 200** `RespuestaJEV`: `id_jev`, `id_ejecucion`, `estado` (`exito`/`error_parcial`/`error`),
  `resultados_por_lenguaje.{lang}` (`estado`, `datos_resultado` con `inferencia_tipos`, `codigo_binding`,
  `resumen`; `hash_resultado`; `desde_cache`), `bloque_ledger`, `errores_globales` y `firma_respuesta`.
- **Cabeceras:** `X-JEV-Idempotente: true|false`; `X-JEV-Callback: programado | rechazado: motivo`.
- **Errores:** `401` (JWT, firma o ventana temporal), `403` (gobernanza de metadatos), `409` (`id_solicitud`
  reutilizado con otro cuerpo), `422` (validación), `503` (`JEV_HMAC_KEY` ausente).

## 10. POST `/api/v5/jev-dispatch/lote`

Cuerpo `{"solicitudes": [SolicitudJEV, ...]}` (1 a `JEV_LOTE_MAX`, por defecto 20; cada una firmada).
Respuesta 200: `total`, `exitosas`, `fallidas`, `resultados[]` (`indice`, `id_jev`, `codigo_http`,
`respuesta` firmada o `error`, `idempotente`, `callback`) y `firma_respuesta` sobre todo el resumen.
Errores globales: `422` (lote vacío o mayor que `JEV_LOTE_MAX`) y `503` (sin `JEV_HMAC_KEY`).

## 11. GET `/api/v5/jev/agentes`

Devuelve `lenguajes`, `tipos_tarea`, `tareas_no_cacheables`, `max_workers`, `jev_configurado`,
`ventana_segundos`, `lote_max` y `callbacks_habilitados`.

## 12. GET `/api/v5/jev/metricas`

Devuelve `por_lenguaje` (`ejecuciones`, `exitos`, `errores`, `aciertos_cache`, `duracion_media_ms`),
`por_tipo_tarea`, `rechazos` (`firma_invalida`, `sin_configurar`, `fuera_de_ventana`, `replay_conflicto`,
`validacion`, `gobernanza`), `respuestas_idempotentes`, `callbacks` y `cache` (incluye `backend`: `redis` o `memoria`). Con `RATE_LIMIT_STORAGE_URI=redis://...`
las métricas se agregan entre todos los workers/réplicas; con `memory://` son por proceso.
