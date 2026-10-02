"""
Servidor Web FastAPI para Monitoreo en Tiempo Real y Configuración Remota.
Streaming de video 100% en memoria RAM (cero almacenamiento en disco).
"""

import os
import time
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml
import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
import json
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logger = logging.getLogger("WebServer")

# Modelos Pydantic para APIs
class PTZMoveRequest(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0

class SpotlightControlRequest(BaseModel):
    state: Optional[bool] = None
    test: bool = False
    duration_sec: float = 5.0

class ConfigUpdateRequest(BaseModel):
    config: Dict[str, Any]

class LoginRequest(BaseModel):
    username: str
    password: str

class GeofenceUpdateRequest(BaseModel):
    enabled: bool = False
    mode: str = "inclusion"
    points: List[Dict[str, float]] = []


class WebManager:
    """Contenedor de estado compartido entre el pipeline de visión y FastAPI."""
    def __init__(self):
        self.camera_nodes: List[Any] = []
        self.grid_renderer: Optional[Any] = None
        self.detector: Optional[Any] = None
        self.config_path: str = "config.yaml"
        self.config_data: Dict[str, Any] = {}
        self.active_websockets: List[WebSocket] = []

    def get_node(self, cam_id: str):
        for node in self.camera_nodes:
            if node.cam_id == cam_id:
                return node
        return None


web_manager = WebManager()

app = FastAPI(
    title="AI Multi-Camera Vision & PTZ Tracker API",
    description="Interfaz y API de monitoreo y control remoto en tiempo real",
    version="2.0.0"
)

# Configuración de carpetas estáticas y templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Middleware para seguridad del panel web (si está habilitado en config)
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Obtener configuración de seguridad
    sec_cfg = web_manager.config_data.get("security", {})
    auth_enabled = bool(sec_cfg.get("auth_enabled", False))

    if auth_enabled:
        # Rutas exentas de autenticación (archivos estáticos, endpoint de login y favicon)
        path = request.url.path
        if not (path.startswith("/static") or path == "/api/login" or path == "/favicon.ico"):
            auth_token = request.cookies.get("session_auth")
            # El token válido es la clave secreta o token firmado
            valid_user = sec_cfg.get("admin_username", "admin")
            valid_pass = sec_cfg.get("admin_password", "admin123")
            import hashlib
            expected_token = hashlib.sha256(f"{valid_user}:{valid_pass}".encode()).hexdigest()

            if auth_token != expected_token:
                # Si es una petición a la API REST, retornar 401 Unauthorized
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"detail": "No autenticado. Inicie sesión."})
                # Si es una página web, renderizar vista o permitir index con bandera auth_required
                pass

    response = await call_next(request)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página principal del Dashboard de Monitoreo."""
    sec_cfg = web_manager.config_data.get("security", {})
    auth_enabled = bool(sec_cfg.get("auth_enabled", False))
    valid_user = sec_cfg.get("admin_username", "admin")
    valid_pass = sec_cfg.get("admin_password", "admin123")
    import hashlib
    expected_token = hashlib.sha256(f"{valid_user}:{valid_pass}".encode()).hexdigest()
    is_authenticated = (not auth_enabled) or (request.cookies.get("session_auth") == expected_token)

    cams = [
        {
            "id": node.cam_id,
            "name": node.name,
            "has_ptz": node.ptz_manager.has_hardware_ptz()
        }
        for node in web_manager.camera_nodes
    ]
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cameras": cams,
            "total_cameras": len(cams),
            "auth_enabled": auth_enabled,
            "is_authenticated": is_authenticated
        }
    )


# -------------------------------------------------------------------------
# STREAMING MJPEG EN MEMORIA RAM (CERO DISCO)
# -------------------------------------------------------------------------

async def generate_mjpeg_stream(get_frame_func, fps_limit: int = 25):
    """Generador asíncrono para streaming MJPEG continuo sin tocar el disco."""
    interval = 1.0 / max(1, fps_limit)
    while True:
        frame_bytes = get_frame_func()
        if frame_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
        await asyncio.sleep(interval)


@app.get("/api/stream/grid")
async def stream_grid():
    """Stream MJPEG del mosaico general de todas las cámaras."""
    def get_grid_bytes():
        if not web_manager.grid_renderer or not web_manager.camera_nodes:
            return b""
        display_frames = [node.get_display_frame() for node in web_manager.camera_nodes]
        canvas = web_manager.grid_renderer.compose_grid(display_frames)
        success, buffer = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return buffer.tobytes() if success else b""

    return StreamingResponse(
        generate_mjpeg_stream(get_grid_bytes, fps_limit=25),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/stream/{cam_id}")
async def stream_camera(cam_id: str):
    """Stream MJPEG en vivo de una cámara individual."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Cámara '{cam_id}' no encontrada")

    return StreamingResponse(
        generate_mjpeg_stream(lambda: node.get_jpeg_frame(quality=80), fps_limit=25),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# -------------------------------------------------------------------------
# APIS REST DE ESTADO Y TELEMETRÍA
# -------------------------------------------------------------------------

@app.get("/api/status")
async def get_system_status():
    """Retorna el estado de todas las cámaras, detecciones y PTZ."""
    cameras_telemetry = [node.get_telemetry() for node in web_manager.camera_nodes]
    total_detections = sum(c["detections_count"] for c in cameras_telemetry)
    return {
        "status": "online",
        "timestamp": time.time(),
        "total_cameras": len(web_manager.camera_nodes),
        "total_detections": total_detections,
        "cameras": cameras_telemetry
    }


# -------------------------------------------------------------------------
# APIS REST DE CONTROL PTZ
# -------------------------------------------------------------------------

@app.post("/api/ptz/{cam_id}/move")
async def ptz_move(cam_id: str, req: PTZMoveRequest):
    """Mueve manualmente la cámara PTZ y activa la pausa temporal de patrullaje."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ motorizado")

    success = node.ptz_manager.manual_move(req.pan, req.tilt, req.zoom)
    return {
        "success": success,
        "cam_id": cam_id,
        "status": node.ptz_manager.status,
        "pause_sec": node.ptz_manager.manual_override_pause_sec
    }


@app.post("/api/ptz/{cam_id}/stop")
async def ptz_stop(cam_id: str):
    """Detiene los motores PTZ y establece la pausa antes de reanudar patrullaje."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    success = node.ptz_manager.manual_stop()
    return {"success": success, "cam_id": cam_id, "status": node.ptz_manager.status}


@app.post("/api/ptz/{cam_id}/patrol")
async def ptz_toggle_patrol(cam_id: str):
    """Alterna el modo patrulla de la cámara."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    node.ptz_manager.toggle_patrol()
    return {
        "success": True,
        "is_patrolling": node.ptz_manager.is_patrolling,
        "status": node.ptz_manager.status
    }


@app.post("/api/ptz/{cam_id}/calibrate")
async def ptz_calibrate(cam_id: str):
    """Dispara la secuencia de calibración inicial de Punto Cero 355° o Homing."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con PTZ físico para calibrar")

    success = node.ptz_manager.trigger_homing()
    return {
        "success": success,
        "message": "Calibración inicial / Retorno a Home iniciado."
    }


