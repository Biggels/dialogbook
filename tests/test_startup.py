"""Starting the app when the port is taken.

uvicorn reports a bind failure *after* printing "Application startup complete", so
without a check up front the user sees a link that was never going to work, followed by
a WinError 10048 several lines later. Worth catching early — the usual cause is a
previous run that did not exit.
"""
import socket

import pytest

from dialogbook.app import describe_listener, main, port_in_use


@pytest.fixture
def taken_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen()
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def test_port_in_use_detects_a_listener(taken_port):
    assert port_in_use('127.0.0.1', taken_port) is True


def test_port_in_use_is_false_once_it_is_free(taken_port):
    free = taken_port
    assert port_in_use('127.0.0.1', free) is True
    # The fixture's socket closes at teardown; bind a fresh one to find a definitely-free port.
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    assert port_in_use('127.0.0.1', port) is False


def test_describe_listener_names_the_process(taken_port):
    "The message should say what is squatting on the port, not just that something is."
    desc = describe_listener(taken_port)
    assert 'pid' in desc and desc.strip(), desc


def test_describe_listener_stays_on_one_short_line(taken_port):
    "A `python -c` holder carries its entire script in argv; that must not land in the error."
    desc = describe_listener(taken_port)
    assert '\n' not in desc and len(desc) < 110, desc


def test_main_exits_with_a_clear_message_instead_of_a_bind_error(taken_port, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv('DIALOGBOOK_PORT', str(taken_port))
    monkeypatch.setenv('DIALOGBOOK_WORKSPACE', str(tmp_path))

    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 1

    out = capsys.readouterr().out
    assert f'port {taken_port} is already in use' in out
    assert 'DIALOGBOOK_PORT' in out, 'the message should say how to pick another port'
    assert 'http://' not in out, 'no link that was never going to work'
