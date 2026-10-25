"""Unit tests for src/singleton.py: the single-instance TCP lock and the PID /
stop-flag files the watchdog reads.

Nothing here binds the real lock port. Each test points the module at a port
the OS picks (0) or a freshly discovered free one, and at files under tmp_path.
Every socket the module opens is tracked and closed on teardown.
"""
from __future__ import annotations

import os
import socket
import threading
import types

import pytest

from src import singleton

pytestmark = pytest.mark.unit

RACERS = 8
JOIN_TIMEOUT_SECONDS = 10


class LogSpy:
    """Records the messages passed to _log.exception()."""

    def __init__(self):
        self.messages: list[str] = []

    def exception(self, msg, *args, **kwargs):
        self.messages.append(str(msg))


@pytest.fixture
def lock(monkeypatch, tmp_path):
    """The module pointed at tmp_path files and an OS-chosen port, with every
    socket it creates tracked so none outlives the test."""
    data = tmp_path / "data"
    made: list[socket.socket] = []

    class Tracked(socket.socket):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(singleton, "socket", types.SimpleNamespace(
        socket=Tracked, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM))
    monkeypatch.setattr(singleton, "_PID_FILE", data / "wispr.pid")
    monkeypatch.setattr(singleton, "_STOP_FLAG", data / "wispr.stop")
    monkeypatch.setattr(singleton, "_LOCK_PORT", 0)
    monkeypatch.setattr(singleton, "_lock_socket", None)
    yield types.SimpleNamespace(data=data, pid=data / "wispr.pid", stop=data / "wispr.stop")
    for s in made:
        s.close()


