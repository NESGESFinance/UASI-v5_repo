# Flujo de CI de UASI v5 (plantilla)

Este archivo es una plantilla de GitHub Actions. No puede subirse automáticamente a
`.github/workflows/` porque la GitHub App usada por el asistente no tiene el permiso
`workflows`.

Para activarlo, el propietario del repositorio debe:

1. Conceder el permiso **workflows** a la GitHub App (Settings → Integrations/GitHub Apps),
   **o** hacerlo manualmente.
2. Mover este archivo a su ubicación activa:
   ```bash
   mkdir -p .github/workflows
   git mv docs/ci.yml.example .github/workflows/ci.yml
   git commit -m "ci: activar workflow de CI"
   git push
   ```

El contenido del workflow está listo para usarse tal cual (ruff + mypy + pytest en Python 3.11 y 3.12).

