"""Canvas 기반 360° 레이더 위젯 — UI 전용, 디바이스·시리얼을 전혀 모른다."""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import flet as ft
import flet.canvas as cv

MAX_DIST_CM = 300  # 최대 표시 거리 (요구사항정의서 6.2)
GRID_CIRCLES_CM = (50, 100, 200, 300)
ANGLE_GUIDES_DEG = (-90, -45, 0, 45, 90)
CANVAS_SIZE_PX = 460
CANVAS_MARGIN_PX = 30
POINT_RADIUS_PX = 6
GRID_LABEL_SIZE = 10
POINT_LABEL_SIZE = 12
GRID_STROKE_WIDTH = 1
RING_STROKE_WIDTH = 2  # 각도 없는 타겟(거리만)의 링 두께
RING_LABEL_OFFSET_PX = 14

BG_COLOR = "#0a140d"
GRID_COLOR = ft.Colors.GREEN_900
GRID_LABEL_COLOR = ft.Colors.GREEN_700
POINT_COLOR = ft.Colors.GREEN_ACCENT_400  # 색 미지정 시 기본색
POINT_WARN_COLOR = ft.Colors.ORANGE_ACCENT_400  # 범위 초과 경고색


@dataclass(frozen=True)
class RadarTarget:
    """레이더에 그릴 타겟 1개. angle_deg=None이면 거리 링으로 표시한다."""

    dist_cm: int
    angle_deg: Optional[int]
    warn: bool
    label: Optional[str]
    color: str = POINT_COLOR


class RadarView(ft.Container):
    """동심원·각도 눈금 위에 태그 점 1개를 그리는 레이더 뷰 (0°=화면 위 12시)."""

    def __init__(self, size_px: int = CANVAS_SIZE_PX) -> None:
        self._cx = size_px / 2
        self._cy = size_px / 2
        self._radius_px = size_px / 2 - CANVAS_MARGIN_PX
        self._background = self._build_background()
        self._canvas = cv.Canvas(
            shapes=list(self._background), width=size_px, height=size_px
        )
        super().__init__(
            content=self._canvas,
            bgcolor=BG_COLOR,
            border_radius=12,
            padding=8,
            alignment=ft.Alignment(0.5, 0.5),
        )

    def update_points(self, targets: List[RadarTarget]) -> None:
        """여러 타겟을 한 번에 그린다 (각도 있음=점, 없음=거리 링)."""
        shapes: List[cv.Shape] = list(self._background)
        for t in targets:
            if t.angle_deg is None:
                shapes.extend(self._ring_shapes(t))
            else:
                shapes.extend(self._point_shapes(t))
        self._canvas.shapes = shapes

    def hide_point(self) -> None:
        """데이터 없음 시 타겟 표시를 숨기고 배경만 남긴다."""
        self._canvas.shapes = list(self._background)

    def _point_shapes(self, t: RadarTarget) -> List[cv.Shape]:
        """각도가 있는 타겟 = 채운 점 + 라벨. 범위 초과면 경고색."""
        assert t.angle_deg is not None
        x, y = self._polar_to_xy(t.dist_cm, t.angle_deg)
        color = POINT_WARN_COLOR if t.warn else t.color
        label = f"{t.dist_cm}cm / {t.angle_deg}°"
        if t.label is not None:
            label = f"{t.label}: {label}"
        return [
            cv.Circle(
                x,
                y,
                POINT_RADIUS_PX,
                ft.Paint(color=color, style=ft.PaintingStyle.FILL),
            ),
            cv.Text(
                x + POINT_RADIUS_PX + 4,
                y - POINT_RADIUS_PX - 12,
                label,
                ft.TextStyle(size=POINT_LABEL_SIZE, color=color),
            ),
        ]

    def _ring_shapes(self, t: RadarTarget) -> List[cv.Shape]:
        """각도 없는 타겟(UCI 보드=안테나 1개) = 그 거리 반지름의 링 + 라벨."""
        r_px = min(t.dist_cm, MAX_DIST_CM) / MAX_DIST_CM * self._radius_px
        color = POINT_WARN_COLOR if t.warn else t.color
        label = f"{t.dist_cm}cm"
        if t.label is not None:
            label = f"{t.label}: {label}"
        return [
            cv.Circle(
                self._cx,
                self._cy,
                r_px,
                ft.Paint(
                    color=color,
                    stroke_width=RING_STROKE_WIDTH,
                    style=ft.PaintingStyle.STROKE,
                ),
            ),
            cv.Text(
                self._cx + 4,
                self._cy - r_px - RING_LABEL_OFFSET_PX,
                label,
                ft.TextStyle(size=POINT_LABEL_SIZE, color=color),
            ),
        ]

    def _polar_to_xy(self, dist_cm: int, angle_deg: int) -> Tuple[float, float]:
        """x = cx + r·sin(θ), y = cy − r·cos(θ). 범위 초과 거리는 바깥 눈금에 클램프."""
        r_px = min(dist_cm, MAX_DIST_CM) / MAX_DIST_CM * self._radius_px
        theta = math.radians(angle_deg)
        return self._cx + r_px * math.sin(theta), self._cy - r_px * math.cos(theta)

    def _build_background(self) -> List[cv.Shape]:
        """동심원(거리 눈금) + 각도 가이드선 배경을 만든다. 연결 여부와 무관하게 고정."""
        return self._grid_circles() + self._angle_guides()

    def _grid_circles(self) -> List[cv.Shape]:
        """50/100/200/300cm 동심원과 cm 라벨."""
        stroke = ft.Paint(
            color=GRID_COLOR,
            stroke_width=GRID_STROKE_WIDTH,
            style=ft.PaintingStyle.STROKE,
        )
        shapes: List[cv.Shape] = []
        for dist in GRID_CIRCLES_CM:
            r_px = dist / MAX_DIST_CM * self._radius_px
            shapes.append(cv.Circle(self._cx, self._cy, r_px, stroke))
            shapes.append(
                cv.Text(
                    self._cx + 4,
                    self._cy - r_px + 2,
                    f"{dist}",
                    ft.TextStyle(size=GRID_LABEL_SIZE, color=GRID_LABEL_COLOR),
                )
            )
        return shapes

    def _angle_guides(self) -> List[cv.Shape]:
        """−90/−45/0/+45/+90° 가이드선과 각도 라벨 (표시 범위 상반부)."""
        stroke = ft.Paint(
            color=GRID_COLOR,
            stroke_width=GRID_STROKE_WIDTH,
            style=ft.PaintingStyle.STROKE,
        )
        shapes: List[cv.Shape] = []
        for deg in ANGLE_GUIDES_DEG:
            theta = math.radians(deg)
            x2 = self._cx + self._radius_px * math.sin(theta)
            y2 = self._cy - self._radius_px * math.cos(theta)
            shapes.append(cv.Line(self._cx, self._cy, x2, y2, stroke))
            lx = self._cx + (self._radius_px + 8) * math.sin(theta)
            ly = self._cy - (self._radius_px + 8) * math.cos(theta)
            shapes.append(
                cv.Text(
                    lx - 8,
                    ly - 6,
                    f"{deg:+d}°" if deg else "0°",
                    ft.TextStyle(size=GRID_LABEL_SIZE, color=GRID_LABEL_COLOR),
                )
            )
        return shapes
