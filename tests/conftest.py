"""Default checks are offline; simulator mutation requires an explicit flag."""
import pytest


def pytest_addoption(parser):
    parser.addoption("--run-live-simulator", action="store_true", default=False,
                     help="Allow marked tests to reset and drive the running simulator")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live-simulator"):
        return
    skip = pytest.mark.skip(reason="Live simulator mutation requires --run-live-simulator")
    for item in items:
        if item.get_closest_marker("live_simulator"):
            item.add_marker(skip)
