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
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logger = logging.getLogger("WebServer")

# Modelos Pydantic para APIs
class PTZMoveRequest(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0

class ConfigUpdateRequest(BaseModel):
    config: Dict[str, Any]


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página principal del Dashboard de Monitoreo."""
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
            "total_cameras": len(cams)
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
        with open(web_manager.config_path, "w", encoding="utf-8") as f:
            yaml.dump(new_cfg, f, allow_unicode=True, sort_keys=False)

        web_manager.config_data = new_cfg

        # Actualizar en caliente los parámetros aplicables a los nodos en ejecución
        det_cfg = new_cfg.get("detection", {})
        if web_manager.detector and det_cfg:
            web_manager.detector.confidence = float(det_cfg.get("confidence", 0.30))
            if "target_classes" in det_cfg:
                web_manager.detector.target_classes = det_cfg["target_classes"]
                web_manager.detector.target_class_ids = [
                    cid for cid, name in web_manager.detector.class_names.items()
                    if name in web_manager.detector.target_classes
                ]

        # Actualizar parámetros PTZ de cada cámara
        cams_cfg = new_cfg.get("cameras", [])
        for c_cfg in cams_cfg:
            cid = c_cfg.get("id")
            node = web_manager.get_node(cid)
            if node:
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
