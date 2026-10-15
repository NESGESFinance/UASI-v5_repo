#!/usr/bin/env bash
# =============================================================================
# Verifica la prueba OpenTimestamps (.ots) de una fecha.
#
# Modo API (por defecto): GET /api/v5/verify-ots/{fecha}
#   → coherencia ledger↔prueba, upgrade contra calendarios y contraste del
#     bloque Bitcoin con un explorador Esplora.
# Modo local (--local): usa el cliente oficial `ots` sobre el archivo .ots del
#   workspace, sin pasar por la API (verificación independiente).
#
# Uso:
#   ./scripts/verificar_ots.sh 2026-10-12
#   ./scripts/verificar_ots.sh 2026-10-12 --local
#
# Variables: UASI_API_URL, UASI_TOKEN, PATH_WORKSPACE (modo local)
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${RAIZ}/.env" ]] && set -a && . "${RAIZ}/.env" && set +a

FECHA="${1:-$(date -u +%F)}"
MODO="${2:-}"
API="${UASI_API_URL:-http://localhost:8000}"

if [[ ! "${FECHA}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "ERROR: fecha inválida '${FECHA}' (formato YYYY-MM-DD)" >&2
  exit 2
fi

if [[ "${MODO}" == "--local" ]]; then
  WS="${PATH_WORKSPACE:-${RAIZ}/uasi_workspace}"
  [[ "${WS}" != /* ]] && WS="${RAIZ}/${WS#./}"
  OTS="${WS}/ots_proofs/${FECHA}.ots"
  META="${WS}/ots_proofs/${FECHA}.json"
  [[ -f "${OTS}" ]] || { echo "ERROR: no existe ${OTS}" >&2; exit 1; }
  command -v ots >/dev/null || { echo "ERROR: instale opentimestamps-client (pip install opentimestamps-client)" >&2; exit 1; }
  echo "→ Información de la prueba"
  ots info "${OTS}"
  echo "→ Upgrade contra calendarios"
  ots upgrade "${OTS}" || echo "  (aún pendiente de confirmación en Bitcoin)"
  if [[ -f "${META}" ]]; then
    RAIZ_MERKLE="$(python3 -c 'import sys,json; print(json.load(open(sys.argv[1]))["raiz_merkle"])' "${META}")"
    echo "→ Verificación del digest ${RAIZ_MERKLE}"
    echo "  (requiere un nodo Bitcoin Core local accesible para verificación sin terceros)"
    ots verify -d "${RAIZ_MERKLE}" "${OTS}" || true
  fi
  exit 0
fi

command -v curl >/dev/null || { echo "ERROR: se necesita curl" >&2; exit 1; }
if [[ -z "${UASI_TOKEN:-}" ]]; then
  UASI_TOKEN="$(curl -fsS "${API}/api/v5/token?sujeto=script_verificacion&minutos=10" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')" || {
    echo "ERROR: defina UASI_TOKEN (en producción /api/v5/token está deshabilitado)" >&2; exit 1; }
fi

echo "→ Verificando prueba OTS de ${FECHA} vía API"
TMP="$(mktemp)"
HTTP_CODE="$(curl -sS -o "${TMP}" -w '%{http_code}' -H "Authorization: Bearer ${UASI_TOKEN}" \
  "${API}/api/v5/verify-ots/${FECHA}")"
CUERPO="$(cat "${TMP}")"; rm -f "${TMP}"
python3 -m json.tool <<< "${CUERPO}" 2>/dev/null || echo "${CUERPO}"
[[ "${HTTP_CODE}" == "200" ]] || { echo "✘ Error HTTP ${HTTP_CODE}" >&2; exit 1; }

python3 - "${CUERPO}" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
coincide = d.get("coherencia_ledger", {}).get("coincide")
print(f"\nEstado: {d.get('estado')}")
print(f"Coherencia ledger ↔ prueba: {'✔' if coincide else '✘'}")
sys.exit(0 if coincide else 3)
PY
