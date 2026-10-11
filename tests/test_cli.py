import socket

from box_box.cli import main


def test_taken_port_gives_help_instead_of_a_traceback(capsys, tmp_path) -> None:
    squatter = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    squatter.bind(("0.0.0.0", 0))
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
