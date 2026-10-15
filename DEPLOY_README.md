# UASI v5 Enterprise Production: guía de despliegue

Versión `5.1.0-enterprise` · Archivos: `uasi_v5_enterprise_production.py`, `requirements_uasi_v5.txt`, `test_uasi_v5.py`

## 1. Instalación

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements_uasi_v5.txt
pytest -q test_uasi_v5.py                        # 17 tests sin red
UASI_TEST_OTS_RED=1 pytest -q test_uasi_v5.py    # + anclaje OpenTimestamps real
```

## 2. Variables de entorno

| Variable | Obligatoria | Defecto | Descripción |
|---|---|---|---|
| `SECRET_JWT_KEY` | **Sí** | — | Clave HMAC para firmar y validar JWT. Si falta, los endpoints protegidos responden **503** (el sistema no queda abierto). Mínimo 32 caracteres. |
| `UASI_ENV` | No | `development` | Con `production` se desactiva `/api/v5/token`. |
| `PATH_WORKSPACE` | No | `./uasi_workspace` | Directorio raíz (`modelos/`, `ledger/`, `ots_proofs/`). Debe ser un volumen persistente compartido por todos los workers. |
| `JWT_ALGORITHM` / `JWT_ISSUER` / `JWT_EXP_MINUTES` | No | `HS256` / `uasi-v5` / `60` | Parámetros del token. |
| `RATE_LIMIT_PER_MINUTE` | No | `30` | Límite de peticiones por IP y minuto (slowapi). |
| `RATE_LIMIT_STORAGE_URI` | No | `memory://` | Con varios workers, usar `redis://host:6379` para que el límite sea global. |
| `MAX_PAYLOAD_BYTES` | No | `10485760` | Tamaño máximo del cuerpo de la petición (10 MB). Si se supera, responde 413. |
| `UASI_MAX_MUESTRAS` / `UASI_MAX_FEATURES` | No | `50000` / `512` | Límites dimensionales. |
| `UASI_MODEL_HMAC_KEY` | Recomendada | — | Si se define, los checksums de los modelos son HMAC-SHA256 y no un simple SHA-256. Así, un atacante con acceso de escritura al disco no puede recalcularlos. |
| `BITCOIN_NETWORK` | No | `mainnet` | `mainnet` o `testnet`. Determina el explorador que se usa para contrastar los bloques. |
| `OTS_CALENDARS` | No | pools públicos de OpenTimestamps y Eternitywall | Lista de calendarios separados por comas. |
| `OTS_MIN_CALENDARS` / `OTS_TIMEOUT_SECONDS` | No | `1` / `10` | Número mínimo de calendarios que deben aceptar el anclaje y timeout de red. |
| `ESPLORA_API_URL` | No | Blockstream (según la red) | API Esplora para contrastar la merkle root del bloque. |
| `UASI_UMBRAL_ENTROPIA_DESBALANCE` / `UASI_UMBRAL_ENTROPIA_FEATURES` | No | `0.85` / `0.90` | Umbrales del enrutamiento metacognitivo. |
| `LOG_LEVEL` | No | `INFO` | Los logs se emiten en formato JSON por stdout. |

> **Testnet:** los calendarios públicos de OpenTimestamps anclan **solo en Bitcoin mainnet**. Si se usa `BITCOIN_NETWORK=testnet`, hay que desplegar un calendario propio (`otsd`) en testnet y apuntar `OTS_CALENDARS` a él.

## 3. Generar el secreto JWT

```bash
export SECRET_JWT_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export UASI_MODEL_HMAC_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Guarde ambos valores en un gestor de secretos (Vault, AWS Secrets Manager…). No los incluya en el repositorio.

## 4. Arranque

```bash
# Producción: gunicorn + workers uvicorn
gunicorn uasi_v5_enterprise_production:app \
  -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 --timeout 300 --graceful-timeout 30

