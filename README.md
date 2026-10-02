# AI Multi-Camera Vision & PTZ Tracker

Sistema integral de videovigilancia inteligente en Python con **Visión Artificial por Inteligencia Artificial (YOLOv8)**, diseñado para la detección, clasificación y seguimiento en tiempo real de personas, vehículos (autos, camionetas, buses, motos) y animales a través de **múltiples cámaras simultáneas** (cámaras IP Dahua, Hikvision, cámaras ONVIF genéricas o Webcams).

Incorpora una **Interfaz Gráfica Web interactiva (FastAPI + WebSocket)** para monitorear y configurar el sistema de forma 100% remota en servidores locales o en la nube, con soporte de control manual PTZ, **patrullaje panorámico 3D de 355°**, **calibración inicial secuencial de Punto Cero**, **prioridad dinámica de movimiento vs. quietud** y **modo silencioso de logs**.

---

## ⚡ Características Principales

- **Arquitectura Multi-Cámara Paralela (N Cámaras):**
  - Procesa múltiples cámaras concurrentemente sin cuellos de botella mediante hilos independientes.
  - Visualización en **Mosaico Dinámico (Grid)** con ajuste automático (1x1, 1x2, 2x2, 2x3, 3x3).
- **Interfaz Gráfica Web Responsiva (FastAPI + HTML5 + Tailwind CSS):**
  - Accesible desde cualquier navegador web (PC, laptop, tablet o smartphone).
  - Streaming de video en tiempo real vía MJPEG multipart con selección de cámara o mosaico general.
  - **D-Pad Virtual y control por flechas del teclado físico** con pausa automática de patrulla.
  - **Gestión completa de cámaras (CRUD):** Agregar, Editar credenciales/IPs y Eliminar cámaras desde la web.
  - Telemetría en vivo vía WebSockets (FPS, conteo de objetivos, estado del motor PTZ).
  - Editor dinámico de parámetros que guarda automáticamente en `config.yaml` sin reiniciar.
- **Inferencia IA Compartida Eficiente (YOLOv8 + ByteTrack):**
  - Una sola instancia de red neuronal en memoria RAM atiende todas las cámaras.
  - Soporte de aceleración por hardware: **Apple Silicon Metal (MPS)** en Mac, **CUDA** en NVIDIA y CPU optimizada.
- **Patrullaje Panorámico 3D de 355° con Inclinación Profunda:**
  - Barrido horizontal completo (355°) de extremo a extremo alternado con inclinación vertical hacia la calle y hacia el horizonte.
  - Señal de *Keep-Alive* (1.0s) para evitar que el firmware del fabricante corte la energía del motor antes de tiempo.
- **Calibración Inicial Secuencial (Punto Cero 355°):**
  - Calibra el domo de forma segura al encender ejecutando dos movimientos independientes: primero tope **Derecha**, amortiguación, y luego tope **Arriba**.
- **Filtro de Inmovilidad y Prioridad Dinámica (Personas Inmóviles vs. En Movimiento):**
  - Diferenciación instantánea por cálculo de vector de desplazamiento: si un objetivo queda inmóvil durante 3 segundos, se marca como `[ESTATICO]`.
  - Si aparece otro objetivo en movimiento, desengancha de inmediato para priorizar el movimiento.
  - Si no hay movimiento, **reanuda de inmediato el patrullaje en el mismo sentido que llevaba**, sin esperas muertas de 12 segundos.
- **Autodetección de Número de Serie (SN) como ID:**
  - Consulta en tiempo real el Número de Serie físico de fábrica del hardware (ONVIF `GetDeviceInformation` o Dahua CGI `getSerialNo`) para asignar identificadores únicos e inmutables automáticamente.
- **Ronda de Presets (Guard Tour) con Reordenamiento Drag & Drop:**
  - Patrullaje programable saltando entre puntos clave con tiempos de permanencia (`dwell_time_sec`) individuales.
  - Reordenamiento visual intuitivo de la secuencia mediante **Drag and Drop** nativo.
  - Sincronización y lectura de nombres reales nativos asignados en el firmware (ej: `Entrada`, `Patio`).
