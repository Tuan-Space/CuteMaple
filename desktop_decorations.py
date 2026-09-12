"""Small local, ephemeral decorations. No keyboard text is ever accepted."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class _Particle:
    kind: str
    x: float
    y: float
    vx: float
    vy: float
    age: float
    lifetime: float
    angle: float
    spin: float
    size: float
    text: str = ""


class DesktopDecorations(QWidget):
    MAX_PARTICLES = 24
    TEXTS = ("加油", "专注中", "开心")
    RECIPES = {'keyboard': ('leaf', 'star'), 'audio': ('note', 'bubble'), 'happy': ('heart', 'petal', 'star'),
               'clean_dust': ('dust', 'dust', 'dust'), 'clean_done': ('star', 'star', 'petal'),
               **{name: (name,) for name in ('leaf', 'note', 'star', 'heart', 'bubble', 'petal', 'dust', 'sleep')}}
    INTERVALS = {name: .4 if name == 'keyboard' else .45 if name == 'audio' else .16 for name in RECIPES}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self._particles: list[_Particle] = []
        self._last_emit: dict[str, float] = {}
        self._suspended = False
        self._rng = random.Random()
        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance)
        if parent:
            self.setGeometry(parent.rect())

    def trigger(self, kind: str, x: float, y: float) -> None:
        if kind not in self.INTERVALS or self._suspended or not self.isVisible():
            return
        if not math.isfinite(x) or not math.isfinite(y):
            return
        now = time.monotonic()
        if now - self._last_emit.get(kind, -math.inf) < self.INTERVALS[kind]:
            return
        if len(self._particles) >= self.MAX_PARTICLES:
            return
        self._last_emit[kind] = now
        scale = max(.4, self.width() / 220)
        bounds = self._visible_bounds()
        if min(bounds.width(), bounds.height()) < 30 * scale:
            return
        for shape in self.RECIPES[kind][:self.MAX_PARTICLES - len(self._particles)]:
            size = self._rng.uniform(7, 10) * scale * (.4 if shape == 'dust' else 1)
            margin = size + 4 * scale
            side = 1 if len(self._particles) % 2 else -1
            origin_x = x if kind in ('clean_dust', 'dust') else x + side * self.width() * .2
            tier = (len(self._particles) // 2 % 3 - 1) * self.height() * .085
            origin_y = y if kind in ('clean_dust', 'dust') else y + tier
            px = min(max(bounds.left() + margin, origin_x), bounds.right() - margin)
            py = min(max(bounds.top() + margin, origin_y), bounds.bottom() - margin)
            sx = 1 if px - bounds.left() < 40 * scale else -1 if bounds.right() - px < 40 * scale else side
            sy = 1 if py - bounds.top() < 30 * scale else -1
            self._particles.append(_Particle(
                shape, px, py, sx * self._rng.uniform(12, 23) * scale, sy * self._rng.uniform(14, 21) * scale,
                0.0, self._rng.uniform(1.5, 2), self._rng.uniform(-20, 20),
                self._rng.uniform(-30, 30), size, ''))
        if not self._timer.isActive():
            self._last_tick = now
            self._timer.start()
        self.raise_()
        self.update()

    def _advance(self) -> None:
        now = time.monotonic()
        dt = max(0.0, min(0.1, now - self._last_tick))
        self._last_tick = now
        bounds = self._visible_bounds()
        if min(bounds.width(), bounds.height()) < 30 * max(.4, self.width() / 220):
            self.stop()
            return
        for particle in self._particles:
            particle.age += dt
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.angle += particle.spin * dt
            margin = particle.size + 4 * max(.4, self.width() / 220)
            if particle.x < bounds.left() + margin or particle.x > bounds.right() - margin:
                particle.vx *= -1
            if particle.y < bounds.top() + margin or particle.y > bounds.bottom() - margin:
                particle.vy *= -1
            particle.x = max(bounds.left() + margin, min(bounds.right() - margin, particle.x))
            particle.y = max(bounds.top() + margin, min(bounds.bottom() - margin, particle.y))
        self._particles = [p for p in self._particles if p.age < p.lifetime]
        if not self._particles:
            self._timer.stop()
        self.update()

    def _visible_bounds(self):
        bounds = self.rect()
        parent = self.parentWidget()
        if parent and parent.screen():
            bounds = bounds.intersected(parent.screen().availableGeometry().translated(-self.mapToGlobal(self.rect().topLeft())))
        return bounds

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = bool(suspended)
        if suspended:
            self.stop()

    def stop(self) -> None:
        self._timer.stop()
        self._particles.clear()
        self._last_emit.clear()
        self.update()

    def hideEvent(self, event) -> None:
        self.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for particle in self._particles:
            painter.save()
            progress = particle.age / particle.lifetime
            painter.setOpacity(min(1.0, max(0.0, (1.0 - progress) * 1.5)))
            painter.translate(particle.x, particle.y)
            painter.rotate(particle.angle if particle.kind != "text" else 0)
            color = QColor("#A977CD" if particle.kind == "note" else "#E79864")
            painter.setPen(QPen(color.darker(110), 1.3))
            painter.setBrush(color)
            size = particle.size
            if particle.kind == "leaf":
                path = QPainterPath()
                points = [(0, -1), (.22, -.5), (.62, -.66), (.48, -.25),
                          (1, -.18), (.58, .18), (.7, .54), (.18, .4),
                          (0, .75), (-.18, .4), (-.7, .54), (-.58, .18),
                          (-1, -.18), (-.48, -.25), (-.62, -.66), (-.22, -.5)]
                path.moveTo(points[0][0] * size, points[0][1] * size)
                for px, py in points[1:]:
                    path.lineTo(px * size, py * size)
                path.closeSubpath()
                painter.drawPath(path)
                painter.drawLine(QPointF(0, size * .3), QPointF(size * .1, size))
            elif particle.kind == "note":
                painter.drawEllipse(QPointF(-size * .3, size * .35), size * .32, size * .23)
                painter.drawLine(QPointF(0, size * .35), QPointF(0, -size * .8))
                painter.drawLine(QPointF(0, -size * .8), QPointF(size * .5, -size * .55))
            elif particle.kind in {'star', 'heart'}:
                path = QPainterPath()
                if particle.kind == 'star':
                    for index in range(10):
                        angle = index * math.pi / 5 - math.pi / 2
                        radius = size * (1 if index % 2 == 0 else .42)
                        point = QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
                        path.moveTo(point) if index == 0 else path.lineTo(point)
                    path.closeSubpath()
                elif particle.kind == 'heart':
                    painter.setBrush(QColor('#ed9aac'))
                    path.moveTo(0, size * .8)
                    path.cubicTo(-size * 1.5, -size * .1, -size * .6, -size * 1.3, 0, -size * .45)
                    path.cubicTo(size * .6, -size * 1.3, size * 1.5, -size * .1, 0, size * .8)
                painter.drawPath(path)
            elif particle.kind in {'bubble', 'petal', 'dust'}:
                painter.setBrush(QColor('#bfdfee') if particle.kind == 'bubble' else QColor('#ed9aac') if particle.kind == 'petal' else QColor('#dfc7a3'))
                painter.drawEllipse(QPointF(0, 0), size, size * (.5 if particle.kind == 'petal' else 1))
            else:
                font = QFont("Microsoft YaHei")
                font.setPixelSize(round(12 * max(.4, self.width() / 220)))
                painter.setFont(font)
                painter.drawText(QPointF(-size * .5, size * .5), 'z' if particle.kind == 'sleep' else particle.text)
            painter.restore()
