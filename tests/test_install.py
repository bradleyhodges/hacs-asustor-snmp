"""Verify installer staging, backup and rollback in temporary directories."""

from pathlib import Path
from unittest.mock import patch

import pytest

from install import install


@pytest.fixture
def paths(tmp_path):
    config = tmp_path / "ha"
    config.mkdir()
    (config / "configuration.yaml").write_text("# preserve me\n")
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text('{"domain":"asustor_snmp"}')
    (source / "sensor.py").write_text("# new\n")
    return config, source


def test_first_install_and_upgrade_backup(paths):
    config, source = paths
    assert install(config, source) is None
    target = config / "custom_components/asustor_snmp"
    (source / "sensor.py").write_text("# newer\n")
    backup = install(config, source)
    assert (backup / "sensor.py").read_text() == "# new\n"
    assert (target / "sensor.py").read_text() == "# newer\n"
    assert (config / "configuration.yaml").read_text() == "# preserve me\n"


def test_replacement_failure_restores_previous_install(paths):
    config, source = paths
    install(config, source)
    (source / "sensor.py").write_text("# failed upgrade\n")
    original = Path.rename

    def rename(path, target):
        if "-stage-" in str(path):
            raise OSError("simulated replacement failure")
        return original(path, target)

    with patch.object(Path, "rename", rename), pytest.raises(OSError):
        install(config, source)
    assert (config / "custom_components/asustor_snmp/sensor.py").read_text() == "# new\n"
    assert not list((config / "custom_components").glob(".*-install.lock"))


def test_wrong_directory_is_rejected(paths, tmp_path):
    _, source = paths
    with pytest.raises(ValueError, match="configuration"):
        install(tmp_path, source)
