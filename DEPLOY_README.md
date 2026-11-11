# UASI v5 Enterprise Production — Guía de despliegue

Versión `5.5.0-enterprise` · Paquete `src/uasi_v5/` (app ASGI: `uasi_v5.main:app`) · Dependencias: `requirements.txt` (+ `requirements-dev.txt` para desarrollo).

> Esta guía cubre el despliegue del **backend** (API FastAPI). El **panel de administración** es una aplicación web independiente que consume esta API vía la variable `NEXT_PUBLIC_UASI_API_URL`.

---

## 0. Camino rápido con Docker (recomendado)

Es la forma más sencilla y reproducible de poner UASI en producción (API + Redis, usuario no-root, red mínima de capacidades, health checks).

```bash
# 1. Preparar variables de entorno y secretos
cp .env.example .env
./scripts/generar_secretos.sh --write        # genera SECRET_JWT_KEY y SECRET_KEY (HMAC) seguros en .env

# 2. Marcar entorno de producción en .env
#    UASI_ENV=production   (desactiva el token de prueba GET /api/v5/token)

# 3. Levantar API + Redis
docker compose -f docker/docker-compose.yml --env-file .env up -d --build

# 4. Crear la cuenta administradora inicial (login real usuario/contraseña)
docker compose -f docker/docker-compose.yml exec uasi-api python -m scripts.crear_admin

# 5. Comprobar salud
curl -s http://localhost:8000/api/v5/health | python3 -m json.tool
```

Coloque siempre un **proxy inverso con TLS** (nginx, Traefik o Caddy) delante del contenedor; no exponga el puerto 8000 directamente a Internet.

---

## 1. Instalación manual (sin Docker)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt            # dependencias de ejecución
pip install -r requirements-dev.txt        # solo para desarrollo/tests (ruff, mypy, pytest)
pip install -e .                           # instala el paquete uasi_v5 en modo editable

# Pruebas (sin red)
pytest -q
# Pruebas incluyendo el anclaje real contra calendarios OpenTimestamps
UASI_TEST_OTS_RED=1 pytest -q
```

---

## 2. Variables de entorno

Todas se documentan en `.env.example`. Las esenciales:

| Variable | Obligatoria | Defecto | Descripción |
|---|---|---|---|
| `SECRET_JWT_KEY` | **Sí** | — | Clave HMAC para firmar/validar JWT. Si falta, los endpoints protegidos y el login responden **503** (el sistema nunca queda abierto). Mínimo 32 caracteres. |
| `SECRET_KEY` | **Recomendada** | — | Clave HMAC-SHA256 del manifiesto de integridad de los modelos (alias heredado `UASI_MODEL_HMAC_KEY`). Mínimo 32 caracteres y distinta de `SECRET_JWT_KEY`. Los valores de ejemplo se rechazan al arrancar. |
| `UASI_ENV` | No | `development` | Con `production` se desactiva el token de PRUEBA `GET /api/v5/token`. El login real `POST /api/v5/token` funciona en todos los entornos. |
| `DATABASE_URL` | No | `sqlite:///$PATH_WORKSPACE/uasi.db` | SQLite en desarrollo; en producción PostgreSQL, p. ej. `postgresql+psycopg://uasi:clave@db:5432/uasi`. |
| `PATH_WORKSPACE` | No | `./uasi_workspace` | Raíz de datos (`modelos/`, `ledger/`, `ots_proofs/`, SQLite). Debe ser un volumen persistente compartido por todos los workers. |
| `UASI_ADMIN_USER` / `UASI_ADMIN_PASSWORD` | No | `admin` / (vacío) | Credenciales que lee `python -m scripts.crear_admin`. Si la contraseña queda vacía, el script genera una segura y la imprime una sola vez. |
| `RATE_LIMIT_STORAGE_URI` | No | `memory://` | Con varios workers, usar `redis://host:6379` para que el límite sea global (docker-compose ya lo fija a `redis://redis:6379`). |
| `JWT_ALGORITHM` / `JWT_ISSUER` / `JWT_EXP_MINUTES` | No | `HS256` / `uasi-v5` / `60` | Parámetros del token. |
| `BITCOIN_NETWORK` | No | `mainnet` | `mainnet` o `testnet` (los calendarios OTS públicos solo anclan en mainnet). |
| `OTS_CALENDARS` / `OTS_MIN_CALENDARS` / `OTS_TIMEOUT_SECONDS` | No | pools públicos / `1` / `10` | Calendarios OpenTimestamps y parámetros de anclaje. |
| `ESPLORA_API_URL` | No | Blockstream | API Esplora para contrastar la merkle root del bloque. |
| `NGF_TRUST_API_URL` | No | `https://nesgesfinancetrust.com/api` | Base de la API de NESGESFinanceTrust que consume `/api/v5/trust/*`. |
| `NGF_TRUST_TIMEOUT_SECONDS` | No | `8.0` | Timeout de red del cliente Trust. |
| `NGF_TRUST_RUNA_PRINCIPAL` | No | `NGF•BTC•AM` | Nombre de la runa principal del ecosistema. |
| `LOG_LEVEL` | No | `INFO` | Logs en formato JSON por stdout. |