@app.post("/api/ptz/{cam_id}/tracking")
async def ptz_toggle_tracking(cam_id: str):
    """Alterna modularmente el modo de seguimiento IA de la cámara."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    new_state = node.ptz_manager.toggle_tracking()
    return {
        "success": True,
        "cam_id": cam_id,
        "tracking_enabled": new_state,
        "status": node.ptz_manager.status
    }


@app.post("/api/ptz/{cam_id}/patrol_mode")
async def ptz_toggle_patrol_mode(cam_id: str):
    """Alterna modularmente el modo de patrullaje de búsqueda de la cámara."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    new_state = node.ptz_manager.toggle_patrol_enabled()
    return {
        "success": True,
        "cam_id": cam_id,
        "patrol_enabled": new_state,
        "status": node.ptz_manager.status
    }


@app.post("/api/ptz/{cam_id}/set_home")
async def ptz_set_home(cam_id: str):
    """Guarda la posición actual de la cámara como Home (Preset 1) en el hardware nativo."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ")

    success = node.ptz_manager.save_current_as_home()
    return {
        "success": success,
        "cam_id": cam_id,
        "message": f"Posición actual guardada como Home (Preset {node.ptz_manager.home_preset}) en memoria física." if success else "La cámara no aceptó el comando SetPreset."
    }


@app.post("/api/ptz/{cam_id}/goto_home")
async def ptz_goto_home(cam_id: str):
    """Ordena a la cámara regresar inmediatamente a su posición Home."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ")

    success = node.ptz_manager.go_to_home()
    return {
        "success": success,
        "cam_id": cam_id,
        "message": "Comando Ir a Home ejecutado."
    }


