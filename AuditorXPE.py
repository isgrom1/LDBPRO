"""Aplicación de escritorio del Auditor XPE (se empaqueta como AuditorXPE.exe).

    python AuditorXPE.py            # abre la interfaz en el navegador
    python AuditorXPE.py --autoprueba
"""

import sys

from auditor.app.lanzador import main

if __name__ == "__main__":
    sys.exit(main())
