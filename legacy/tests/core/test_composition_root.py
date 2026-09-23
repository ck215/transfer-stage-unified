"""S12 — the composition root and registry events (RC-9).

Two invariants:

* **I-9.1** `RedPercentSystem.available_probes` is a subset of the live
  registered probes at all times. It used to be a snapshot assigned by
  whichever launcher had just finished building, which nothing refreshed —
  so a released probe stayed in the dict, stayed selected, and the monitor
  thread went on reading its frozen last position into the data log
  (PYSIDE-3, STEPPER-13, REDPERCENT-11).
* **I-9.2** The three launchers produce identical managers for the same
  config. Tested through `normalize_config` + `build_models` directly,
  because that is now the whole of what a launcher does.

These use stub models rather than real probes on purpose: the invariant is
about the registry, and constructing a real `StepperProbe` costs a 1.5 s
bootloader wait (SERIAL-6) that would push this file into `slow`.
"""

import pytest

from app_bootstrap import (build_models, discover_controllers, link_models,
                           normalize_config, validate_assignment)
from model.redpercent_system import RedPercentSystem
from model.system_manager import SystemManager


class FakeProbe:
    """A position source: the duck-type Red Percent selects on."""

    def __init__(self, name="probe"):
        self.name = name
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.pos_z = 0.0
        self.torn_down = 0

    def teardown(self):
        self.torn_down += 1

    def emergency_stop(self):
        pass


class FakeDevice:
    """Registered, but not a position source."""

    def teardown(self):
        pass

    def emergency_stop(self):
        pass


@pytest.fixture
def linked():
    """A manager holding a bound Red Percent model, and nothing else yet."""
    manager = SystemManager()
    red = RedPercentSystem()
    manager.register("Red Percent Window", red)
    link_models(manager)
    yield manager, red
    red.unbind_registry()


# --- I-9.1 -----------------------------------------------------------------

def test_i_9_1_a_released_probe_leaves_available_probes(linked):
    manager, red = linked
    manager.register("Stepper Probe", FakeProbe())
    assert red.available_probes == {"Stepper Probe": manager.get_model("Stepper Probe")}

    manager.release("Stepper Probe")

    assert red.available_probes == {}
    assert red.stepper_model is None
    assert red.selected_probe_name is None


def test_i_9_1_shutdown_all_empties_the_probe_registry(linked):
    manager, red = linked
    manager.register("Stepper Probe", FakeProbe())
    manager.register("DC Probe", FakeProbe())
    assert len(red.available_probes) == 2

    manager.shutdown_all()

    assert red.available_probes == {}
    assert red.stepper_model is None


def test_a_probe_registered_after_red_percent_is_picked_up(linked):
    """PYSIDE-3: opening a probe after Red Percent left it unselected, because
    the linking block only ever ran once, at launch."""
    manager, red = linked
    assert red.get_available_probe_names() == []

    probe = manager.register("DC Probe", FakeProbe())

    assert red.stepper_model is probe
    assert red.selected_probe_name == "DC Probe"


def test_red_percent_built_after_its_probes_still_sees_them():
    """The other ordering. Each launcher assumed one of the two and the
    orderings were not the same in all three."""
    manager = SystemManager()
    probe = manager.register("Stepper Probe", FakeProbe())
    red = RedPercentSystem()
    manager.register("Red Percent Window", red)

    link_models(manager)

    assert red.available_probes == {"Stepper Probe": probe}
    assert red.stepper_model is probe
    red.unbind_registry()


def test_releasing_the_selected_probe_falls_back_to_a_live_one(linked):
    manager, red = linked
    manager.register("Stepper Probe", FakeProbe())
    dc = manager.register("DC Probe", FakeProbe())
    assert red.selected_probe_name == "Stepper Probe"

    manager.release("Stepper Probe")

    assert red.selected_probe_name == "DC Probe"
    assert red.stepper_model is dc


