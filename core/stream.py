"""
Gestor de Stream de Video en Tiempo Real con Cero Latencia y Auto-Reconexión.
Procesa frames exclusivamente en memoria RAM sin almacenar nada en disco.
"""

import time
import threading
import logging
from typing import Optional, Tuple, Union
import cv2
import numpy as np

logger = logging.getLogger("VideoStream")


class VideoStream:
    """
    Capturador de video multihilo diseñado para streams RTSP y webcams.
    Mantiene siempre en memoria el frame más reciente, descartando frames antiguos
    para eliminar la acumulación de latencia en el buffer de OpenCV.
    """

    def __init__(self, source: Union[str, int], reconnect_interval: int = 5):
        """
        :param source: URL RTSP (str), índice de webcam (int), o ruta a video.
        :param reconnect_interval: Segundos a esperar antes de reintentar conexión.
        """
        self.source = source
        self.reconnect_interval = reconnect_interval

        self.cap: Optional[cv2.VideoCapture] = None
        self.latest_frame: Optional[np.ndarray] = None
        self.is_connected = False
        self.is_running = False

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "VideoStream":
        """Inicia el hilo de captura en segundo plano."""
        if self.is_running:
            return self

        self.is_running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="VideoStreamThread")
        self._thread.start()
        logger.info(f"Iniciando captura de video para fuente: {self._mask_credentials(str(self.source))}")
        return self

    def _open_capture(self) -> bool:
        """Intenta abrir la fuente de video."""
        if self.cap is not None:
            self.cap.release()

        # Opciones para reducir buffering de red en OpenCV / FFmpeg
        if isinstance(self.source, str) and self.source.startswith("rtsp://"):
            # En plataformas compatibles, indicar transporte TCP y buffer mínimo
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self.cap = cv2.VideoCapture(self.source)

        if self.cap.isOpened():
            self.is_connected = True
            logger.info("Conexión con la fuente de video establecida exitosamente.")
            return True
        else:
            self.is_connected = False
            return False

    def _capture_loop(self):
        """Bucle continuo de lectura de frames en segundo plano."""
        while self.is_running:
            if not self.is_connected or self.cap is None or not self.cap.isOpened():
                success = self._open_capture()
                if not success:
                    logger.warning(
                        f"No se pudo conectar a la cámara. Reintentando en {self.reconnect_interval} segundos..."
                    )
                    time.sleep(self.reconnect_interval)
                    continue

            # Lectura del frame
            ret, frame = self.cap.read()
            if not ret or frame is None:
                logger.warning("Fallo al leer frame del stream. Marcando como desconectado...")
                self.is_connected = False
                time.sleep(1.0)
                continue

            # Guardar el frame más reciente en memoria bajo bloqueo
            with self._lock:
                self.latest_frame = frame

        # Limpieza al detener
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.is_connected = False
        logger.info("Captura de video detenida.")

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Retorna una tupla (éxito, frame_mas_reciente) directamente de memoria RAM.
        No bloquea ni acumula colas.
        """
        with self._lock:
            if self.latest_frame is None or not self.is_connected:
                return False, None
            # Devolvemos una copia o referencia del frame actual
            return True, self.latest_frame.copy()

    def get_resolution(self) -> Tuple[int, int]:
        """Retorna (ancho, alto) de la imagen si está disponible."""
        with self._lock:
            if self.latest_frame is not None:
                h, w = self.latest_frame.shape[:2]
                return w, h
            if self.cap is not None and self.cap.isOpened():
                w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                return w, h
        return 0, 0

    def stop(self):
        """Detiene el hilo y libera los recursos."""
        self.is_running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @staticmethod
    def _mask_credentials(url: str) -> str:
        """Enmascara contraseñas en URLs RTSP para los logs por seguridad."""
        if "@" in url and "://" in url:
            try:
                protocol, rest = url.split("://", 1)
                creds, host = rest.split("@", 1)
                if ":" in creds:
                    user, _ = creds.split(":", 1)
                    return f"{protocol}://{user}:****@{host}"
            except Exception:
                pass
        return url