---

## 3. Generar los secretos

```bash
./scripts/generar_secretos.sh --write     # escribe SECRET_JWT_KEY y SECRET_KEY en .env (permisos 600)
# o manualmente:
export SECRET_JWT_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Guarde ambos valores en un gestor de secretos (Vault, AWS Secrets Manager…). **Nunca** los suba al repositorio. Cambiar `SECRET_JWT_KEY` invalida todos los tokens emitidos; cambiar `SECRET_KEY` invalida los checksums de los modelos ya persistidos (habrá que reentrenarlos).

---

## 4. Arranque

```bash
# Producción: gunicorn + workers uvicorn (el paquete expone uasi_v5.main:app)
gunicorn uasi_v5.main:app \
  -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 --timeout 300 --graceful-timeout 30 --access-logfile -

# Desarrollo
uvicorn uasi_v5.main:app --reload --port 8000
# o:  make run
```

Se usa `--timeout 300` porque el entrenamiento puede durar varios segundos: se ejecuta en el threadpool y no bloquea el event loop, pero ocupa el worker. Los locks de archivo (`filelock`) garantizan la consistencia entre workers siempre que compartan el mismo `PATH_WORKSPACE`.

---

## 5. Autenticación (login real)

La cuenta administradora se crea con el script idempotente:

```bash
python -m scripts.crear_admin                 # usa UASI_ADMIN_USER / UASI_ADMIN_PASSWORD
python -m scripts.crear_admin --reset-password  # reestablece la contraseña del admin
```

Obtener un token con usuario y contraseña (funciona también en producción):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v5/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<su_contraseña>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/admin/ledger?page=1&limit=20"
```

> El token de **prueba** `GET /api/v5/token?sujeto=…` solo existe cuando `SECRET_JWT_KEY` está definida y `UASI_ENV` ≠ `production`. En producción responde 404. Para integraciones externas, su IdP puede emitir tokens con los claims `iss=JWT_ISSUER`, `sub`, `scope`, `iat` y `exp`, firmados con `SECRET_JWT_KEY`.

---

## 6. Endpoints

### Públicos
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v5/health` | Health check real: workspace, checksums de modelos, integridad del ledger y disponibilidad de OTS. |
| POST | `/api/v5/token` | **Login real** por usuario/contraseña (JSON). Devuelve JWT con scopes. |
| GET | `/api/v5/token` | Token de PRUEBA (solo desarrollo; 404 en producción). |

### Protegidos (requieren `Authorization: Bearer <token>`)
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/procesar` | Agentes clásicos (`red_neuronal`, `svm`, `arbol_decision`). |
| POST | `/api/v5/uasi-master-execute` | Orquestador semántico: elige ruta (diagnóstico, auditoría, streaming, explicabilidad, evolutivo, profundo o automática). |
| POST | `/api/v5/streaming-ingest` | Aprendizaje online multiclase con clases dinámicas. |
| POST | `/api/v5/orquestar` | **Capa conversacional (v5.4.0):** interpreta una intención en lenguaje natural, el guardián la clasifica (403 si no permitida/inyección) y el orquestador decide el plan; cada interacción se ancla en el ledger. |
| GET | `/api/v5/trust/estado` | **NESGESFinanceTrust (v5.5.0):** resumen on-chain (salud + runa NGF•BTC•AM + RWA/MiCA). |
| GET | `/api/v5/trust/runes?limite=` | Listado de runas indexadas por NESGESFinanceTrust. |
| GET | `/api/v5/trust/rwa?limite=` | Activos del mundo real (RWA) tokenizados y su clasificación MiCA. |
| GET | `/api/v5/cryptographic-ledger?page=&page_size=&verificar=` | Ledger paginado; con `verificar=true` comprueba la integridad de la cadena. |
| GET | `/api/v5/merkle-anchor[?fecha=YYYY-MM-DD]` | Calcula la raíz de Merkle del día y la ancla en Bitcoin. |
| GET | `/api/v5/verify-ots/{fecha}` | Verifica y actualiza la prueba `.ots`. |
| POST | `/api/v5/jev-dispatch` · `/lote` | Despacho de agentes JEV (individual y por lote). |
| GET | `/api/v5/jev/agentes` · `/api/v5/jev/metricas` | Estado y métricas de los agentes JEV. |