# Desarrollo
python uasi_v5_enterprise_production.py          # uvicorn en :8000
```

Hay que usar `--timeout 300` porque el entrenamiento puede durar varios segundos. Se ejecuta en el threadpool y no bloquea el event loop, pero sí ocupa el worker. Los locks de archivo (`filelock`) garantizan la consistencia entre workers siempre que todos compartan el mismo `PATH_WORKSPACE`.

## 5. Obtener un token de prueba (solo en desarrollo)

```bash
TOKEN=$(curl -s "http://localhost:8000/api/v5/token?sujeto=ana&minutos=60" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/cryptographic-ledger?page=1&page_size=20"
```

En producción (`UASI_ENV=production`), `/api/v5/token` devuelve 404. Los tokens debe emitirlos su IdP con los claims `iss=JWT_ISSUER`, `sub`, `iat` y `exp`, firmados con `SECRET_JWT_KEY`.

## 6. Endpoints

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/api/v1/procesar` | ✔ | Agentes clásicos (`red_neuronal`, `svm`, `arbol_decision`) con targets binarios. |
| POST | `/api/v5/uasi-master-execute` | ✔ | El orquestador semántico elige la ruta: diagnóstico, auditoría, streaming, explicabilidad, evolutivo, profundo o automática. |
| POST | `/api/v5/streaming-ingest` | ✔ | Aprendizaje online multiclase con clases dinámicas. |
| GET | `/api/v5/cryptographic-ledger?page=&page_size=&verificar=` | ✔ | Ledger paginado. Con `verificar=true` comprueba además la integridad de la cadena. |
| GET | `/api/v5/merkle-anchor[?fecha=YYYY-MM-DD]` | ✔ | Calcula la raíz de Merkle del día y la ancla en Bitcoin. |
| GET | `/api/v5/verify-ots/{fecha}` | ✔ | Verifica y actualiza la prueba `.ots`. |
| GET | `/api/v5/health` | ✘ | Health check real: workspace, checksums de modelos, integridad del ledger y disponibilidad de OTS. |
| GET | `/api/v5/token` | ✘ | Token de prueba (solo en desarrollo). |

Ejemplo de petición al endpoint maestro:

```bash
curl -X POST http://localhost:8000/api/v5/uasi-master-execute \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt_intencion":"clasificar operaciones","inputs":[[0.1,1.2],[0.3,0.8],[2.1,0.1],[1.9,0.3]],"targets":[0,0,1,1],"metadatos_contexto":{"semilla":7}}'
```

## 7. Anclar el ledger en Bitcoin (OpenTimestamps)

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/merkle-anchor"
```

El proceso funciona así:
1. Se calcula la raíz de Merkle de los bloques del día (hoja = `SHA256(0x00‖hash_bloque)`, nodo = `SHA256(0x01‖izq‖der)`).
2. La raíz se envía, con un nonce, a los calendarios OTS. Estos agregan los compromisos y los publican en una transacción Bitcoin con `OP_RETURN`.
3. Se guardan los archivos siguientes:
   - `PATH_WORKSPACE/ots_proofs/<fecha>.ots`: la prueba del último anclaje del día.
   - `<fecha>_<hora>.ots`: un histórico inmutable de cada anclaje.
   - `<fecha>.json`: raíz, número de bloques y calendarios usados.

La confirmación en Bitcoin suele tardar unas horas. Se recomienda programar un anclaje diario, por ejemplo a las 23:55 UTC:

```cron
55 23 * * * curl -s -H "Authorization: Bearer $TOKEN_SERVICIO" http://localhost:8000/api/v5/merkle-anchor
```

## 8. Verificar una prueba OTS

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/verify-ots/2026-10-12"
```

La API hace tres cosas:
1. **Coherencia ledger ↔ prueba.** Recalcula la raíz con los N bloques anclados y la compara con el digest de la prueba.
2. **Upgrade.** Consulta las atestaciones pendientes a los calendarios incluidos en la lista blanca. Si alguna se ha actualizado, reescribe el `.ots`.
3. **Contraste.** Si la prueba ya contiene `BitcoinBlockHeaderAttestation`, compara la merkle root del bloque con la que devuelve el explorador Esplora.

Estados posibles:
- `pendiente_confirmacion_bitcoin`
- `confirmada_en_bitcoin_y_contrastada`
- `confirmada_en_bitcoin (contraste con explorador no disponible)`

**Verificación independiente** con el cliente oficial (`pip install opentimestamps-client`):

```bash
ots info    uasi_workspace/ots_proofs/2026-10-12.ots
ots upgrade uasi_workspace/ots_proofs/2026-10-12.ots
ots verify  -d <raiz_merkle_hex> uasi_workspace/ots_proofs/2026-10-12.ots   # requiere un nodo Bitcoin local para verificar sin terceros
```

## 9. Notas operativas

- **Formato del ledger:** es JSONL append-only y hash-encadenado (`ledger/ledger_auditoria.jsonl`). Haga copias de seguridad junto con `ots_proofs/`.
- **Modelos:** se guardan con joblib junto a un `.sha256` y el checksum se verifica **antes** de deserializar. Si el checksum no coincide, la carga responde 500 y el health check informa `integridad_comprometida`.
- **Límite por worker:** el rate limit en memoria se aplica por separado en cada worker. Use Redis para tener un límite global.
- **Servicio expuesto:** coloque un proxy inverso con TLS (nginx o Traefik) delante del servicio.
