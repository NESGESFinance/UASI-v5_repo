#!/usr/bin/env bash
# =============================================================================
# Genera SECRET_JWT_KEY y UASI_MODEL_HMAC_KEY criptográficamente seguros.
#
# Uso:
#   ./scripts/generar_secretos.sh            # muestra los secretos (export ...)
#   ./scripts/generar_secretos.sh --write    # los escribe/actualiza en .env
#   eval "$(./scripts/generar_secretos.sh)"  # los exporta en la shell actual
#
# ATENCIÓN: cambiar UASI_MODEL_HMAC_KEY invalida los checksums de los modelos
# ya persistidos (habrá que reentrenarlos). Cambiar SECRET_JWT_KEY invalida
# todos los tokens emitidos.
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RAIZ}/.env"

generar() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-64
  else
    echo "ERROR: se necesita python3 u openssl" >&2
    exit 1
  fi
}

JWT_KEY="$(generar)"
HMAC_KEY="$(generar)"

# Sustituye o añade CLAVE=valor en .env (sin depender de sed -i, portable macOS/Linux)
fijar_variable() {
  local clave="$1" valor="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${clave}=" "${ENV_FILE}"; then
    awk -v k="${clave}" -v v="${valor}" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' "${ENV_FILE}" > "${tmp}"
  else
    cat "${ENV_FILE}" > "${tmp}"
    printf '%s=%s\n' "${clave}" "${valor}" >> "${tmp}"
  fi
  mv "${tmp}" "${ENV_FILE}"
}

if [[ "${1:-}" == "--write" ]]; then
  if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${RAIZ}/.env.example" "${ENV_FILE}"
    echo "Creado ${ENV_FILE} a partir de .env.example"
  fi
  fijar_variable "SECRET_JWT_KEY" "${JWT_KEY}"
  fijar_variable "UASI_MODEL_HMAC_KEY" "${HMAC_KEY}"
  chmod 600 "${ENV_FILE}"
  echo "✔ Secretos escritos en ${ENV_FILE} (permisos 600). No lo suba al repositorio."
else
  echo "export SECRET_JWT_KEY='${JWT_KEY}'"
  echo "export UASI_MODEL_HMAC_KEY='${HMAC_KEY}'"
fi
