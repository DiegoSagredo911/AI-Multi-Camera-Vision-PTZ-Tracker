"""
Interfaz base abstracta para controladores PTZ universales.
"""

from abc import ABC, abstractmethod


class BasePTZController(ABC):
    """
    Clase base abstracta para cualquier controlador PTZ (físico o digital).
    """

    @abstractmethod
    def is_available(self) -> bool:
        """
        Retorna True si el hardware o servicio PTZ está disponible y conectado.
        """
        pass

    @abstractmethod
    def continuous_move(
        self,
        pan_velocity: float,
        tilt_velocity: float,
        zoom_velocity: float = 0.0
    ) -> bool:
        """
        Envía un comando de movimiento continuo con velocidades normalizadas entre -1.0 y 1.0.
        :param pan_velocity: Horizontal (-1.0 Izquierda, 1.0 Derecha)
        :param tilt_velocity: Vertical (-1.0 Abajo, 1.0 Arriba)
        :param zoom_velocity: Zoom (-1.0 Zoom Out, 1.0 Zoom In)
        """
        pass

    @abstractmethod
    def stop(self) -> bool:
        """
        Detiene todo movimiento PTZ inmediatamente.
        """
        pass

    def get_status(self) -> dict:
        """
        Retorna el estado de posición actual y movimiento si la cámara lo soporta:
        ej: {"supported": True, "pan": 0.0, "tilt": 0.0, "is_moving": False}
        """
        return {"supported": False, "pan": None, "tilt": None, "is_moving": False}

    def goto_preset(self, preset_token: str) -> bool:
        """
        Mueve la cámara a una posición preestablecida (Preset) nativa del hardware.
        """
        return False

    def set_preset(self, preset_name: str = "Home", preset_token: str = "1") -> bool:
        """
        Guarda la posición actual de la cámara como un Preset nativo en la memoria de la cámara.
        """
        return False

    def get_presets(self) -> list:
        """
        Obtiene la lista de presets guardados en la cámara.
        """
        return []

    def get_serial_number(self) -> Optional[str]:
        """
        Retorna el Número de Serie (SN) único del hardware de la cámara si está disponible.
        """
        return None

    def close(self):
        """
        Libera conexiones y recursos del controlador.
        """
        pass


