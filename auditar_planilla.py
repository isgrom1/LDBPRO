#!/usr/bin/env python3
"""Auditor automático de planillas de fauna XPE (LdB SEIA).

Uso:
    python auditar_planilla.py ruta/planilla.xlsx [--config config.yaml] [--solo-reporte]
"""

import sys

from auditor.cli import main

if __name__ == "__main__":
    sys.exit(main())