@app.post("/api/ptz/{cam_id}/set_preset")
async def ptz_save_preset(cam_id: str, preset_id: str = "1", name: str = ""):
    """Guarda la posición actual de la cámara como un Preset numérico arbitrario."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ")

    success = node.ptz_manager.save_preset(preset_id, name)
    return {
        "success": success,
        "cam_id": cam_id,
        "preset_id": preset_id,
        "message": f"Posición actual fijada en Preset {preset_id}." if success else f"Error al guardar Preset {preset_id} en hardware."
    }


@app.post("/api/ptz/{cam_id}/goto_preset")
async def ptz_goto_preset(cam_id: str, preset_id: str = "1"):
    """Ordena a la cámara moverse al Preset numérico indicado."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ")

    success = node.ptz_manager.goto_preset_num(preset_id)
    return {
        "success": success,
        "cam_id": cam_id,
        "preset_id": preset_id,
        "message": f"Moviendo a Preset {preset_id}..."
    }


@app.post("/api/ptz/{cam_id}/spotlight")
async def ptz_control_spotlight(cam_id: str, req: SpotlightControlRequest):
    """
    Controla el foco de luz blanca / flash óptico de la cámara (Cero audio/sirena).
    Permite encender, apagar o ejecutar una prueba temporal de X segundos.
    """
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        raise HTTPException(status_code=400, detail="La cámara no cuenta con hardware PTZ/CGI configurable")

    if req.test:
        success = node.spotlight.trigger_test(req.duration_sec, node.ptz_manager)
        return {
            "success": success,
            "cam_id": cam_id,
            "active": True,
            "message": f"Prueba de flash óptico iniciada ({req.duration_sec}s)."
        }

    target_state = bool(req.state)
    success = node.ptz_manager.set_white_light(target_state)
    node.spotlight.is_flashing = target_state
    return {
        "success": success,
        "cam_id": cam_id,
        "active": target_state,
        "message": f"Foco de luz blanca {'ENCENDIDO' if target_state else 'APAGADO'}."
    }




@app.get("/api/ptz/{cam_id}/presets")
async def ptz_list_presets(cam_id: str):
    """Consulta y retorna los presets reales guardados en la memoria física de la cámara."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    if not node.ptz_manager.has_hardware_ptz():
        return {"success": False, "presets": [], "message": "Sin hardware PTZ"}

    presets = node.ptz_manager.get_camera_presets()
    return {
        "success": True,
        "cam_id": cam_id,
        "total": len(presets),
        "presets": presets
    }


@app.post("/api/camera/detect_sn")
async def detect_camera_serial_number(req: Request):
    """
    Se conecta a la IP indicada y lee el Número de Serie (SN) físico real de la cámara.
    Sirve para autogenerar o asignar el ID único basado en el SN de fábrica.
    """
    try:
        body = await req.json()
        ip = body.get("camera_ip", "").strip()
        port = int(body.get("port", 80))
        user = body.get("username", "admin").strip()
        password = body.get("password", "")
        driver = body.get("driver", "onvif").strip().lower()
        channel = int(body.get("channel", 1))

        if not ip:
            raise HTTPException(status_code=400, detail="Se requiere la dirección IP de la cámara.")

        sn = None
        # Probar primero con el driver indicado
        if driver == "onvif":
            from core.ptz.onvif_controller import OnvifPTZController
            ctrl = OnvifPTZController(ip, port=port, username=user, password=password)
            if ctrl.is_available():
                sn = ctrl.get_serial_number()
            ctrl.close()

        # Si no respondió o el driver es dahua_cgi
        if not sn:
            from core.ptz.dahua_cgi_controller import DahuaCgiPTZController
            ctrl_cgi = DahuaCgiPTZController(ip, port=port, username=user, password=password, channel=channel)
            if ctrl_cgi.is_available():
                sn = ctrl_cgi.get_serial_number()
            ctrl_cgi.close()

        if sn:
            return {"success": True, "serial_number": sn, "message": f"Número de serie detectado: {sn}"}
        else:
            # Fallback limpio si la cámara no reporta el campo SN por ONVIF/CGI
            ip_suffix = ip.replace(".", "_")
            fallback_sn = f"CAM_{ip_suffix}"
            return {"success": False, "fallback_id": fallback_sn, "message": "La cámara no expuso su SN de fábrica. Se sugiere ID basado en IP."}

    except Exception as e:
        logger.error(f"Error detectando SN de cámara: {e}")
        return {"success": False, "message": f"Error conectando a la cámara: {e}"}



@app.get("/api/ptz/{cam_id}/status")
async def ptz_get_detailed_status(cam_id: str):

    """Retorna información detallada de telemetría y coordenadas de la cámara."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    return node.ptz_manager.get_state_dict()



