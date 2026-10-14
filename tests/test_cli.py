import socket

from box_box.cli import main


def test_taken_port_gives_help_instead_of_a_traceback(capsys, tmp_path) -> None:
    squatter = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    squatter.bind(("127.0.0.1", 0))
    port = squatter.getsockname()[1]
    try:
        assert main(["watch", "--port", str(port)]) == 1
        err = capsys.readouterr().err
        assert f"UDP port {port}" in err and "netstat" in err and f"box-box watch --port {port + 1}" in err

        out = tmp_path / "x.f1raw"
        assert main(["record", "--port", str(port), "--out", str(out)]) == 1
        assert not out.exists()  # fails before creating an empty recording
    finally:
        squatter.close()


def test_verbose_works_after_the_subcommand(monkeypatch) -> None:
    from box_box import cli

    seen = []
    monkeypatch.setattr(cli, "run", lambda open_browser, verbose: seen.append(verbose) or 0)
    for argv in (["-v", "run"], ["run", "-v"], ["run"], []):
        main(argv)
    assert seen == [True, True, False, False]


def test_already_running_finds_the_fallback_port(monkeypatch) -> None:
    from box_box import cli

    monkeypatch.setattr(cli, "_answers", lambda port: port == 8779)
    assert cli._already_running(8777) == 8779
    monkeypatch.setattr(cli, "_answers", lambda port: False)
    assert cli._already_running(8777) is None
