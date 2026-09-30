"""
Compositor de Visualización en Cuadrícula (Mosaico Multi-Cámara).
Combina N frames de cámaras en una única vista matricial en tiempo real.
Opera 100% en memoria RAM sin realizar escrituras a disco.
"""

import math
from typing import List
import cv2
import numpy as np


class GridRenderer:
    """
    Organiza frames de múltiples cámaras en una cuadrícula equilibrada:
    - 1 Cámara: Pantalla completa nativa.
    - 2 Cámaras: 1 fila x 2 columnas.
    - 3 - 4 Cámaras: Cuadrícula 2x2.
    - 5 - 6 Cámaras: Cuadrícula 2x3.
    - 7 - 9 Cámaras: Cuadrícula 3x3.
    """

    def __init__(self, target_cell_width: int = 640, target_cell_height: int = 360):
        self.cell_w = target_cell_width
        self.cell_h = target_cell_height

    def compose_grid(self, frames: List[np.ndarray]) -> np.ndarray:
        """
        Combina una lista de frames en un único lienzo en memoria.
        """
        n = len(frames)
        if n == 0:
            blank = np.zeros((self.cell_h, self.cell_w, 3), dtype=np.uint8)
            cv2.putText(blank, "SIN CAMARAS CONFIGURADAS", (150, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return blank

        if n == 1:
            return frames[0]

        # Calcular número de columnas y filas
        if n == 2:
            cols, rows = 2, 1
        elif n <= 4:
            cols, rows = 2, 2
        elif n <= 6:
            cols, rows = 3, 2
        elif n <= 9:
            cols, rows = 3, 3
        else:
            cols = math.ceil(math.sqrt(n))
            rows = math.ceil(n / cols)

        # Ajustar dimensiones de celda según el número de columnas para no desbordar monitores estándar
        cell_w = self.cell_w
        cell_h = self.cell_h
        if cols >= 3:
            cell_w = int(self.cell_w * 0.75)
            cell_h = int(self.cell_h * 0.75)

        # Normalizar y redimensionar cada frame al tamaño de celda uniforme
        resized_cells = []
        for frame in frames:
            if frame.shape[1] != cell_w or frame.shape[0] != cell_h:
                resized = cv2.resize(frame, (cell_w, cell_h), interpolation=cv2.INTER_LINEAR)
            else:
                resized = frame
            resized_cells.append(resized)

        # Rellenar celdas vacías con mosaicos negros si faltan para completar la matriz
        total_slots = cols * rows
        while len(resized_cells) < total_slots:
            empty_tile = np.zeros((cell_h, cell_w, 3), dtype=np.uint8)
            resized_cells.append(empty_tile)

        # Construir cuadrícula por filas
        row_strips = []
        for r in range(rows):
            row_frames = resized_cells[r * cols : (r + 1) * cols]
            row_strip = np.hstack(row_frames)
            row_strips.append(row_strip)

        grid_canvas = np.vstack(row_strips)
        return grid_canvas
