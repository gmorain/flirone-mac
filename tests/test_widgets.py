"""SliderField must behave like the spin box it replaces.

Sliders are integer-valued, so the real range is scaled internally. These guard
the conversion, because a silent factor error would misalign every image.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from flirone.ui.widgets import SliderField


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_round_trips_a_fractional_value(app):
    field = SliderField(0.0, 1.0, step=0.01, decimals=2)
    field.setValue(0.6)
    assert field.value() == pytest.approx(0.6)


def test_round_trips_a_fine_scale(app):
    field = SliderField(0.8, 1.6, step=0.001, decimals=3)
    field.setValue(1.2379)
    assert field.value() == pytest.approx(1.238, abs=0.0005)


@pytest.mark.parametrize("offset", [-150, -25, 0, 24, 150])
def test_round_trips_integer_offsets(app, offset):
    field = SliderField(-150, 150, step=1, decimals=0)
    field.setValue(offset)
    assert field.value() == offset


def test_clamps_to_its_range(app):
    field = SliderField(-150, 150, step=1, decimals=0)
    field.setValue(9999)
    assert field.value() == 150
    field.setValue(-9999)
    assert field.value() == -150


def test_emits_on_change(app):
    field = SliderField(0.0, 1.0, step=0.01, decimals=2)
    seen = []
    field.valueChanged.connect(seen.append)
    field.setValue(0.25)
    assert seen == [pytest.approx(0.25)]


def test_block_signals_suppresses_emission(app):
    """The sync helpers set values without retriggering a render."""
    field = SliderField(0.0, 1.0, step=0.01, decimals=2)
    seen = []
    field.valueChanged.connect(seen.append)
    field.blockSignals(True)
    field.setValue(0.4)
    field.blockSignals(False)
    assert seen == []
    assert field.value() == pytest.approx(0.4)


def test_readout_shows_the_value(app):
    field = SliderField(0.0, 1.0, step=0.01, decimals=2)
    field.setValue(0.6)
    assert field.readout.text() == "0.60"


def test_offsets_read_signed(app):
    field = SliderField(-150, 150, step=1, decimals=0)
    field.setValue(24)
    assert field.readout.text() == "+24"
    field.setValue(-17)
    assert field.readout.text() == "-17"


def test_arrow_key_step_is_one_real_unit(app):
    """Dragging explores; the keyboard is what aligns to the pixel."""
    field = SliderField(-150, 150, step=1, decimals=0)
    field.setValue(10)
    field.slider.setValue(field.slider.value() + field.slider.singleStep())
    assert field.value() == 11


class TestClickStepping:
    """Clicking the groove of an offset slider nudges by one.

    The native macOS style jumps to the clicked position, which throws away a
    careful alignment on a stray click.
    """

    @staticmethod
    def click(slider, x):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        point = QPointF(x, slider.height() / 2)
        slider.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                point,
                point,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def field(self, app, **kwargs):
        f = SliderField(-150, 150, step=1, decimals=0, **kwargs)
        f.slider.resize(200, 20)
        f.setValue(0)
        return f

    def test_click_right_of_the_handle_adds_one(self, app):
        f = self.field(app, step_on_click=True)
        self.click(f.slider, 190)
        assert f.value() == 1

    def test_click_left_of_the_handle_subtracts_one(self, app):
        f = self.field(app, step_on_click=True)
        self.click(f.slider, 10)
        assert f.value() == -1

    def test_a_far_click_still_moves_only_one(self, app):
        """The whole point: distance from the handle must not matter."""
        f = self.field(app, step_on_click=True)
        for _ in range(3):
            self.click(f.slider, 199)
        assert f.value() == 3

    def test_without_the_option_the_native_behaviour_is_kept(self, app):
        from PySide6.QtWidgets import QSlider

        f = self.field(app)
        assert not isinstance(f.slider, type(self.field(app, step_on_click=True).slider))
        assert isinstance(f.slider, QSlider)

    def test_stepping_still_respects_the_range(self, app):
        f = self.field(app, step_on_click=True)
        f.setValue(150)
        self.click(f.slider, 199)
        assert f.value() == 150
