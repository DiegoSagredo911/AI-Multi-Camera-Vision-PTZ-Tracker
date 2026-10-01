#!/usr/bin/env python3
"""
Sistema Universal Multi-Cámara de Detección con IA y Control PTZ Inteligente.
Procesa N cámaras en paralelo 100% en memoria RAM (cero almacenamiento en disco).
Soporta cámaras fijas Dahua (IPC-HFW1320S-W), cámaras PTZ motorizadas y Webcams.
"""

import sys
import time
import signal
import logging
import argparse
from pathlib import Path
from typing import List
import yaml
import cv2

from core.detector import ObjectDetector
from core.camera_node import CameraNode
from core.grid_renderer import GridRenderer

# Configuración de Logging Inicial
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Main")


def configure_logging(quiet: bool = False, level_str: str = "INFO"):
    """Configura el nivel de logs en consola y silencia dependencias ruidosas."""
    target_level = logging.ERROR if quiet else getattr(logging, level_str.upper(), logging.INFO)
    logging.getLogger().setLevel(target_level)
    
    # Silenciar librerías externas para mantener la consola limpia
    for mod_name in ["ultralytics", "uvicorn", "uvicorn.access", "uvicorn.error", "zeep", "urllib3"]:
        logging.getLogger(mod_name).setLevel(logging.ERROR if quiet else logging.WARNING)


