"""Zooming must not move the measurements.

Every coordinate conversion goes through one rect, so these guard the property
that matters: a sensor pixel maps to a widget point and back unchanged, at any
magnification or pan.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from flirone.measure import MeasurementSet
from flirone.ui.imageview import MAX_ZOOM, MIN_ZOOM, ThermalView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    v = ThermalView(MeasurementSet())
    v.resize(900, 600)
    v._sensor_size = (480, 640)
    return v


@pytest.mark.parametrize("zoom", [1.0, 1.5, 2.0, 4.0, 8.0, 16.0])
@pytest.mark.parametrize("sensor", [(0, 0), (239, 319), (479, 639), (100, 500)])
def test_round_trip_is_exact_at_every_zoom(view, zoom, sensor):
    view._zoom = zoom
    assert view._to_sensor(view._to_widget(*sensor)) == sensor


@pytest.mark.parametrize("pan", [(0, 0), (60, -40), (-120, 90)])
def test_round_trip_is_exact_under_pan(view, pan):
    view._zoom = 4.0
    view._pan = QPointF(*pan)
    for sensor in ((0, 0), (239, 319), (479, 639)):
        assert view._to_sensor(view._to_widget(*sensor)) == sensor


@pytest.mark.parametrize("cursor", [(300, 200), (700, 480), (450, 300)])
def test_zoom_keeps_the_point_under_the_cursor(view, cursor):
    """Otherwise the image slides away from whatever you were inspecting."""
    anchor = QPointF(*cursor)
    before = view._to_sensor(anchor)
    view.set_zoom(4.0, anchor)
    assert view._to_sensor(anchor) == before


def test_zoom_is_clamped(view):
    view.set_zoom(1000.0)
    assert view.zoom == MAX_ZOOM
    view.set_zoom(0.01)
    assert view.zoom == MIN_ZOOM


def test_reset_restores_fit(view):
    view.set_zoom(6.0)
    view._pan = QPointF(50, 50)
    view.reset_view()
    assert view.zoom == 1.0
    assert view._pan.isNull()
    assert view._target_rect() == view._fit_rect()


def test_pan_cannot_lose_the_image(view):
    view.set_zoom(4.0)
    view._pan = QPointF(99999, 99999)
    view._clamp_pan()
    assert view._target_rect().intersects(view.rect())


def test_unzoomed_rect_is_the_fit_rect(view):
    assert view._target_rect() == view._fit_rect()


def test_zoom_changed_is_emitted(view):
    seen = []
    view.zoom_changed.connect(seen.append)
    view.set_zoom(2.0)
    view.reset_view()
    assert seen == [2.0, 1.0]


class TestPanning:
    """Left-drag has to pan without stealing the measurement gestures."""

    def test_left_drag_pans_in_cursor_mode_over_empty_space(self, view):
        from flirone.ui.imageview import TOOL_NONE

        view.tool = TOOL_NONE
        assert view._can_pan_with_left(100, 100)

    def test_left_drag_does_not_pan_when_a_tool_is_active(self, view):
        from flirone.ui.imageview import TOOL_BOX, TOOL_LINE, TOOL_SPOT

        for tool in (TOOL_SPOT, TOOL_BOX, TOOL_LINE):
            view.tool = tool
            assert not view._can_pan_with_left(100, 100)

    def test_left_drag_does_not_pan_when_grabbing_a_spot(self, view):
        """Moving an existing spot must still work in Cursor mode."""
        from flirone.measure import Spot
        from flirone.ui.imageview import TOOL_NONE

        view.tool = TOOL_NONE
        view.measurements.spots.append(Spot(100, 100))
        assert not view._can_pan_with_left(102, 101)
        assert view._can_pan_with_left(300, 300)

    def test_panning_moves_the_image(self, view):
        view.set_zoom(4.0)
        before = view._target_rect().x()
        view._begin_pan(QPointF(400, 300))
        view._pan = QPointF(view._pan.x() + 40, view._pan.y())
        view._clamp_pan()
        assert view._target_rect().x() == before + 40

    def test_pan_does_not_move_measurements_relative_to_the_image(self, view):
        """Panning changes where the image sits, not what a pixel means."""
        view.set_zoom(4.0)
        widget_before = view._to_widget(200, 300)
        view._pan = QPointF(view._pan.x() + 50, view._pan.y() + 30)
        widget_after = view._to_widget(200, 300)
        assert widget_after.x() == pytest.approx(widget_before.x() + 50, abs=1.5)
        assert view._to_sensor(widget_after) == (200, 300)