@pytest.fixture
def log_spy(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(singleton, "_log", spy)
    return spy


def held_port() -> int:
    return singleton._lock_socket.getsockname()[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def outside_holder():
    """A listening socket the module did not create, standing in for another
    running instance."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s
    s.close()


# --- module constants -------------------------------------------------------

def test_default_port_is_unprivileged_and_the_two_files_share_a_directory():
    assert 1024 <= singleton._LOCK_PORT <= 65535
    assert singleton._PID_FILE.parent == singleton._STOP_FLAG.parent
    assert singleton._PID_FILE != singleton._STOP_FLAG


# --- acquire_or_exit: first instance ---------------------------------------

def test_first_instance_listens_on_loopback_only(lock):
    singleton.acquire_or_exit()
    sock = singleton._lock_socket
    assert sock is not None
    assert sock.getsockname()[0] == "127.0.0.1"
    # A second process would see the port as taken: connecting succeeds.
    socket.create_connection(("127.0.0.1", held_port()), timeout=2).close()


def test_first_instance_writes_its_pid_and_creates_the_data_dir(lock):
    assert not lock.data.exists()
    singleton.acquire_or_exit()
    assert lock.pid.read_text() == str(os.getpid())


def test_a_stale_stop_flag_is_cleared_on_start(lock):
    lock.data.mkdir()
    lock.stop.write_text("123")
    singleton.acquire_or_exit()
    assert not lock.stop.exists()


def test_start_without_a_stop_flag_is_fine(lock):
    singleton.acquire_or_exit()
    assert not lock.stop.exists()
    assert singleton._lock_socket is not None


# --- acquire_or_exit: second instance --------------------------------------

def test_second_instance_prints_a_message_and_exits_zero(lock, monkeypatch, capsys):
    singleton.acquire_or_exit()
    first = singleton._lock_socket
    monkeypatch.setattr(singleton, "_LOCK_PORT", held_port())
    with pytest.raises(SystemExit) as e:
        singleton.acquire_or_exit()
    assert e.value.code == 0
    assert "already running" in capsys.readouterr().out
    assert singleton._lock_socket is first, "the loser must not replace the winner's lock"


def test_a_losing_start_leaves_the_running_instances_files_alone(
        lock, monkeypatch, outside_holder):
    lock.data.mkdir()
    lock.pid.write_text("4242")
    lock.stop.write_text("4242")
    monkeypatch.setattr(singleton, "_LOCK_PORT", outside_holder.getsockname()[1])
    with pytest.raises(SystemExit):
        singleton.acquire_or_exit()
    assert lock.pid.read_text() == "4242"
    assert lock.stop.exists()
    assert singleton._lock_socket is None


def test_the_lock_is_free_again_once_the_holder_closes_its_socket(lock, monkeypatch):
    singleton.acquire_or_exit()
    port = held_port()
    singleton._lock_socket.close()
    singleton._lock_socket = None
    monkeypatch.setattr(singleton, "_LOCK_PORT", port)
    singleton.acquire_or_exit()
    assert held_port() == port


def test_only_one_of_many_simultaneous_starts_gets_the_lock(lock, monkeypatch):
    monkeypatch.setattr(singleton, "_LOCK_PORT", free_port())
    barrier = threading.Barrier(RACERS)
    outcomes: list[str] = []

    def start():
        barrier.wait(timeout=JOIN_TIMEOUT_SECONDS)
        try:
            singleton.acquire_or_exit()
            outcomes.append("won")
        except SystemExit:
            outcomes.append("lost")

    threads = [threading.Thread(target=start) for _ in range(RACERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT_SECONDS)
    assert sorted(outcomes) == ["lost"] * (RACERS - 1) + ["won"]


# --- acquire_or_exit: file failures never cost the lock --------------------

def test_an_unwritable_pid_location_is_logged_and_the_lock_is_still_held(
        lock, monkeypatch, tmp_path, log_spy):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the data dir should be")
    monkeypatch.setattr(singleton, "_PID_FILE", blocker / "wispr.pid")
    singleton.acquire_or_exit()
    assert singleton._lock_socket is not None
    assert len(log_spy.messages) == 1 and "_write_pid" in log_spy.messages[0]


def test_a_stop_flag_that_cannot_be_removed_is_logged_and_the_lock_is_still_held(
        lock, log_spy):
    lock.stop.mkdir(parents=True)          # unlink() on a directory fails
    singleton.acquire_or_exit()
    assert singleton._lock_socket is not None
    assert lock.pid.read_text() == str(os.getpid())
    assert len(log_spy.messages) == 1 and "_clear_stop_flag" in log_spy.messages[0]


# --- request_stop -----------------------------------------------------------

def test_request_stop_writes_the_pid_and_creates_the_data_dir(lock):
    assert not lock.data.exists()
    singleton.request_stop()
    assert lock.stop.read_text() == str(os.getpid())


def test_request_stop_replaces_an_older_flag_and_is_repeatable(lock):
    lock.data.mkdir()
    lock.stop.write_text("old and longer than a pid")
    singleton.request_stop()
    singleton.request_stop()
    assert lock.stop.read_text() == str(os.getpid())


def test_request_stop_forces_the_flag_to_disk_before_returning(lock, monkeypatch):
    # The tray-quit path calls os._exit() right after, which skips write-back.
    calls: list[int] = []
    real_fsync = os.fsync

    def spy(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(singleton.os, "fsync", spy)
    singleton.request_stop()
    assert len(calls) == 1
    assert lock.stop.read_text() == str(os.getpid())


def test_request_stop_leaves_the_pid_file_for_the_watchdog(lock):
    # Quit vs crash is told apart by the flag, so the PID must survive a quit.
    singleton.acquire_or_exit()
    singleton.request_stop()
    assert lock.pid.read_text() == str(os.getpid())
    assert lock.stop.exists()


def test_request_stop_failure_is_logged_not_raised(lock, monkeypatch, tmp_path, log_spy):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the data dir should be")
    monkeypatch.setattr(singleton, "_STOP_FLAG", blocker / "wispr.stop")
    assert singleton.request_stop() is None
    assert len(log_spy.messages) == 1 and "request_stop" in log_spy.messages[0]


def test_a_deliberate_quit_then_a_fresh_start_is_seen_as_running_again(lock):
    singleton.acquire_or_exit()
    singleton.request_stop()
    assert lock.stop.exists()
    singleton._lock_socket.close()
    singleton._lock_socket = None
    singleton.acquire_or_exit()
    assert not lock.stop.exists()
