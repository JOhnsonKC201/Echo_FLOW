"""A dead default microphone must not cost the user their dictation.

Windows promotes any newly plugged USB audio device to default input. When
that device's mic hears nothing (a speaker dongle, a muted headset), a recorder
that only follows the default records silence and every dictation is dropped as
"too quiet". The recorder therefore also listens on the other real microphones
and hands back the take that heard the user."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from src import audio
from src.audio import AudioConfig, Recorder, Take, backup_inputs, choose_take

MME, WASAPI = 0, 1

# The device table of the machine this bug was found on, trimmed.
DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "hostapi": MME, "max_input_channels": 2},
    {"name": "Microphone (YF USB Audio)", "hostapi": MME, "max_input_channels": 1},
    {"name": "Microphone Array (Realtek(R) Au", "hostapi": MME, "max_input_channels": 2},
    {"name": "Stereo Mix (Realtek(R) Audio)", "hostapi": MME, "max_input_channels": 2},
    {"name": "Speakers (YF USB Audio)", "hostapi": MME, "max_input_channels": 0},
    {"name": "Microphone Array (Realtek(R) Audio)", "hostapi": WASAPI, "max_input_channels": 2},
]
USB, ARRAY = 1, 2

SPEECH = np.full((480, 1), 0.05, dtype=np.float32)
HISS = np.full((480, 1), 0.0005, dtype=np.float32)


class _Stream:
    def __init__(self, sd, **kw):
        self.sd, self.device, self.callback = sd, kw["device"], kw["callback"]
        self.started = self.closed = False
        if self.device in sd.broken:
            raise RuntimeError("device busy")
        sd.streams.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def feed(self, block, times=20):
        for _ in range(times):
            self.callback(block, len(block), None, None)


def _fake_sd(devices=DEVICES, default=USB):
    sd = types.ModuleType("sounddevice")
    sd.devices, sd.streams, sd.broken = list(devices), [], set()
    sd.default = types.SimpleNamespace(device=[default, 4])
    sd.query_devices = lambda index=None: sd.devices if index is None else sd.devices[index]
    sd.query_hostapis = lambda index: {"name": "MME" if index == MME else "Windows WASAPI"}
    sd.InputStream = lambda **kw: _Stream(sd, **kw)
    return sd


@pytest.fixture
def sd(monkeypatch):
    fake = _fake_sd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def _recorder(**cfg):
    return Recorder(AudioConfig(vad_enabled=False, **cfg))


def _stream_for(sd, device):
    return next(s for s in sd.streams if s.device == device)


# --- which devices count as a backup microphone ---------------------------

def test_backups_are_the_other_real_mics_on_the_same_host_api(sd):
    assert backup_inputs(sd) == [(ARRAY, "Microphone Array (Realtek(R) Au")]


def test_backups_never_include_a_device_that_records_the_speakers(sd):
    """A silent mic rescued by Stereo Mix would paste whatever video was
    playing as if the user had dictated it."""
    names = [name for _, name in backup_inputs(sd)]
    assert not any("Stereo Mix" in n for n in names)


def test_no_backups_when_the_device_table_cannot_be_read():
    bare = types.ModuleType("sounddevice")  # no query_devices at all
    assert backup_inputs(bare) == []


# --- which take wins -------------------------------------------------------

def _take(name, is_default, level):
    block = np.full(1600, level, dtype=np.float32)
    return Take(name, is_default, block, audio.rms(block))


def test_the_default_mic_wins_whenever_it_heard_something():
    takes = [_take("usb", True, 0.01), _take("array", False, 0.2)]
    assert choose_take(takes).device == "usb"


def test_a_silent_default_loses_to_a_backup_that_heard_speech():
    takes = [_take("usb", True, 0.0005), _take("array", False, 0.05)]
    assert choose_take(takes).device == "array"


def test_all_silent_returns_the_default_so_the_drop_names_the_right_mic():
    takes = [_take("usb", True, 0.0005), _take("array", False, 0.0002)]
    assert choose_take(takes).device == "usb"


# --- the recorder end to end ----------------------------------------------

def test_start_listens_on_the_default_and_on_the_backup(sd):
    rec = _recorder()
    rec.start()
    assert sorted(str(s.device) for s in sd.streams) == ["2", "None"]
    assert all(s.started for s in sd.streams)
    rec.stop()
    assert all(s.closed for s in sd.streams)


def test_a_dead_default_mic_is_rescued_by_the_backup(sd):
    rec = _recorder()
    rec.start()
    _stream_for(sd, None).feed(HISS)
    _stream_for(sd, ARRAY).feed(SPEECH)

    out = rec.stop()

    assert audio.rms(out) == pytest.approx(0.05, rel=1e-3)
    cap = rec.last_capture
    assert cap.device == "Microphone Array (Realtek(R) Au"
    assert cap.rescued_from == "Microphone (YF USB Audio)"
    assert cap.levels["Microphone (YF USB Audio)"] < audio.SILENCE_RMS


def test_a_working_default_mic_is_used_and_nothing_is_reported(sd):
    rec = _recorder()
    rec.start()
    _stream_for(sd, None).feed(SPEECH)
    _stream_for(sd, ARRAY).feed(SPEECH)

    rec.stop()

    assert rec.last_capture.device == "Microphone (YF USB Audio)"
    assert rec.last_capture.rescued_from is None


def test_a_pinned_device_is_recorded_alone(sd):
    """Choosing a device in config is an instruction, not a hint."""
    rec = _recorder(device=ARRAY)
    rec.start()
    assert [s.device for s in sd.streams] == [ARRAY]
    rec.stop()


def test_fallback_can_be_switched_off(sd):
    rec = _recorder(fallback_mics=False)
    rec.start()
    assert [s.device for s in sd.streams] == [None]
    rec.stop()


def test_a_backup_that_will_not_open_does_not_stop_the_recording(sd):
    sd.broken.add(ARRAY)
    rec = _recorder()
    rec.start()
    _stream_for(sd, None).feed(SPEECH)

    out = rec.stop()

    assert out.size == 20 * 480
    assert rec.last_capture.device == "Microphone (YF USB Audio)"


def test_stop_survives_a_backup_stream_that_raises_on_close(sd):
    rec = _recorder()
    rec.start()
    _stream_for(sd, None).feed(SPEECH)

    def boom():
        raise RuntimeError("PortAudio: device unplugged")
    _stream_for(sd, ARRAY).stop = boom

    assert rec.stop().size == 20 * 480
    rec.start()  # and the recorder is not wedged
    rec.stop()


# --- a microphone plugged in while the daemon is running -------------------

def test_a_changed_device_count_refreshes_the_list_before_recording(sd, monkeypatch):
    """PortAudio snapshots devices at startup. After a hot plug the old
    indexes can name a different device, so re-read before opening."""
    calls = []
    sd._terminate = lambda: calls.append("terminate")
    sd._initialize = lambda: calls.append("initialize")
    # The fake table holds three MME inputs besides the Sound Mapper alias;
    # Windows now reports a fourth.
    monkeypatch.setattr(audio, "_system_input_count", lambda: 4)

    rec = _recorder()
    rec.start()
    rec.stop()

    assert calls == ["terminate", "initialize"]


def test_an_unchanged_device_count_leaves_portaudio_alone(sd, monkeypatch):
    calls = []
    sd._terminate = lambda: calls.append("terminate")
    sd._initialize = lambda: calls.append("initialize")
    monkeypatch.setattr(audio, "_system_input_count", lambda: 3)

    rec = _recorder()
    rec.start()
    rec.stop()

    assert calls == []
