#!/usr/bin/env bash
# ==============================================================================
# Script de Configuración del Entorno Virtual (.venv) para IA Cam IP Dahua
# ==============================================================================

set -e

echo "=== 1. Verificando versión de Python ==="
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 no está instalado o no se encuentra en el PATH."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python detectado: $PYTHON_VERSION"

echo "=== 2. Creando Entorno Virtual (.venv) ==="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Entorno virtual .venv creado con éxito."
else
    echo "El entorno virtual .venv ya existe."
fi

echo "=== 3. Actualizando pip dentro de .venv ==="
.venv/bin/python -m pip install --upgrade pip

echo "=== 4. Instalando dependencias desde requirements.txt ==="
.venv/bin/pip install -r requirements.txt

echo "=============================================================================="
echo "¡INSTALACIÓN COMPLETADA CON ÉXITO!"
echo ""
echo "Para activar el entorno virtual ejecuta:"
echo "    source .venv/bin/activate"
echo ""
echo "Para iniciar el sistema ejecuta:"
echo "    python main.py"
echo "=============================================================================="
