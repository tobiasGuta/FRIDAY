"""Experimental Qt-painted orbital hologram (Slice 1).

Pure presentation. No microphone amplitude, SDK data, GPU assets or network access.
The accepted v0.5.6 hologram renderer remains a separate, unchanged fallback.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient

# Values reflect actual session state, not audio volume or model confidence.
_STATE_ENERGY = {
    "Disconnected": 0.27,
    "Connecting": 0.57,
    "Ready": 0.73,
    "Listening": 0.97,
    "Responding": 1.0,
    "Disconnecting": 0.38,
}
_CENTER = QPointF(92, 92)


def animation_speed(state: str) -> float:
    """A static disconnected orb does not consume idle animation work."""
    return {
        "Ready": 0.42,
        "Connecting": 0.76,
        "Listening": 0.68,
        "Responding": 1.0,
        "Disconnecting": 0.38,
    }.get(state, 0.0)


def _tint(hex_color: str, alpha: int) -> QColor:
    color = QColor(hex_color)
    color.setAlpha(max(0, min(255, alpha)))
    return color


def _orbit(
    painter: QPainter, *,
    phase: float, tilt: float, width: float, height: float,
    speed: float, offset: float, energy: float,
) -> None:
    """Three layered elliptical mechanisms give a simulated 3D silhouette."""
    painter.save()
    painter.translate(_CENTER)
    painter.rotate(tilt + 5.5 * math.sin(phase * 0.22 + offset))
    ellipse = QRectF(-width, -height, width * 2, height * 2)
    theta = (math.degrees(phase * speed) + offset * 57) % 360

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_tint("#F7A64D", int(57 * energy)), 0.75))
    painter.drawEllipse(ellipse)

    # The back half is deliberately faint. Glowing foreground arc fragments
    # simulate depth without QML, shaders, textures or real 3D scene objects.
    for start, span, intensity in ((theta + 10, 116, 1.0), (theta + 196, 63, 0.65)):
        alpha = int(160 * energy * intensity)
        painter.setPen(QPen(_tint("#F6A03B", int(alpha * 0.27)), 3.1))
        painter.drawArc(ellipse, round(start * 16), round(span * 16))
        painter.setPen(QPen(_tint("#FFDC96", alpha), 1.05))
        painter.drawArc(ellipse, round(start * 16), round(span * 16))
    painter.restore()



def _paint_fragments(painter: QPainter, *, phase: float, energy: float) -> None:
    """Draw a small deterministic energy field around the existing orbital core.

    These are decorative Qt primitives, not sampled audio or random particles.
    Fixed positions and phase-derived motion make every frame reproducible.
    """
    painter.save()

    # Sparse points at the perimeter leave the central energy core readable.
    # Alternating drift directions and opacity give a layered depth illusion.
    for index in range(18):
        theta = index * 2.399963229728653 + phase * (0.10 if index % 2 else -0.075)
        radius = 69 + (index * 11 % 19)
        point = QPointF(
            _CENTER.x() + math.cos(theta) * radius,
            _CENTER.y() + math.sin(theta) * radius * 0.79,
        )
        shimmer = 0.65 + 0.35 * math.sin(phase * 0.47 + index * 1.37)
        alpha = round((100 + 64 * shimmer) * energy)
        size = 1.15 if index % 5 == 0 else 0.7
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tint("#F7A64D", round(alpha * 0.22)))
        painter.drawEllipse(point, size + 1.6, size + 1.6)
        painter.setBrush(_tint("#FFE4A8", alpha))
        painter.drawEllipse(point, size, size)

    # Glowing fragments follow the same three tilted orbital planes as Slice 1.
    # Short, faded tails are drawn behind selected segments, never full trails.
    planes = (
        (22, 81, 27, 0.65, 0.1),
        (83, 73, 40, -0.40, 2.4),
        (-39, 66, 20, 0.22, 4.7),
    )
    for plane, (tilt, width, height, speed, offset) in enumerate(planes):
        painter.save()
        painter.translate(_CENTER)
        painter.rotate(tilt + 5.5 * math.sin(phase * 0.22 + offset))
        ellipse = QRectF(-width, -height, width * 2, height * 2)
        for segment in range(2):
            angle = math.degrees(phase * speed) + offset * 57 + segment * 169
            shimmer = 0.70 + 0.30 * math.sin(phase * 0.55 + plane + segment)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_tint("#F7A64D", round(66 * energy * shimmer)), 3.0))
            painter.drawArc(ellipse, round(angle * 16), 10 * 16)
            painter.setPen(QPen(_tint("#FFE5AD", round(173 * energy * shimmer)), 1.0))
            painter.drawArc(ellipse, round(angle * 16), 10 * 16)
            if segment == 0:
                for tail in range(2):
                    painter.setPen(
                        QPen(
                            _tint("#EFA04B", round((39 - 13 * tail) * energy * shimmer)),
                            0.75,
                        )
                    )
                    painter.drawArc(
                        ellipse, round((angle - (tail + 1) * 9) * 16), 6 * 16
                    )
        painter.restore()

    painter.restore()


def paint_orbital_lab(
    painter: QPainter, *, phase: float, state: str, fragments: bool = False
) -> None:
    """Draw the optional v0.5.7 renderer in the existing 184x184 orb coordinate space."""
    energy = _STATE_ENERGY.get(state, 0.3)
    breathing = (1.0 + math.sin(phase * 0.85)) / 2.0
    responding = state == "Responding"

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    aura = QRadialGradient(_CENTER, 89)
    aura.setColorAt(0, _tint("#FFB95E", int((30 + breathing * 11) * energy)))
    aura.setColorAt(0.58, _tint("#E58A35", int(24 * energy)))
    aura.setColorAt(1, _tint("#E58A35", 0))
    painter.setBrush(aura)
    painter.drawEllipse(_CENTER, 89, 89)

    # Independent counter-rotating orbital planes: angles and arc phases vary.
    _orbit(
        painter, phase=phase, tilt=22, width=81, height=27,
        speed=0.65, offset=0.1, energy=energy,
    )
    _orbit(
        painter, phase=phase, tilt=83, width=73, height=40,
        speed=-0.40, offset=2.4, energy=energy,
    )
    _orbit(
        painter, phase=phase, tilt=-39, width=66, height=20,
        speed=0.22, offset=4.7, energy=energy,
    )

    # Restrained fixed circuit ticks. A separate fragment/particle field is
    # deliberately deferred to Slice 2, after Windows visual acceptance.
    for index in range(48):
        theta = math.tau * index / 48 + phase * 0.10
        radius = 80 if index % 4 == 0 else 78
        length = 5.0 if index % 4 == 0 else 2.0
        alpha = int((123 if index % 4 == 0 else 62) * energy)
        painter.setPen(QPen(_tint("#FFD18D", alpha), 0.9))
        painter.drawLine(
            QPointF(
                _CENTER.x() + math.cos(theta) * (radius - length),
                _CENTER.y() + math.sin(theta) * (radius - length),
            ),
            QPointF(
                _CENTER.x() + math.cos(theta) * radius,
                _CENTER.y() + math.sin(theta) * radius,
            ),
        )

    if fragments:
        _paint_fragments(painter, phase=phase, energy=energy)

    # The center breathes slightly at rest and becomes brighter while speaking.
    painter.setPen(Qt.PenStyle.NoPen)
    radius = 32.0 + (1.6 * breathing if state != "Disconnected" else 0.0)
    core = QRadialGradient(_CENTER, radius + 8)
    core.setColorAt(0, _tint("#FFF7D9", int(252 * energy)))
    core.setColorAt(0.24, _tint("#FFD58A", int(240 * energy)))
    core.setColorAt(0.65, _tint("#EC9636", int(217 * energy)))
    core.setColorAt(1.0, _tint("#9D511D", int(156 * energy)))
    painter.setBrush(core)
    painter.drawEllipse(_CENTER, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_tint("#FFE7B5", int(169 * energy)), 0.85))
    painter.drawEllipse(_CENTER, radius, radius)

    if responding:
        # Decorative state pulse; this is NOT a measured audio waveform.
        pulse = 39.0 + breathing * 4.0
        painter.setPen(QPen(_tint("#FFCB7B", round(92 * (1.0 - breathing * 0.55))), 1.0))
        painter.drawEllipse(_CENTER, pulse, pulse)
    painter.restore()