def test_releasing_an_unselected_probe_leaves_the_selection_alone(linked):
    manager, red = linked
    stepper = manager.register("Stepper Probe", FakeProbe())
    manager.register("DC Probe", FakeProbe())

    manager.release("DC Probe")

    assert red.selected_probe_name == "Stepper Probe"
    assert red.stepper_model is stepper


def test_a_non_position_model_is_not_a_probe(linked):
    manager, red = linked
    manager.register("Temperature Controller", FakeDevice())

    assert red.available_probes == {}
    assert red.get_available_probe_names() == []


def test_probe_names_come_back_in_device_registry_order(linked):
    """The "prefer the stepper" line each launcher carried is now an ordering
    over the registry, so the default selection does not depend on which
    frontend happened to enumerate its checkboxes in which order."""
    manager, red = linked
    manager.register("DC Probe", FakeProbe())
    manager.register("Stepper Probe", FakeProbe())

    assert red.get_available_probe_names() == ["Stepper Probe", "DC Probe"]


def test_a_launch_selects_the_first_probe_in_registry_order(linked):
    """What the launchers' `if "Stepper Probe" in probe_models` line did, for
    the case it was written for: everything registered in one pass."""
    manager, red = linked
    stepper = manager.register("Stepper Probe", FakeProbe())
    manager.register("DC Probe", FakeProbe())

    assert red.selected_probe_name == "Stepper Probe"
    assert red.stepper_model is stepper


def test_a_later_probe_does_not_steal_a_live_selection(linked):
    """Re-pointing the position source under a running monitor would put two
    probes' positions in one data log with nothing to say where the seam is.
    A selection changes when it has to — when its probe goes away — or when
    the operator changes it, never on its own."""
    manager, red = linked
    dc = manager.register("DC Probe", FakeProbe())
    manager.register("Stepper Probe", FakeProbe())

    assert red.selected_probe_name == "DC Probe"
    assert red.stepper_model is dc


def test_teardown_unbinds_from_the_registry():
    """A torn-down Red Percent must stop receiving events; otherwise the
    manager keeps a reference to it through the subscriber list."""
    manager = SystemManager()
    red = RedPercentSystem()
    manager.register("Red Percent Window", red)
    link_models(manager)

    red.teardown()
    manager.register("Stepper Probe", FakeProbe())

    assert red.available_probes == {}


def test_a_failing_subscriber_does_not_break_registration():
    manager = SystemManager()

    def boom(name, model):
        raise RuntimeError("subscriber is broken")

    manager.subscribe("registered", boom)
    model = manager.register("DC Probe", FakeProbe())

    assert manager.get_model("DC Probe") is model


def test_unsubscribe_stops_delivery():
    manager = SystemManager()
    seen = []
    callback = manager.subscribe("registered", lambda n, m: seen.append(n))
    manager.register("DC Probe", FakeProbe())
    manager.unsubscribe(callback)
    manager.register("Stepper Probe", FakeProbe())

    assert seen == ["DC Probe"]


def test_subscribe_refuses_an_unknown_event():
    with pytest.raises(ValueError):
        SystemManager().subscribe("reconfigured", lambda n, m: None)


# --- I-9.2 and normalization ----------------------------------------------

#: The same operator choices, expressed the way each frontend expresses them.
TK_RAW = [
    {"device": "Stepper Probe", "port": "Headless", "controller": "None",
     "enabled": True},
    {"device": "DC Probe", "port": "COM4", "controller": "ID 0: Pad",
     "enabled": True},
    {"device": "Temperature Controller", "port": "COM5", "controller": "None",
     "enabled": False},
]

WEB_RAW = {
    "Stepper Probe": {"port": "COM3", "controller_id": "None",
                      "mode": "simulation", "enabled": True},
    "DC Probe": {"port": "COM4", "controller_id": "ID 0: Pad",
                 "mode": "hardware", "enabled": True},
    "Temperature Controller": {"port": "COM5", "controller_id": "None",
                               "mode": "hardware", "enabled": False},
}