- **Optimización CPU: Frame Skipping Configurable (Ideal Servidores / Mini PC):**
  - Salto inteligente de cuadros de inferencia (1x, 2x, 3x, 4x) configurable individualmente por cámara.
  - Reduce el uso de CPU hasta en un **50% a 75%** manteniendo el lazo de seguimiento PTZ fluido y la visualización activa.
- **Selección de Objetos y Orden de Prioridad Jerárquico de Seguimiento (PTZ):**
  - Configuración modular por cámara de las clases a seguir (`person`, `car`, `motorcycle`, `dog`, etc.).
  - **Prioridad estricta**: Si conviven varios objetos a la vez, la cámara sigue al de mayor prioridad (ej. 1º Personas > 2º Autos > 3º Motos).
  - **Conmutación inteligente**: Si la cámara está siguiendo un vehículo y entra una persona al encuadre, conmuta inmediatamente la mira hacia la persona.
  - Reordenamiento interactivo en el panel web con botones de prioridad `[▲]` `[▼]` y casillas de activación.
- **Luz de Advertencia (Flash) Inteligente Modular (Luz Blanca Óptica 100% Silenciosa):**
  - **Espera de Seguimiento antes de Disparar (`trigger_delay_sec`):** La cámara sigue activamente al objetivo y, si lo mantiene en seguimiento durante el tiempo configurado (ej. 3 segundos), activa la luz de advertencia (flash).
  - **Disparo Único por Persona (`one_shot_per_target`):** El foco destella por la duración establecida (ej. 3 segundos), se apaga automáticamente y la cámara **continúa siguiendo al objetivo sin repetir el flash** de manera infinita.
  - **Filtro Modular de Objetivos:** Configuración para *Solo Personas*, *Personas y Vehículos*, *Personas, Vehículos y Mascotas* o *Cualquier Detección IA*.
  - **Condición de Visión Nocturna Automática:** Detección instantánea de infrarrojos en RAM por saturación cromática (`HSV`), activando el flash solo de noche o siempre según se configure.
  - **Garantía Silenciosa (Cero Sirenas):** Excluye completamente altavoces y alarmas acústicas, operando de forma 100% óptica.
  - **Feedback en Tiempo Real:** Cuenta regresiva visual en el HUD del video (`[FLASH EN 2.3s]`) y badge animado en la interfaz web.
  - **Compatibilidad Híbrida ONVIF / Dahua:** Soporta activación óptica directa tanto con driver Dahua CGI como a través del puente auxiliar ONVIF.
- **Auto-Zoom Dinámico (Encuadre Automático Inteligente):**
  - Ajusta el zoom óptico/digital automáticamente según la altura relativa del objetivo respecto al fotograma (`target_height_ratio_min` y `max`).
  - Si una persona se encuentra lejana o pequeña (< 20% del alto), la cámara hace **Zoom In** para capturar detalles faciales; si se acerca demasiado (> 50%), hace **Zoom Out** para evitar perder el seguimiento.
  - Totalmente parametrizable y activable/desactivable de forma independiente por cámara.
- **Zonas de Interés y Geocercas (Polígonos Dinámicos en la Web):**
  - Delimitación de polígonos de detección directamente desde el navegador web mediante un editor visual interactivo con coordenadas normalizadas.
  - Soporta modos de **Inclusión** (solo detecta y sigue dentro del polígono) y **Exclusión** (ignora calles públicas, veredas transitadas o árboles).
  - Dibujado visual de la geocerca en el HUD del stream de video y configurable por cámara.
- **Grabación Automática de Clip Corto de Evidencia (MP4 en RAM) en Alertas:**
  - Búfer circular en memoria RAM continua (`pre_alarm_sec`, ej: 5s antes del evento) + grabación en tiempo real posterior al disparo (`post_alarm_sec`, ej: 10s).
  - Al detectar un objetivo configurado o ingresar a la geocerca, genera un archivo MP4 con metadatos JSON detallados.
  - Reproductor y descargador de evidencias integrado en la pestaña web **Evidencia (MP4)**, con codificación de alta compatibilidad (H.264 / MP4V) sin degradar SSDs en operación normal.