# -------------------------------------------------------------------------
# APIS REST DE CONFIGURACIÓN DINÁMICA
# -------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    """Lee y retorna la configuración actual desde el archivo YAML."""
    try:
        with open(web_manager.config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer configuración: {e}")


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """Actualiza y guarda la configuración en config.yaml, aplicando cambios dinámicos."""
    try:
        new_cfg = req.config

        # Determinar conjunto completo de clases requeridas sumando cámaras y config global
        det_cfg = new_cfg.get("detection", {})
        cams_cfg = new_cfg.get("cameras") or []
        all_classes = set(det_cfg.get("target_classes") or ["person", "car", "motorcycle", "truck", "bus", "dog", "cat"])
        for c in cams_cfg:
            if isinstance(c, dict):
                pri = c.get("tracking_priority") or (c.get("ptz", {}).get("tracking_priority") if isinstance(c.get("ptz"), dict) else None)
                if pri and isinstance(pri, list):
                    all_classes.update(pri)
                spot = c.get("spotlight", {})
                if isinstance(spot, dict):
                    trig = spot.get("trigger_classes")
                    if trig and isinstance(trig, list):
                        all_classes.update([t for t in trig if t not in ("all", "vehicles", "animals")])

        if "detection" not in new_cfg:
            new_cfg["detection"] = {}
        new_cfg["detection"]["target_classes"] = list(all_classes)

        with open(web_manager.config_path, "w", encoding="utf-8") as f:
            yaml.dump(new_cfg, f, allow_unicode=True, sort_keys=False)

        web_manager.config_data = new_cfg

        # Actualizar en caliente los parámetros aplicables al detector en ejecución
        if web_manager.detector:
            if det_cfg:
                web_manager.detector.confidence = float(det_cfg.get("confidence", 0.30))
            web_manager.detector.update_target_classes(list(all_classes))

        # Actualizar lista de cámaras dinámicamente en caliente
        cams_cfg = new_cfg.get("cameras") or []
        new_cams_ids = {str(c.get("id")) for c in cams_cfg if c.get("id")}

        # 1. Detener y remover nodos eliminados
        nodes_to_keep = []
        for node in web_manager.camera_nodes:
            if node.cam_id not in new_cams_ids:
                try:
                    node.stop()
                    logger.info(f"Cámara '{node.name}' ({node.cam_id}) detenida y removida.")
                except Exception as ex:
                    logger.error(f"Error al detener cámara {node.cam_id}: {ex}")
            else:
                nodes_to_keep.append(node)
        web_manager.camera_nodes.clear()
        web_manager.camera_nodes.extend(nodes_to_keep)

        # 2. Agregar nuevas cámaras o actualizar existentes
        for c_cfg in cams_cfg:
            cid = str(c_cfg.get("id"))
            node = web_manager.get_node(cid)
            if not node:
                # Cámara nueva agregada desde la interfaz web
                try:
                    from core.camera_node import CameraNode
                    det_cfg = new_cfg.get("detection", {})
                    new_node = CameraNode(c_cfg, det_cfg)
                    new_node.start()
                    web_manager.camera_nodes.append(new_node)
                    logger.info(f"Nueva cámara '{new_node.name}' ({cid}) iniciada en caliente desde la Web.")
                    node = new_node
                except Exception as ex:
                    logger.error(f"Error al iniciar nueva cámara {cid}: {ex}")
                    continue

            if node:
                if "name" in c_cfg:
                    node.name = str(c_cfg["name"])

                # Reiniciar stream si cambió la IP, credenciales, puerto o canal RTSP
                new_src = node._resolve_source(c_cfg)
                if new_src != node.video_source:
                    try:
                        logger.info(f"Actualizando fuente de video en caliente para '{node.name}'...")
                        node.stream.stop()
                        node.video_source = new_src
                        from core.stream import VideoStream
                        node.stream = VideoStream(node.video_source, reconnect_interval=node.reconnect_sec)
                        node.stream.start()
                    except Exception as ex:
                        logger.error(f"Error al reconectar stream en caliente para '{node.name}': {ex}")

                # Reiniciar controlador PTZ si cambiaron las credenciales o el driver/puerto
                ptz_c = c_cfg.get("ptz", {})
                new_auth = {
                    "camera_ip": c_cfg.get("camera_ip", "192.168.1.108"),
                    "username": c_cfg.get("username", "admin"),
                    "password": c_cfg.get("password", ""),
                    "channel": c_cfg.get("channel", 1)
                }
                new_driver = ptz_c.get("driver", "onvif")
                new_port = ptz_c.get("onvif_port", 80)
                old_port = node.ptz_manager.config.get("onvif_port", 80)
                if (new_auth != getattr(node.ptz_manager, "auth", {}) or
                    new_driver != getattr(node.ptz_manager, "driver_type", "onvif") or
                    new_port != old_port):
                    try:
                        logger.info(f"Actualizando conexión PTZ en caliente para '{node.name}'...")
                        node.ptz_manager.stop()
                        from core.ptz_manager import PTZManager
                        node.ptz_manager = PTZManager(ptz_c, new_auth)
                    except Exception as ex:
                        logger.error(f"Error al reconectar PTZ en caliente para '{node.name}': {ex}")

                if "frame_skip" in c_cfg:
                    node.frame_skip = max(1, int(c_cfg["frame_skip"]))
                if "ptz" in c_cfg:
                    ptz_c = c_cfg["ptz"]
                    node.ptz_manager.patrol_speed = float(ptz_c.get("patrol_speed", node.ptz_manager.patrol_speed))
                    node.ptz_manager.sweep_pan_time = float(ptz_c.get("sweep_pan_time_sec", node.ptz_manager.sweep_pan_time))
                    node.ptz_manager.tilt_transition_time = float(ptz_c.get("tilt_transition_time_sec", node.ptz_manager.tilt_transition_time))
                    node.ptz_manager.tilt_speed = float(ptz_c.get("patrol_tilt_speed", node.ptz_manager.tilt_speed))
                    node.ptz_manager.static_target_timeout = float(ptz_c.get("static_target_timeout_sec", node.ptz_manager.static_target_timeout))
                    node.ptz_manager.manual_override_pause_sec = float(ptz_c.get("manual_override_pause_sec", node.ptz_manager.manual_override_pause_sec))
                    if "patrol_enabled" in ptz_c:
                        node.ptz_manager.patrol_enabled = bool(ptz_c["patrol_enabled"])
                    if "tracking_enabled" in ptz_c:
                        node.ptz_manager.tracking_enabled = bool(ptz_c["tracking_enabled"])
                    if "positioning_mode" in ptz_c:
                        node.ptz_manager.positioning_mode = str(ptz_c["positioning_mode"]).lower()
                    if "home_preset" in ptz_c:
                        node.ptz_manager.home_preset = str(ptz_c["home_preset"])
                    if "patrol_type" in ptz_c:
                        node.ptz_manager.patrol_type = str(ptz_c["patrol_type"]).lower()
                    if "preset_tour" in ptz_c:
                        node.ptz_manager.preset_tour = list(ptz_c["preset_tour"])
                        node.ptz_manager.tour_current_idx = 0
                        node.ptz_manager.tour_substate = "MOVING"
                        node.ptz_manager.tour_cmd_sent_time = 0.0

                # Actualizar orden de prioridad de seguimiento de objetivos
                if "tracking_priority" in c_cfg:
                    node.tracking_priority = list(c_cfg["tracking_priority"])
                elif "ptz" in c_cfg and "tracking_priority" in c_cfg["ptz"]:
                    node.tracking_priority = list(c_cfg["ptz"]["tracking_priority"])

                # Actualizar parámetros de Disuasión Óptica / Flash en caliente
                if "spotlight" in c_cfg:
                    sc = c_cfg["spotlight"]
                    node.spotlight.update_config(sc)

                # Actualizar parámetros de Geocercas en caliente
                if "geofence" in c_cfg:
                    node.geofence.update_config(c_cfg["geofence"])

                # Actualizar parámetros de Grabación de Evidencia en caliente
                if "event_recording" in c_cfg:
                    node.event_recorder.update_config(c_cfg["event_recording"])

                # Actualizar parámetros de Auto-Zoom Dinámico en caliente
                if "ptz" in c_cfg and "auto_zoom" in c_cfg["ptz"]:
                    az = c_cfg["ptz"]["auto_zoom"]
                    node.ptz_manager.auto_zoom_enabled = bool(az.get("enabled", node.ptz_manager.auto_zoom_enabled))
                    node.ptz_manager.target_height_ratio_min = float(az.get("target_height_ratio_min", node.ptz_manager.target_height_ratio_min))
                    node.ptz_manager.target_height_ratio_max = float(az.get("target_height_ratio_max", node.ptz_manager.target_height_ratio_max))
                    node.ptz_manager.zoom_speed = float(az.get("zoom_speed", node.ptz_manager.zoom_speed))

                # Actualizar parámetros de Suavizado PTZ en caliente
                if "ptz" in c_cfg and "smoothing" in c_cfg["ptz"]:
                    sm = c_cfg["ptz"]["smoothing"]
                    node.ptz_manager.smoothing_enabled = bool(sm.get("enabled", node.ptz_manager.smoothing_enabled))
                    node.ptz_manager.smoothing_alpha = float(sm.get("alpha", node.ptz_manager.smoothing_alpha))




        # Actualizar logging dinámicamente si cambió
        log_cfg = new_cfg.get("logging", {})
        if log_cfg:
            is_quiet = bool(log_cfg.get("quiet", False))
            log_level = logging.ERROR if is_quiet else getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
            logging.getLogger().setLevel(log_level)
            for mod_name in ["ultralytics", "uvicorn", "uvicorn.access", "uvicorn.error", "zeep", "urllib3"]:
                logging.getLogger(mod_name).setLevel(logging.ERROR if is_quiet else logging.WARNING)

        logger.info("Configuración actualizada y guardada exitosamente.")
        return {"success": True, "message": "Configuración guardada y aplicada en caliente."}

    except Exception as e:
        logger.error(f"Error al guardar configuración: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# WEBSOCKET DE TELEMETRÍA EN VIVO
# -------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """Envía actualizaciones en tiempo real a los navegadores conectados."""
    await websocket.accept()
    web_manager.active_websockets.append(websocket)
    try:
        while True:
            # Enviar paquete de telemetría cada 300 ms
            cameras_data = [node.get_telemetry() for node in web_manager.camera_nodes]
            msg = {
                "timestamp": time.time(),
                "total_cameras": len(web_manager.camera_nodes),
                "cameras": cameras_data
            }
            await websocket.send_json(msg)
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Desconexión WebSocket: {e}")
    finally:
        if websocket in web_manager.active_websockets:
            web_manager.active_websockets.remove(websocket)


# -------------------------------------------------------------------------
# APIS DE AUTENTICACIÓN Y SEGURIDAD DEL PANEL
# -------------------------------------------------------------------------

@app.post("/api/login")
async def login(req: LoginRequest):
    """Verifica credenciales del administrador y establece la cookie de sesión."""
    sec_cfg = web_manager.config_data.get("security", {})
    valid_user = sec_cfg.get("admin_username", "admin")
    valid_pass = sec_cfg.get("admin_password", "admin123")

    if req.username == valid_user and req.password == valid_pass:
        import hashlib
        token = hashlib.sha256(f"{valid_user}:{valid_pass}".encode()).hexdigest()
        response = JSONResponse(content={"success": True, "message": "Autenticación exitosa."})
        response.set_cookie(key="session_auth", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
        return response
    else:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")


@app.post("/api/logout")
async def logout():
    """Elimina la cookie de sesión de autenticación."""
    response = JSONResponse(content={"success": True, "message": "Sesión cerrada correctamente."})
    response.delete_cookie(key="session_auth")
    return response


# -------------------------------------------------------------------------
# APIS DE CLIPS DE EVIDENCIA EN ALERTAS (MP4 + METADATA)
# -------------------------------------------------------------------------

@app.get("/api/events")
async def list_events():
    """Lista todos los clips de evidencia grabados automáticamente."""
    events_dir = Path("events")
    if not events_dir.exists():
        return {"events": []}

    events = []
    for meta_file in sorted(events_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(meta_file, "r", encoding="utf-8") as jf:
                meta = json.load(jf)
                # Verificar si el archivo MP4 correspondiente existe
                video_filename = meta.get("filename", "")
                if (events_dir / video_filename).exists():
                    events.append(meta)
        except Exception as e:
            logger.debug(f"Error leyendo evento {meta_file}: {e}")

    return {"events": events}


@app.get("/api/events/{filename}")
async def get_event_file(filename: str):
    """Descarga o transmite un clip MP4 de evidencia."""
    # Prevenir Directory Traversal
    clean_filename = Path(filename).name
    file_path = Path("events") / clean_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo de evidencia no encontrado")

    media_type = "video/mp4" if clean_filename.endswith(".mp4") else "application/json"
    return FileResponse(path=str(file_path), media_type=media_type, filename=clean_filename)


@app.delete("/api/events/{filename}")
async def delete_event_file(filename: str):
    """Elimina un clip MP4 y su archivo de metadata JSON asociado."""
    clean_filename = Path(filename).name
    base_name = clean_filename.rsplit(".", 1)[0]
    events_dir = Path("events")

    deleted = 0
    for ext in [".mp4", ".json"]:
        target = events_dir / f"{base_name}{ext}"
        if target.exists():
            try:
                target.unlink()
                deleted += 1
            except Exception as e:
                logger.error(f"Error eliminando {target}: {e}")

    return {"success": deleted > 0, "deleted_files": deleted}


# -------------------------------------------------------------------------
# APIS DE GEOCERCAS / POLÍGONOS DE DETECCIÓN POR CÁMARA
# -------------------------------------------------------------------------

@app.post("/api/camera/{cam_id}/geofence")
async def update_camera_geofence(cam_id: str, req: GeofenceUpdateRequest):
    """Actualiza la geocerca para una cámara específica y persiste en config.yaml."""
    node = web_manager.get_node(cam_id)
    if not node:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    geofence_dict = {
        "enabled": req.enabled,
        "mode": req.mode,
        "points": req.points
    }
    node.geofence.update_config(geofence_dict)

    # Actualizar y guardar en config.yaml
    cams = web_manager.config_data.get("cameras", [])
    for c in cams:
        if str(c.get("id")) == cam_id:
            c["geofence"] = geofence_dict
            break

    try:
        with open(web_manager.config_path, "w", encoding="utf-8") as f:
            yaml.dump(web_manager.config_data, f, allow_unicode=True, sort_keys=False)
        return {"success": True, "message": "Geocerca actualizada y guardada con éxito."}
    except Exception as e:
        logger.error(f"Error guardando geocerca en YAML: {e}")
        return {"success": True, "message": "Geocerca aplicada en memoria (error al guardar en YAML)."}