### Administración (requieren scope `admin`)
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v5/admin/agents` · `/metrics` · `/ledger` · `/health/detailed` | Vistas agregadas para el panel. |
| POST | `/api/v5/admin/anchor` · `/tokens/revoke` | Anclaje manual y revocación de tokens. |
| GET/POST/PATCH/DELETE | `/api/v5/admin/users[/{username}]` | Gestión de usuarios (alta, edición, baja lógica). |

Ejemplo de petición al endpoint maestro:

```bash
curl -X POST http://localhost:8000/api/v5/uasi-master-execute \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt_intencion":"clasificar operaciones","inputs":[[0.1,1.2],[0.3,0.8],[2.1,0.1],[1.9,0.3]],"targets":[0,0,1,1],"metadatos_contexto":{"semilla":7}}'
```

---

## 7. Anclar el ledger en Bitcoin (OpenTimestamps)

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/merkle-anchor"
```

El proceso funciona así:
1. Se calcula la raíz de Merkle de los bloques del día (hoja = `SHA256(0x00‖hash_bloque)`, nodo = `SHA256(0x01‖izq‖der)`).
2. La raíz se envía, con un nonce, a los calendarios OTS. Estos agregan los compromisos y los publican en una transacción Bitcoin con `OP_RETURN`.
3. Se guardan:
   - `PATH_WORKSPACE/ots_proofs/<fecha>.ots`: prueba del último anclaje del día.
   - `<fecha>_<hora>.ots`: histórico inmutable de cada anclaje.
   - `<fecha>.json`: raíz, número de bloques y calendarios usados.

La confirmación en Bitcoin suele tardar unas horas. Se recomienda un anclaje diario, por ejemplo a las 23:55 UTC:

```cron
55 23 * * * curl -s -H "Authorization: Bearer $TOKEN_SERVICIO" http://localhost:8000/api/v5/merkle-anchor
```

---

## 8. Verificar una prueba OTS

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v5/verify-ots/2026-10-12"
```

La API hace tres cosas:
1. **Coherencia ledger ↔ prueba.** Recalcula la raíz con los N bloques anclados y la compara con el digest de la prueba.
2. **Upgrade.** Consulta las atestaciones pendientes a los calendarios de la lista blanca; si alguna se actualizó, reescribe el `.ots`.
3. **Contraste.** Si la prueba contiene `BitcoinBlockHeaderAttestation`, compara la merkle root del bloque con la que devuelve Esplora.

Estados posibles: `pendiente_confirmacion_bitcoin`, `confirmada_en_bitcoin_y_contrastada`, `confirmada_en_bitcoin (contraste con explorador no disponible)`.

Verificación independiente con el cliente oficial (`pip install opentimestamps-client`):

```bash
ots info    uasi_workspace/ots_proofs/2026-10-12.ots
ots upgrade uasi_workspace/ots_proofs/2026-10-12.ots
ots verify  -d <raiz_merkle_hex> uasi_workspace/ots_proofs/2026-10-12.ots
```

---

## 9. Integración continua (CI)

El flujo de CI (ruff + mypy + pytest en Python 3.11 y 3.12) está listo en `docs/ci.yml.example`. No se encuentra activo en `.github/workflows/` porque requiere el permiso **workflows** en la GitHub App. Para activarlo, siga las instrucciones de `docs/ci.README.md`.

---

## 10. Notas operativas

- **Formato del ledger:** por defecto backend SQL (`DATABASE_URL`, tabla `ledger_entries`). El modo heredado `UASI_LEDGER_BACKEND=jsonl` usa `ledger/ledger_auditoria.jsonl` (append-only, hash-encadenado). Respalde el ledger junto con `ots_proofs/`.
- **Modelos:** se guardan con joblib junto a un manifiesto firmado; el checksum se verifica **antes** de deserializar. Si no coincide, la carga responde 500 y el health check informa `integridad_comprometida`.
- **Rate limit:** en memoria se aplica por worker; use Redis para un límite global (docker-compose ya lo configura).
- **TLS:** coloque siempre un proxy inverso con TLS delante del servicio.
- **Panel de administración:** configúrelo con `NEXT_PUBLIC_UASI_API_URL` apuntando a esta API (con TLS). Mientras el backend no esté accesible, el panel opera en modo demostración.
