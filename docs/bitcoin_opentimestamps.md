# Anclaje en Bitcoin L1 con OpenTimestamps

Este documento explica cómo UASI v5 (5.1.0-enterprise) convierte su ledger de auditoría en una
**prueba de existencia verificable públicamente**, por qué elige Bitcoin L1 y cómo verificar una prueba
`.ots` años después, aunque UASI ya no exista.

---

## 1. El problema: un ledger hash-encadenado no basta

Cada bloque del ledger incluye el hash del anterior (`hash_previo`). Si se modifica un bloque antiguo,
cambian su `hash_bloque` y todos los siguientes, así que la manipulación **se detecta**
(`/api/v5/cryptographic-ledger?verificar=true`).

Ahora bien, quien controle el servidor puede **reescribir toda la cadena** desde el bloque alterado y
el resultado será internamente coherente. Un encadenamiento local no demuestra **cuándo** existía cada
versión. Para eso hace falta un **testigo externo, público e inmutable**: Bitcoin.

## 2. ¿Qué es OpenTimestamps (OTS)?

[OpenTimestamps](https://opentimestamps.org) es un estándar abierto y gratuito de **sellado de tiempo
sobre Bitcoin**:

1. El cliente envía un **digest** (32 bytes, aquí la raíz de Merkle diaria) a uno o varios
   **servidores de calendario**. El documento nunca sale del sistema; solo viaja su hash.
2. Cada calendario agrega en un árbol de Merkle propio miles de digests recibidos de muchos usuarios.
3. Periódicamente, el calendario publica la **raíz de ese árbol** en una transacción de Bitcoin, dentro
   de una salida `OP_RETURN`.
4. Al principio el cliente recibe una prueba **incompleta** con una `PendingAttestation` ("pregunta más
   tarde a este calendario").
5. Cuando la transacción se confirma en un bloque, el cliente hace **upgrade**: descarga del calendario
   la ruta que faltaba y la prueba pasa a contener una `BitcoinBlockHeaderAttestation(altura)`.

Una prueba `.ots` completa es una **cadena de operaciones deterministas** (`append`, `prepend`,
`sha256`…) que, aplicadas al digest original, producen exactamente la **raíz de Merkle de la cabecera
de un bloque de Bitcoin**. Para verificarla solo hacen falta el digest, el archivo `.ots` y una copia
de la cadena de bloques de Bitcoin. **No hay que confiar en el calendario**: si este mintiera, las
operaciones no darían como resultado una cabecera real.

**Coste:** una sola transacción agrega un número ilimitado de sellos, así que el servicio es gratuito
para el usuario y escala sin límite.

## 3. ¿Qué es OP_RETURN?

`OP_RETURN` es un *opcode* de Bitcoin Script que marca una salida de transacción como **no gastable**
y permite incrustar en ella hasta 80 bytes de datos arbitrarios (por política estándar de relay).

- Al no ser gastable, los nodos la **descartan del conjunto UTXO** y no la cargan en la memoria de estado.
- Los datos quedan **grabados para siempre** en el bloque y protegidos por toda la prueba de trabajo
  que se acumule después.
- OpenTimestamps solo necesita 32 bytes (una raíz SHA-256), así que cabe de sobra.

```text
Transacción del calendario
  ├─ input:  UTXO del calendario (paga la comisión)
  ├─ output 0: OP_RETURN <32 bytes = raíz agregada del calendario>
  └─ output 1: cambio de vuelta al calendario
```

En sentido estricto, la prueba `.ots` termina en la **merkle root de la cabecera del bloque**: la ruta
va del digest a la raíz del calendario, de ahí a la transacción (txid) y del txid a la merkle root del
bloque.

## 4. ¿Por qué Bitcoin L1?

| Criterio | Bitcoin L1 | Alternativas (L2, sidechains, cadenas privadas, TSA RFC 3161) |
|---|---|---|
| **Seguridad acumulada** | La prueba de trabajo más grande del mundo; reescribir un bloque antiguo exige rehacer todo el trabajo posterior. | Menor hashrate o seguridad delegada en un operador o comité. |
| **Neutralidad** | Sin un emisor central que pueda revocar o reescribir. | Una TSA o un consorcio puede desaparecer, revocar certificados o ser coaccionado. |
| **Longevidad** | Funciona sin interrupción desde 2009 y la cadena está replicada en decenas de miles de nodos. | Dependen de la continuidad de un proyecto o de una empresa. |
| **Verificación independiente** | Cualquiera puede verificar con su propio nodo (Bitcoin Core) sin pedir permiso. | Suelen requerir confiar en una API, una PKI o un operador. |
| **Coste para UASI** | Gratuito gracias a la agregación de OTS. | Comisiones propias o contratos de servicio. |

Las capas 2 (Lightning, rollups…) no ofrecen un registro público permanente de datos arbitrarios. La
finalidad de un sello de tiempo es justamente que **perdure y sea verificable por terceros**, y eso solo
lo da la capa base.

## 5. Flujo completo en UASI v5

```text
 ┌────────────┐   POST /api/v1/procesar · /uasi-master-execute · /streaming-ingest
 │  Datos     │   (inputs, targets, metadatos)
 └─────┬──────┘
       │  hash_datos = SHA256(X.tobytes() ‖ y.tobytes())   ← solo se guarda el hash, no los datos
       ▼
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  LEDGER (JSONL append-only)                                                    │
 │  bloque_i = {indice, timestamp, fecha, evento, actor, datos, hash_datos,       │
 │              hash_previo = hash_bloque_{i-1}}                                  │
 │  hash_bloque_i = SHA256(JSON canónico de bloque_i)                             │
 └─────┬──────────────────────────────────────────────────────────────────────────┘
       │  GET /api/v5/merkle-anchor?fecha=YYYY-MM-DD
       ▼
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  ÁRBOL DE MERKLE DEL DÍA                                                        │
 │  hoja_i = SHA256(0x00 ‖ hash_bloque_i)          ← separación de dominio         │
 │  nodo   = SHA256(0x01 ‖ izq ‖ der)              ← evita ataques de 2.ª preimagen│
 │  nodo impar → se promociona al nivel superior                                  │
 │  RAÍZ (32 bytes) + n_bloques                                                   │
 └─────┬──────────────────────────────────────────────────────────────────────────┘
       │  POST de la raíz a OTS_CALENDARS (a/b.pool.opentimestamps.org, a.pool.eternitywall.com)
       ▼
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  OPENTIMESTAMPS                                                                │
 │  ots_proofs/<fecha>.ots (PendingAttestation) · <fecha>_<sello>.ots · <fecha>.json│
 │  estado = pendiente_confirmacion_bitcoin                                       │
 └─────┬──────────────────────────────────────────────────────────────────────────┘
       │  el calendario agrega y publica la transacción (≈ 1–6 h, según el calendario y las comisiones)
       ▼
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  BITCOIN L1                                                                    │
 │  tx con OP_RETURN <raíz del calendario> → bloque de altura H                   │
 │  GET /api/v5/verify-ots/<fecha> → upgrade → BitcoinBlockHeaderAttestation(H)   │
 │  contraste de la merkle root de la cabecera H con Esplora                      │
 │  estado = confirmada_en_bitcoin_y_contrastada                                  │
 └────────────────────────────────────────────────────────────────────────────────┘
```

Detalles de implementación (`MerkleOpenTimestampsManager`):

- `n_bloques` se guarda en `<fecha>.json`. La verificación recalcula la raíz **exactamente sobre los
  primeros `n_bloques` del día**, así que los bloques añadidos después (incluido el propio evento
  `ANCLAJE_MERKLE_OTS`) no invalidan la prueba. Se pueden hacer varios anclajes al día; cada uno queda
  en `<fecha>_<sello>.ots` y `<fecha>.ots` apunta al último.
- Basta con que acepten la raíz `OTS_MIN_CALENDARS` calendarios (1 por defecto). Usar varios aporta
  redundancia.
- El nombre de archivo se valida con el patrón `YYYY-MM-DD`, lo que impide el *path traversal*.
- Los **calendarios públicos solo anclan en mainnet**. Con `BITCOIN_NETWORK=testnet` hace falta un
  calendario OTS propio (`OTS_CALENDARS`); Esplora cambia automáticamente a testnet.

### Operación diaria

```bash
# Anclar el día actual (o una fecha concreta). Se recomienda un cron a las 23:55 UTC.
./scripts/anclar_ledger_bitcoin.sh              # = make anchor-btc
./scripts/anclar_ledger_bitcoin.sh 2026-10-12

# Unas horas después: upgrade y verificación
./scripts/verificar_ots.sh 2026-10-12           # vía API
./scripts/verificar_ots.sh 2026-10-12 --local   # con el cliente `ots` sobre PATH_WORKSPACE/ots_proofs
```

Ejemplo de cron:
```cron
55 23 * * * cd /opt/uasi_v5_repo && UASI_API_URL=http://localhost:8000 ./scripts/anclar_ledger_bitcoin.sh >> /var/log/uasi_anchor.log 2>&1
0  6  * * * cd /opt/uasi_v5_repo && ./scripts/verificar_ots.sh $(date -u -d yesterday +\%F) >> /var/log/uasi_anchor.log 2>&1
```

## 6. Cómo verificar un `.ots` años después

La prueba se diseñó para que la pueda verificar **cualquier auditor sin UASI, sin la API y sin los
calendarios**. Solo necesita:

| Material | Dónde está | Cómo conservarlo |
|---|---|---|
| `ledger_auditoria.jsonl` | `PATH_WORKSPACE/ledger/` | Copia WORM u offline (S3 Object Lock, cinta, etc.). |
| `<fecha>.ots` **ya actualizado** | `PATH_WORKSPACE/ots_proofs/` | Junto al ledger, **después** de que `verify-ots` informe de la confirmación. |
| `<fecha>.json` | `PATH_WORKSPACE/ots_proofs/` | Metadatos: `raiz_merkle`, `n_bloques` y `sellado_en`. |
| Cadena de Bitcoin | Cualquier nodo | Bitcoin Core propio (lo ideal) o, como mínimo, varios exploradores independientes. |

> ⚠️ **Haga siempre el upgrade antes de archivar.** Un `.ots` con solo `PendingAttestation` depende de
> que el calendario siga en línea. Una vez actualizado, la prueba es **autocontenida**.

### Paso 1. Recalcular la raíz de Merkle desde el ledger

```python
# verificar_raiz.py: solo usa la biblioteca estándar
import hashlib, json, sys

fecha, n = sys.argv[1], int(sys.argv[2])          # p. ej. 2026-10-12 4  (n_bloques de <fecha>.json)
bloques = [b for b in map(json.loads, open("ledger_auditoria.jsonl")) if b["fecha"] == fecha][:n]

nivel = [hashlib.sha256(b"\x00" + bytes.fromhex(b["hash_bloque"])).digest() for b in bloques]
while len(nivel) > 1:
    sig = [hashlib.sha256(b"\x01" + nivel[i] + nivel[i + 1]).digest() for i in range(0, len(nivel) - 1, 2)]
    if len(nivel) % 2:
        sig.append(nivel[-1])
    nivel = sig
print(nivel[0].hex())
```

```bash
python3 verificar_raiz.py 2026-10-12 4
# Debe coincidir con "raiz_merkle" de 2026-10-12.json
```

Para comprobar además que el ledger no se reescribió **internamente**, recalcule cada `hash_bloque`:
SHA-256 del JSON canónico (claves ordenadas, sin espacios) de `{indice, timestamp, fecha, evento,
actor, datos, hash_datos, hash_previo}`. Compruebe también el encadenamiento de `hash_previo`.

### Paso 2. Verificar la prueba OTS contra Bitcoin

```bash
pip install opentimestamps-client

# Información legible de la prueba (operaciones y atestaciones)
ots info 2026-10-12.ots

# Verificación del digest (la raíz recalculada en el paso 1)
ots verify -d <raiz_merkle_hex> 2026-10-12.ots
# → "Success! Bitcoin block 9xxxxx attests existence as of 2026-10-12 UTC"
```

- Con un **nodo Bitcoin Core local** (`bitcoind` con `server=1`), `ots verify` consulta las cabeceras
  por RPC y la verificación **no depende de terceros**. Es el método recomendado para auditorías.
- Sin un nodo local se puede usar `ots --no-bitcoin verify …` para ver la altura del bloque y comprobar
  después la cabecera a mano.

### Paso 3. Contrastar manualmente la cabecera del bloque (sin nodo)

```bash
ots info 2026-10-12.ots | grep -i "BitcoinBlockHeaderAttestation"   # → altura H
curl -s https://blockstream.info/api/block-height/H                  # → hash del bloque
curl -s https://blockstream.info/api/block/<hash> | python3 -m json.tool | grep merkle_root
```

La `merkle_root` del bloque debe coincidir con el resultado final de las operaciones de la prueba
(`ots info` lo muestra; UASI lo expone como `merkle_root_bloque` en orden de visualización). Para
mayor robustez, consulte **dos o más exploradores independientes** o, mejor, su propio nodo.

### Paso 4. Interpretar el resultado

Si se cumplen las tres condiciones:

1. la raíz recalculada coincide con la del `.json`/`.ots`,
2. la prueba OTS lleva esa raíz a la cabecera del bloque `H`, y
3. el bloque `H` pertenece a la cadena con más trabajo de Bitcoin,

queda demostrado criptográficamente que **los bloques del ledger de esa fecha existían, con ese contenido
exacto, como muy tarde en el instante en que se minó el bloque `H`**. Cualquier modificación posterior
(aunque sea de un solo byte en un bloque, en el orden o en el número de bloques) produce otra raíz y la
verificación falla.

### Qué demuestra y qué no

| Demuestra | No demuestra |
|---|---|
| Existencia e integridad del ledger en una fecha (cota superior temporal). | Que los datos de entrada fueran *verdaderos* o correctos. |
| Que el ledger no se ha reescrito después del anclaje. | Que no se omitieran eventos *antes* del anclaje (por eso conviene anclar con frecuencia). |
| Orden y número de bloques cubiertos (`n_bloques`). | La identidad real del `actor` más allá del `sub` del JWT. |

## 7. Robustez a largo plazo

- **SHA-256**: si en el futuro se debilitara, una prueba ya anclada sigue siendo válida mientras no
  existan colisiones prácticas. OTS permite volver a sellar (*re-timestamp*) la prueba antigua con un
  hash más fuerte antes de que eso ocurra.
- **Formato abierto**: el formato `.ots` está documentado y tiene implementaciones independientes
  (Python, Java, JavaScript y Rust).
- **Independencia de los calendarios**: una vez actualizada, la prueba no necesita que ningún calendario
  siga existiendo.
- **Recomendación operativa**: archive cada día el `.ots` actualizado, el `.json` y una copia inmutable
  del ledger, y verifique periódicamente con su propio nodo.

## Referencias

- OpenTimestamps: <https://opentimestamps.org> · <https://github.com/opentimestamps/opentimestamps-client>
- Bitcoin Script, `OP_RETURN`: <https://developer.bitcoin.org/reference/transactions.html>
- Árboles de Merkle en Bitcoin: <https://developer.bitcoin.org/reference/block_chain.html#merkle-trees>
- API de Esplora: <https://github.com/Blockstream/esplora/blob/master/API.md>
