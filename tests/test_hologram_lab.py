"""Offline Hologram Lab tests: rendering only, no Live session or audio."""

import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from friday.ui.desktop import DesktopWindow, VoiceOrb
from friday.ui.hologram_lab import animation_speed, paint_orbital_lab


def _frame_hash(state: str, phase: float, *, particles: bool = False) -> str:
    canvas = QImage(338, 338, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    painter.scale(338 / 184, 338 / 184)
    paint_orbital_lab(painter, phase=phase, state=state, particles=particles)
    painter.end()
    return hashlib.sha256(canvas.bits().tobytes()).hexdigest()


def test_orbital_lab_renders_deterministically_and_tracks_real_states():
    assert _frame_hash("Ready", 1.0) == _frame_hash("Ready", 1.0)
    assert _frame_hash("Ready", 1.0) != _frame_hash("Listening", 1.0)
    assert _frame_hash("Responding", 1.0) != _frame_hash("Responding", 2.0)
    assert _frame_hash("Disconnected", 0.0) != _frame_hash("Ready", 0.0)
    assert animation_speed("Disconnected") == 0
    assert animation_speed("Ready") > 0
    assert animation_speed("Responding") > animation_speed("Ready")


def test_particle_layer_is_opt_in_deterministic_and_not_visible_offline():
    original = _frame_hash("Ready", 1.0)
    enabled = _frame_hash("Ready", 1.0, particles=True)
    assert original != enabled
    assert enabled == _frame_hash("Ready", 1.0, particles=True)
    assert enabled != _frame_hash("Ready", 2.0, particles=True)
    assert _frame_hash("Disconnected", 1.0, particles=True) == _frame_hash(
        "Disconnected", 1.0
    )
    assert _frame_hash("Disconnecting", 1.0, particles=True) == _frame_hash(
        "Disconnecting", 1.0
    )
    assert _frame_hash("Responding", 1.0, particles=True) != _frame_hash(
        "Ready", 1.0, particles=True
    )


def test_experimental_animation_is_local_and_pauses_when_hidden():
    app = QApplication.instance() or QApplication([])
    orb = VoiceOrb(diameter=338, cinematic=True, hologram=True)
    try:
        assert app is not None
        assert not orb._experimental
        orb._timer.stop()  # Control the renderer deterministically.
        orb.set_state("Ready")
        orb.set_experimental(True)
        assert orb._experimental
        orb._animate()
        assert orb._lab_phase == 0.0  # Hidden widget does no animation work.
        orb.show()
        app.processEvents()
        orb._animate()
        assert orb._lab_phase > 0.0  # Ready has a slow decorative pulse.
        prior = orb._lab_phase
        orb.set_state("Disconnected")
        orb._animate()
        assert orb._lab_phase == prior

        # Classic uses its original timer phase and still has no idle animation.
        orb.set_experimental(False)
        assert not orb._experimental
        prior_classic = orb._phase
        orb.set_state("Ready")
        orb._animate()
        assert orb._phase == prior_classic
        orb.set_state("Listening")
        orb._animate()
        assert orb._phase > prior_classic
        orb.hide()
        prior_classic = orb._phase
        orb._animate()
        assert orb._phase == prior_classic
    finally:
        orb.close()


def test_focus_can_switch_classic_and_experimental_without_starting_session():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        window.show()
        app.processEvents()
        window.orb._timer.stop()
        assert window.page_title.text() == "Voice"
        assert window._worker is None
        assert window._scheduler is None
        assert not window.experimental_hologram_action.isChecked()
        assert not window.orb._experimental
        assert not window.energy_particles_action.isEnabled()
        assert not window.energy_particles_action.isChecked()
        classic_image = window.orb.grab().toImage()
        classic = hashlib.sha256(classic_image.bits().tobytes()).hexdigest()

        window.experimental_hologram_action.trigger()
        app.processEvents()
        assert window.experimental_hologram_action.isChecked()
        assert window.orb._experimental
        assert window.energy_particles_action.isEnabled()
        assert not window.orb._particles
        experimental = hashlib.sha256(
            window.orb.grab().toImage().bits().tobytes()
        ).hexdigest()
        assert experimental != classic
        window.energy_particles_action.trigger()
        app.processEvents()
        assert window.energy_particles_action.isChecked()
        assert window.orb._particles
        # Offscreen Qt may reuse the previous widget backing image. The
        # deterministic QImage test above checks actual particle pixels.
        window.energy_particles_action.trigger()
        app.processEvents()
        assert not window.energy_particles_action.isChecked()
        assert not window.orb._particles
        assert window._worker is None
        assert not window.mic_button.isEnabled()
        assert not window.web_option.isChecked()

        window.experimental_hologram_action.trigger()
        app.processEvents()
        assert not window.experimental_hologram_action.isChecked()
        assert not window.orb._experimental
        assert not window.orb._particles
        assert not window.energy_particles_action.isChecked()
        assert not window.energy_particles_action.isEnabled()
        # Offscreen Qt may return padding/backing bytes that differ between
        # grabs. Compare an opaque painted pixel and the restored mode instead
        # of asserting byte-identical native pixmap backing storage.
        restored_image = window.orb.grab().toImage()
        assert restored_image.pixelColor(169, 169) == classic_image.pixelColor(169, 169)
        window._show_voice_context("academic")
        assert window._voice_panel == "academic"
        assert window._worker is None
        assert window._scheduler is None
    finally:
        window.close()