def load_config(config_path: str = "config.yaml") -> dict:
    """Carga y valida el archivo de configuración YAML."""
    path = Path(config_path)
    if not path.is_file():
        logger.error(f"No se encontró el archivo de configuración en {config_path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


def main():
    parser = argparse.ArgumentParser(
        description="Sistema Universal Multi-Cámara de Detección con IA y Control PTZ"
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Ruta al archivo config.yaml"
    )
    parser.add_argument(
        "--source", default=None, help="Ejecutar una sola cámara rápida (Webcam index 0, archivo o RTSP)"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Ejecutar en segundo plano sin ventana gráfica de escritorio"
    )
    parser.add_argument(
        "--web", action="store_true", help="Iniciar la Interfaz Gráfica Web (FastAPI + Uvicorn)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Dirección host del servidor web (por defecto: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Puerto del servidor web (por defecto: 8000)"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Omitir logs en consola (modo silencioso, sólo errores)"
    )
    args = parser.parse_args()

    # Cargar Configuración
    config = load_config(args.config)
    det_cfg = config.get("detection", {})
    disp_cfg = config.get("display", {})
    log_cfg = config.get("logging", {})

    # Aplicar nivel de logs (CLI --quiet tiene prioridad, luego config.yaml)
    is_quiet = args.quiet or bool(log_cfg.get("quiet", False))
    log_level = "ERROR" if is_quiet else str(log_cfg.get("level", "INFO"))
    configure_logging(quiet=is_quiet, level_str=log_level)

    # 1. Inicializar Motor IA Compartido en Memoria
    logger.info("Inicializando Motor IA (YOLOv8) compartido en memoria...")
    detector = ObjectDetector(
        model_name=det_cfg.get("model_name", "yolov8n.pt"),
        target_classes=det_cfg.get("target_classes", ["person", "dog", "car"]),
        confidence=float(det_cfg.get("confidence", 0.40)),
        device=det_cfg.get("device", "auto")
    )

    # 2. Inicializar Nodos de Cámaras
    camera_nodes: List[CameraNode] = []

    # Si se pasó un --source específico por consola, se crea un nodo único de prueba
    if args.source is not None:
        source_val = int(args.source) if args.source.isdigit() else args.source
        cam_cfg = {
            "id": "cli_source",
            "name": f"Fuente Consola ({args.source})",
            "type": "webcam" if isinstance(source_val, int) else "custom",
            "custom_rtsp_url": str(source_val) if not isinstance(source_val, int) else "",
            "webcam_index": source_val if isinstance(source_val, int) else 0,
            "ptz": {"enabled": False}
        }
        node = CameraNode(cam_cfg, det_cfg)
        node.start()
        camera_nodes.append(node)
    else:
        # Cargar lista de cámaras de config.yaml
        configured_cameras = config.get("cameras") or []
        if not configured_cameras:
            # Compatibilidad hacia atrás si usan video_source de la versión 1
            old_src = config.get("video_source") or {}
            if old_src:
                configured_cameras = [{
                    "id": "cam_1",
                    "name": "Cámara Principal",
                    **old_src,
                    "ptz": config.get("ptz", {})
                }]

        for c_cfg in configured_cameras:
            if isinstance(c_cfg, dict):
                node = CameraNode(c_cfg, det_cfg)
                node.start()
                camera_nodes.append(node)

    if not camera_nodes:
        if not args.web:
            logger.error("No se configuraron cámaras en config.yaml. Inicia con --web para configurarlas desde la interfaz web.")
            sys.exit(1)
        else:
            logger.info("ℹ️ No hay cámaras preconfiguradas en config.yaml. Puedes agregarlas desde la interfaz Web.")

    # 3. Inicializar Compositor de Mosaico (Grid)
    cell_w = int(disp_cfg.get("cell_width", 640))
    cell_h = int(disp_cfg.get("cell_height", 360))
    grid_renderer = GridRenderer(target_cell_width=cell_w, target_cell_height=cell_h)

    # Manejo de cierre limpio con Ctrl+C (SIGINT / SIGTERM)
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Señal de cierre recibida. Deteniendo todas las cámaras ordenadamente...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    show_window = disp_cfg.get("show_window", True) and not args.headless
    window_title = disp_cfg.get("window_title", "AI Multi-Camera Vision & PTZ Tracker")

    # 4. Iniciar Servidor Web si se pasó el argumento --web
    if args.web:
        import threading
        import uvicorn
        from web.app import app, web_manager

        web_manager.camera_nodes = camera_nodes
        web_manager.grid_renderer = grid_renderer
        web_manager.detector = detector
        web_manager.config_path = args.config
        web_manager.config_data = config

        def run_uvicorn():
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

        web_thread = threading.Thread(target=run_uvicorn, daemon=True)
        web_thread.start()

    logger.info("=================================================================")
    logger.info(f"   SISTEMA MULTI-CÁMARA INICIADO: {len(camera_nodes)} CÁMARAS ACTIVAS")
    logger.info("   Política: 100% en memoria RAM (Sin almacenamiento en disco)   ")
    logger.info(f"   Clases IA Monitoreadas: {detector.target_classes}             ")
    if args.web:
        host_display = "localhost" if args.host == "0.0.0.0" else args.host
        logger.info(f"   🌐 Interfaz Web: http://{host_display}:{args.port}               ")
    for idx, node in enumerate(camera_nodes, 1):
        ptz_info = "PTZ Motorizado" if node.ptz_manager.has_hardware_ptz() else "Cámara Fija"
        logger.info(f"     [{idx}] {node.name} -> {ptz_info}")
    if show_window:
        logger.info("   Controles en ventana:")
        logger.info("     [q] - Salir")
        logger.info("     [1..9] - Alternar PTZ Digital en la cámara respectiva")
        logger.info("     [p] - Alternar patrullaje manual en cámaras PTZ")
    logger.info("=================================================================")

    try:
        while running:
            if not camera_nodes:
                time.sleep(0.2)
                continue

            # Procesar cada cámara secuencialmente sobre el frame más reciente
            for node in list(camera_nodes):
                ret, frame = node.read_frame()
                if ret and frame is not None:
                    node.frame_count += 1
                    # Si frame_skip > 1, solo ejecutamos la inferencia pesada de YOLO
                    # 1 de cada 'frame_skip' cuadros
                    if (node.frame_count % node.frame_skip) == 0:
                        # Inferencia YOLO con tracking temporal
                        detections = detector.detect_and_track(frame)
                        # Selección del objetivo prioritario
                        primary_target = detector.select_primary_target(
                            detections, node.current_target_id
                        )
                        # Actualizar estado, lazo PTZ y HUD del nodo
                        node.update_cycle(frame, detections, primary_target, detector)
                    else:
                        # Cuadro saltado: actualiza PTZ y HUD reutilizando estado previo (Ahorro CPU)
                        node.update_skipped_cycle(frame, detector)

            # Si la visualización gráfica está habilitada, componer y mostrar mosaico
            if show_window:
                display_frames = [node.get_display_frame() for node in camera_nodes]
                grid_canvas = grid_renderer.compose_grid(display_frames)

                cv2.imshow(window_title, grid_canvas)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    logger.info("Tecla 'q' presionada. Cerrando sistema...")
                    break
                elif key == ord("p"):
                    for node in camera_nodes:
                        if node.ptz_manager.has_hardware_ptz():
                            node.ptz_manager.toggle_patrol()
                # Teclas 1 a 9 para activar zoom digital en una cámara específica
                elif ord("1") <= key <= ord("9"):
                    cam_idx = key - ord("1")
                    if cam_idx < len(camera_nodes):
                        target_node = camera_nodes[cam_idx]
                        target_node.ptz_manager.use_digital_ptz = not target_node.ptz_manager.use_digital_ptz
                        estado = "ACTIVADO" if target_node.ptz_manager.use_digital_ptz else "DESACTIVADO"
                        logger.info(f"PTZ Digital en {target_node.name}: {estado}")
            else:
                # En modo headless, breve descanso para evitar consumo excesivo en bucle vacío
                time.sleep(0.01)

    finally:
        # Cierre ordenado de todos los recursos
        logger.info("Deteniendo todos los nodos de cámaras...")
        for node in camera_nodes:
            node.stop()
        if show_window:
            cv2.destroyAllWindows()
        logger.info("Sistema multi-cámara detenido correctamente.")


if __name__ == "__main__":
    main()