- **Suavizado de Movimiento PTZ (Control Proporcional Amortiguado / Filtro EMA):**
  - Filtro de Media Móvil Exponencial (EMA) sobre las velocidades Pan, Tilt y Zoom para eliminar sacudidas mecánicas y tirones bruscos.
  - Coeficiente de suavizado configurable (`alpha`: 0.1 a 0.9) de forma independiente por cámara para adaptarse a la inercia de cualquier domo.
- **Seguridad del Panel Web (Autenticación y Control de Acceso):**
  - Protección perimetral para el panel de control y la API REST mediante autenticación HTTP Digest / Cookies de sesión cifradas.
  - Activación opcional (`auth_enabled: false` por defecto para retrocompatibilidad) con cambio de usuario y contraseña desde la configuración.
- **Operación 100% en Memoria RAM (Cero Almacenamiento en Disco):**
  - El sistema no escribe archivos de video temporales ni degrada discos SSD. La grabación continua queda a cargo del DVR / NVR existente y la evidencia de alertas se captura bajo demanda.
- **Control de Logs en Consola (Modo Silencioso):**
  - Opción de silenciar logs normales para entornos de producción o servidores headless.

---

## 📋 Requisitos del Sistema

- **Python 3.9 o superior** (probado y optimizado en Python 3.14).
- Conexión de red local hacia las cámaras IP (RTSP / ONVIF).
- Dependencias principales: `ultralytics`, `opencv-python`, `fastapi`, `uvicorn[standard]`, `onvif-zeep`, `pyyaml`, `jinja2`, `python-multipart`.

---

## 🚀 Instalación y Entorno Virtual (`.venv`)

Se recomienda ejecutar siempre el proyecto dentro de un entorno virtual aislado para evitar conflictos con paquetes del sistema.

### Opción 1: Instalación Automática (macOS / Linux)

El repositorio incluye un script automatizado que valida Python, crea el entorno `.venv`, actualiza `pip` e instala todas las dependencias de `requirements.txt`:

```bash
# 1. Dar permisos de ejecución al script
chmod +x setup_env.sh

# 2. Ejecutar instalador
./setup_env.sh
```

---

### Opción 2: Instalación Manual Paso a Paso (macOS / Linux / Windows)

Si prefieres realizar el proceso manualmente o estás en Windows:

#### 1. Crear el Entorno Virtual (`.venv`):
```bash
# En macOS / Linux:
python3 -m venv .venv

# En Windows:
python -m venv .venv
```

#### 2. Activar el Entorno Virtual:
```bash
# En macOS / Linux (bash/zsh):
source .venv/bin/activate

# En Windows (PowerShell):
.venv\Scripts\Activate.ps1

# En Windows (CMD):
.venv\Scripts\activate.bat
```
*(Al activarse, verás el prefijo `(.venv)` a la izquierda en tu terminal).*

#### 3. Actualizar `pip` e instalar dependencias:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> [!TIP]
> Si agregas o actualizas dependencias en el futuro dentro de `.venv`, puedes reinstalar o sincronizar en cualquier momento ejecutando:
> ```bash
> pip install -r requirements.txt
> ```

#### 4. (Opcional) Ejecutar directamente con el binario de `.venv` sin necesidad de activar:
```bash
# En macOS / Linux:
.venv/bin/python main.py --web

# En Windows:
.venv\Scripts\python.exe main.py --web
```

---

## 🌐 Modos de Ejecución