def test_i_9_2_every_frontends_shape_normalizes_to_the_same_configs():
    tk_configs = normalize_config(TK_RAW)
    web_configs = normalize_config(WEB_RAW)

    assert [c["device"] for c in tk_configs] == ["Stepper Probe", "DC Probe"]
    assert [(c["device"], c["port"], c["controller"]) for c in tk_configs] == \
           [(c["device"], c["port"], c["controller"]) for c in web_configs]


def test_i_9_2_the_same_configs_produce_the_same_manager(monkeypatch):
    from model import devices

    monkeypatch.setattr(devices, "build",
                        lambda name, port, ctrl, claims: FakeProbe(name))

    managers = []
    for raw in (TK_RAW, WEB_RAW):
        configs = normalize_config(raw)
        assert validate_assignment(configs) == []
        manager = SystemManager()
        build_models(configs, manager)
        link_models(manager)
        managers.append(manager)

    left, right = managers
    assert list(left.active_models) == list(right.active_models)
    assert left.configs == right.configs


def test_disabled_devices_are_not_constructed(monkeypatch):
    """WEB-4 / MANAGER-12. The web path built every device the wizard knew
    about, including the ones the operator had unchecked."""
    from model import devices

    built = []

    def _build(name, port, ctrl, claims):
        built.append(name)
        return FakeProbe(name)

    monkeypatch.setattr(devices, "build", _build)

    manager = SystemManager()
    build_models(normalize_config(WEB_RAW), manager)

    assert built == ["Stepper Probe", "DC Probe"]
    assert "Temperature Controller" not in manager.active_models


def test_normalize_maps_headless_and_simulation_mode_to_sim():
    configs = normalize_config([
        {"device": "Stepper Probe", "port": "Headless"},
        {"device": "DC Probe", "port": "COM4", "mode": "SIMULATION"},
        {"device": "Chuck Positioner", "port": "COM7", "mode": "hardware"},
    ])
    assert [c["port"] for c in configs] == ["SIM", "SIM", "COM7"]


def test_normalize_accepts_controller_id_or_controller():
    configs = normalize_config([
        {"device": "Stepper Probe", "port": "SIM", "controller_id": "ID 1: Pad"},
        {"device": "DC Probe", "port": "SIM", "controller": "ID 2: Pad"},
        {"device": "Chuck Positioner", "port": "SIM"},
    ])
    assert [c["controller"] for c in configs] == ["ID 1: Pad", "ID 2: Pad", "None"]


def test_normalize_refuses_a_shape_it_cannot_read():
    with pytest.raises(TypeError):
        normalize_config("Stepper Probe")
    with pytest.raises(TypeError):
        normalize_config(["Stepper Probe"])


def test_build_models_owns_its_claims_dict(monkeypatch):
    """MANAGER-16: Tk passed one dict to every launch and nothing cleared it,
    so a relaunch collided with claims held by models that no longer existed.
    PySide and Web each passed a fresh one — the divergence itself is the
    defect, so the dict belongs to the build."""
    from model import devices

    seen = []
    monkeypatch.setattr(devices, "build",
                        lambda name, port, ctrl, claims: (seen.append(claims),
                                                          FakeProbe(name))[1])

    configs = normalize_config([{"device": "Stepper Probe", "port": "SIM"}])
    build_models(configs, SystemManager())
    build_models(configs, SystemManager())

    assert seen[0] == {} and seen[1] == {}
    assert seen[0] is not seen[1]


# --- controller enumeration ------------------------------------------------

def test_discover_controllers_fabricates_nothing(monkeypatch):
    """WEB-15: the web wizard invented "Virtual Controller A"/"B" whenever its
    subprocess found nothing, and a device assigned one got no input."""
    from controller.input_service import input_service

    monkeypatch.setattr(input_service, "enumerate", lambda: [])
    assert discover_controllers() == ["None"]

    monkeypatch.setattr(input_service, "enumerate", lambda: [(0, "Pad"), (1, "Stick")])
    assert discover_controllers() == ["None", "ID 0: Pad", "ID 1: Stick"]


def test_discover_controllers_never_shells_out(monkeypatch):
    import subprocess

    def forbidden(*a, **kw):
        raise AssertionError("controller enumeration spawned a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    discover_controllers()
