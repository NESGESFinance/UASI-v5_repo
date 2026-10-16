#!/usr/bin/env bash
# =============================================================================
# Ancla la raíz de Merkle diaria del ledger en Bitcoin L1 (OpenTimestamps)
# llamando a GET /api/v5/merkle-anchor.
#
# Uso:
#   ./scripts/anclar_ledger_bitcoin.sh               # hoy (UTC)
#   ./scripts/anclar_ledger_bitcoin.sh 2026-10-12    # fecha concreta
#
# Variables:
#   UASI_API_URL  URL base de la API          (defecto: http://localhost:8000)
#   UASI_TOKEN    Token Bearer. Si falta y la API está en modo development,
#                 se obtiene uno de prueba en /api/v5/token.
#
# Cron sugerido (23:55 UTC):
#   55 23 * * * cd /opt/uasi_v5_repo && ./scripts/anclar_ledger_bitcoin.sh >> anclajes.log 2>&1
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${RAIZ}/.env" ]] && set -a && . "${RAIZ}/.env" && set +a

API="${UASI_API_URL:-http://localhost:8000}"
FECHA="${1:-}"

if [[ -n "${FECHA}" && ! "${FECHA}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "ERROR: fecha inválida '${FECHA}' (formato YYYY-MM-DD)" >&2
  exit 2
fi

command -v curl >/dev/null || { echo "ERROR: se necesita curl" >&2; exit 1; }

obtener_token() {
  if [[ -n "${UASI_TOKEN:-}" ]]; then
    echo "${UASI_TOKEN}"
    return
  fi
  local resp
  resp="$(curl -fsS "${API}/api/v5/token?sujeto=script_anclaje&minutos=10")" || {
    echo "ERROR: defina UASI_TOKEN (en producción /api/v5/token está deshabilitado)" >&2
    exit 1
  }
  python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])' <<< "${resp}"
}

TOKEN="$(obtener_token)"
URL="${API}/api/v5/merkle-anchor"
[[ -n "${FECHA}" ]] && URL="${URL}?fecha=${FECHA}"

echo "→ Anclando ledger en Bitcoin: ${URL}"
HTTP_CODE="$(curl -sS -o /tmp/uasi_anclaje_$$.json -w '%{http_code}' \
  -H "Authorization: Bearer ${TOKEN}" "${URL}")"
CUERPO="$(cat /tmp/uasi_anclaje_$$.json)"; rm -f /tmp/uasi_anclaje_$$.json

if command -v python3 >/dev/null; then
  python3 -m json.tool <<< "${CUERPO}" 2>/dev/null || echo "${CUERPO}"
else
  echo "${CUERPO}"
fi

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "✘ Error HTTP ${HTTP_CODE}" >&2
  exit 1
fi

ESTADO="$(python3 -c 'import sys,json; print(json.load(sys.stdin)["anclaje"].get("estado",""))' <<< "${CUERPO}" 2>/dev/null || true)"
case "${ESTADO}" in
  pendiente_confirmacion_bitcoin)
    echo "✔ Raíz enviada a los calendarios OTS. Confirmación en Bitcoin en unas horas."
    echo "  Verifique después con: ./scripts/verificar_ots.sh ${FECHA:-$(date -u +%F)}" ;;
  *)
    echo "✘ Anclaje no completado (estado: ${ESTADO:-desconocido})" >&2
    exit 1 ;;
esac