El archivo [`main.py`](file:///Users/TU_USUARIO/Documents/GitHub/AI-Multi-Camera-Vision-PTZ-Tracker/main.py) incluye múltiples modos según el escenario de despliegue:

### 1. Modo Servidor Web Completo (Recomendado para Servidores)
Inicia el backend FastAPI y permite monitorear y controlar desde el navegador:
```bash
python main.py --web
```
> Abre tu navegador en: **`http://localhost:8000`** (o `http://<IP-DE-TU-SERVIDOR>:8000`).

### 2. Modo Servidor Headless y Silencioso (Servicios en Segundo Plano / Docker)
Ideal para servidores sin monitor físico ni entorno de escritorio:
```bash
python main.py --web --headless -q
```
*(Sin ventana gráfica local y omitiendo logs informativos en consola).*

### 3. Modo Solo Ventana de Escritorio (Clásico OpenCV)
```bash
python main.py
```

### 4. Prueba Rápida con Webcam Local
```bash
python main.py --source 0
```

### Argumentos de Línea de Comandos Disponibles:
| Argumento | Descripción |
| :--- | :--- |
| `-c`, `--config` | Ruta al archivo de configuración (por defecto: `config.yaml`). |
| `--web` | Inicia la Interfaz Gráfica Web (FastAPI + Uvicorn). |
| `--headless` | Desactiva la ventana gráfica local de OpenCV (modo segundo plano). |
| `-q`, `--quiet` | **Modo silencioso**: Oprime logs en consola (sólo muestra advertencias y errores). |
| `--host` | Dirección IP de enlace del servidor web (por defecto: `0.0.0.0`). |
| `--port` | Puerto de escucha del servidor web (por defecto: `8000`). |
| `--source` | Ejecuta una sola cámara rápida pasando índice (ej: `0`) o URL RTSP. |

---

## 🖥️ Interfaz Gráfica Web

La interfaz web incluye dos paneles principales:

### Pestaña 1: Monitoreo y Control en Vivo
- **Visualizador de Video:** Selector de vista (Mosaico de todas las cámaras o cámara individual) con botón de pantalla completa.
- **Módulos Independientes por Cámara (Switches Rápidos):**
  - **`Seguimiento IA` [ON / OFF]:** Activa o apaga el autotracking motorizado de personas/vehículos en esa cámara.
  - **`Patrullaje` [ON / OFF]:** Activa o apaga el patrullaje de búsqueda en reposo para esa cámara.
- **D-Pad Virtual y Teclado:** Botones interactivos táctiles y control por teclado (◄ ▲ ▼ ►).
- **Presets Rápidos (P1, P2, P3, P4):**
  - **Click simple:** Viaja inmediatamente al Preset indicado.
  - **Doble click:** Guarda la posición actual de la cámara en ese Preset físico.
- **Posicionamiento Nativo (Presets):**
  - **`Calibrar Punto Cero / Home`:** Dispara el retorno a Home o la calibración inicial según el modo configurado.
  - **Telemetría de Coordenadas Nativas:** Muestra los grados Pan/Tilt en tiempo real si el hardware los reporta.
- **Detecciones en Tiempo Real:** Lista dinámica que muestra las detecciones activas con su clase, confianza, velocidad de movimiento y estado (`[TRACKING]` o `[ESTATICO]`).

### Pestaña 2: Configuración del Sistema
- **Motor YOLO:** Selección de modelo (`yolov8n.pt`, `yolov8s.pt`), umbral de confianza y activación/desactivación de clases de interés.
- **Omitir logs en consola:** Casilla para activar o desactivar el modo silencioso de logs en caliente.
- **Seguridad y Acceso al Panel:** Configuración de activación de autenticación, usuario y contraseña de acceso.
- **Gestión de Cámaras y Parámetros Independientes (CRUD):**
  - **`+ Agregar Cámara`** / **`Editar`**: Ajusta de forma 100% independiente para cada cámara:
    - **Estrategia de Patrullaje:** Barrido Panorámico (355°) o Ronda de Presets (Guard Tour) con permanencia por parada.
    - **Prioridad de Seguimiento:** Lista interactiva y jerárquica de clases con botones `[▲]` `[▼]`.
    - **Luz de Advertencia (Flash):** Foco blanco silencioso con retardo de disparo y destello único por persona.
    - **Auto-Zoom Dinámico:** Encuadre automático por ratio de altura (`ratio_min`, `ratio_max`, `zoom_speed`).
    - **Suavizado PTZ:** Filtro de inercia EMA (`alpha`).
    - **Geocercas y Zonas de Interés:** Activación, modo de inclusión/exclusión y botón de dibujo de polígonos sobre el stream en vivo.
    - **Grabación de Evidencias:** Activación, segundos pre-alarma en RAM y segundos post-alarma grabados a disco.
  - **`Eliminar`**: Remueve cámaras del sistema.

### Pestaña 3: Galería de Evidencias (MP4)
- **Historial de Clips de Seguridad:** Visualización de clips MP4 grabados automáticamente al activarse las alertas de detección o intrusión en geocercas.
- **Reproductor de Video Integrado:** Modal interactivo HTML5 para reproducir las grabaciones directamente desde el navegador web.
- **Metadatos y Descargas:** Cada clip incluye información de cámara, fecha/hora, clase detectada, y opciones para **Descargar MP4** o **Eliminar** archivo.

---

## ⚙️ Estructura de Configuración (`config.yaml`)

```yaml
# 1. Seguridad del Panel Web y API REST
security:
  auth_enabled: false        # true para exigir login web/API, false para acceso abierto
  admin_username: "admin"
  admin_password: "admin_password"

# 2. Cámaras Monitoreadas (Configuración 100% Modular e Independiente por Cámara)
cameras:
  - id: "cam_ptz"
    name: "Cámara PTZ Principal"
    type: "rtsp"
    camera_ip: "127.0.0.1"
    rtsp_port: 57858
    username: "admin"
    password: "tu_password"
    channel: 1
    subtype: 1 # 1 = Sub-stream (óptimo para IA), 0 = Main-stream
    frame_skip: 2 # 1 = cada frame, 2 = 1 de cada 2 frames (Ahorro 50% CPU en Mini PC)
    reconnect_interval_sec: 5

    # Zonas de Interés y Geocercas (Polígono Normalizado 0.0 - 1.0)
    geofence:
      enabled: false
      mode: "inclusion"              # "inclusion" (solo dentro) o "exclusion" (ignora dentro)
      points:                        # Coordenadas del polígono trazado en la web
        - {x: 0.10, y: 0.20}
        - {x: 0.90, y: 0.20}
        - {x: 0.90, y: 0.85}
        - {x: 0.10, y: 0.85}

    # Grabación Automática de Clip Corto de Evidencia (MP4 en RAM)
    event_recording:
      enabled: false                 # Grabar clips cortos solo en eventos
      pre_alarm_sec: 5.0             # Segundos previos al evento almacenados en RAM continua
      post_alarm_sec: 10.0           # Segundos de grabación tras activarse el evento
      cooldown_sec: 30.0             # Enfriamiento para evitar ráfagas duplicadas
      trigger_classes:
        - "person"

    # Luz de Advertencia (Flash) Inteligente Modular
    spotlight:
      enabled: true                  # Habilitar flash óptico al detectar
      night_only: true               # Solo en modo noche IR (false = siempre)
      trigger_classes:               # Clases a advertir
        - "person"
      trigger_delay_sec: 3.0         # Segundos de seguimiento continuo antes de disparar el flash
      flash_duration_sec: 3.0        # Duración del destello encendido
      one_shot_per_target: true      # Un solo flash por objetivo mientras se le sigue
      cooldown_sec: 8.0              # Tiempo de enfriamiento

    # Orden de Prioridad Jerárquico de Seguimiento
    tracking_priority:
      - "person"                     # Prioridad 1: Personas
      - "car"                        # Prioridad 2: Autos
      - "motorcycle"                 # Prioridad 3: Motos
      - "dog"                        # Prioridad 4: Perros

    ptz:
      enabled: "auto"        # "auto" detecta motores PTZ; false para cámara fija
      driver: "onvif"        # "onvif" o "dahua_cgi"
      onvif_port: 57858
      positioning_mode: "native_preset" # "native_preset" o "time_based"
      home_preset: "1"       # Identificador de Preset Home en la cámara
      tracking_enabled: true # Habilitar seguimiento motorizado IA para esta cámara
      patrol_enabled: true   # Habilitar patrullaje de búsqueda en reposo
      patrol_type: "preset_tour" # "panoramic" (Barrido 355°) o "preset_tour" (Ronda de Presets)
      preset_tour:           # Lista de paradas con tiempos de vigilancia específicos:
        - preset: "1"
          dwell_time_sec: 10.0 # Segundos de vigilancia quieta en Preset 1
        - preset: "2"
          dwell_time_sec: 6.0  # Segundos en Preset 2
        - preset: "3"
          dwell_time_sec: 12.0 # Segundos en Preset 3
      initial_homing: true   # Calibración o retorno a Home al iniciar
      homing_pan_duration_sec: 14.0  # Segundos para tope derecho (si usa modo time_based)
      homing_tilt_duration_sec: 2.5  # Segundos para tope arriba (si usa modo time_based)
      patrol_interval_sec: 12        # Espera sin eventos antes de salir a patrullar
      patrol_speed: 0.35             # Velocidad horizontal
      panoramic_patrol: true         # Patrulla horizontal + inclinación vertical
      sweep_pan_time_sec: 14.0       # Tiempo para recorrer los 355° de extremo a extremo
      tilt_transition_time_sec: 3.0  # Tiempo de inclinación vertical hacia la calle
      patrol_tilt_speed: 0.35        # Velocidad de inclinación vertical
      tracking_speed: 0.65           # Velocidad de seguimiento ágil
      deadzone_x: 0.08               # Zona muerta de disparo horizontal
      deadzone_y: 0.08               # Zona muerta de disparo vertical
      static_target_timeout_sec: 3.0 # Segundos inmóvil antes de continuar patrullando
      manual_override_pause_sec: 1.0 # Pausa tras mover con controles web

      # Auto-Zoom Dinámico (Encuadre Automático)
      auto_zoom:
        enabled: false
        target_height_ratio_min: 0.20 # Si altura < 20% del frame -> Zoom In
        target_height_ratio_max: 0.50 # Si altura > 50% del frame -> Zoom Out
        zoom_speed: 0.35              # Velocidad óptica de zoom

      # Suavizado de Movimiento PTZ (Control Amortiguado EMA)
      smoothing:
        enabled: true
        alpha: 0.30                   # Coeficiente EMA (0.1 suave, 1.0 instantáneo)

# 3. Política de Almacenamiento
storage:
  save_video: false
  save_snapshots: false
  events_dir: "recordings/events"

# 4. Motor IA (YOLO)
detection:
  model_name: "yolov8n.pt"
  target_classes:
    - "person"
    - "dog"
    - "car"
    - "truck"
    - "bus"
    - "motorcycle"
  confidence: 0.30
  device: "auto"             # "auto", "mps" (Mac), "cuda" o "cpu"

# 5. Visualización en Pantalla Local
display:
  show_window: true
  window_title: "AI Multi-Camera Vision & PTZ Tracker"
  cell_width: 640
  cell_height: 360
  draw_hud: true

# 6. Configuración de Logs en Consola
logging:
  quiet: false               # true para suprimir logs en consola (sólo errores)
  level: "INFO"              # "DEBUG", "INFO", "WARNING", "ERROR"
```

---

## ⌨️ Controles en Pantalla (Modo Escritorio OpenCV)

- **`q`**: Cerrar el sistema ordenadamente.
- **`p`**: Alternar / pausar patrullaje manual en las cámaras PTZ.
- **`1` a `9`**: Activar / desactivar PTZ Digital (Smart Framing) en la cámara correspondiente.

---

## 🔒 Seguridad y Privacidad

- El sistema no almacena secuencias de video en disco local (100% en RAM).
- Las contraseñas en URLs RTSP se enmascaran automáticamente en los logs (`rtsp://admin:****@ip:puerto`).

---

## 📄 Licencia

Este proyecto está licenciado bajo la **GNU Affero General Public License v3.0 (AGPL-3.0)**. Consulta el archivo [LICENSE](LICENSE) para más detalles.

El modelo de visión por computadora utiliza Ultralytics YOLOv8 bajo sus términos correspondientes de código abierto.
