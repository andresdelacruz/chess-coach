#!/usr/bin/env bash
# Compatibilidad: la instalación ahora es install.sh y las cuentas se configuran en el navegador.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" "$@"
