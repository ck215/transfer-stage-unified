import pytest
from model.probes import StepperProbe, DCProbe, ChuckPositioner
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from model.redpercent_system import RedPercentSystem

@pytest.fixture
def model_instances():
    """
    Instantiate all the hardware models to test their ui_schema bindings.
    We pass 'SIM' or mock parameters to prevent actual hardware calls if they happen on init.
    """
    active_claims = {}
    return [
        StepperProbe(port="SIM", controller_id=None, active_claims=active_claims),
        DCProbe(port="SIM", controller_id=None, active_claims=active_claims),
        ChuckPositioner(port="SIM", controller_id=None, active_claims=active_claims),
        TemperatureSystem(port="SIM"),
        RotatorSystem(default_port="SIM"),
        RedPercentSystem()
    ]

def test_ui_schema_model_attrs_exist(model_instances):
    """
    Validate that any 'model_attr' declared in the ui_schema 
    corresponds to an actual attribute on the model instance.
    """
    for model in model_instances:
        schema = model.ui_schema
        assert "sections" in schema, f"{model.__class__.__name__} schema missing 'sections'"
        
        for section in schema["sections"]:
            assert "elements" in section, f"Section '{section.get('title')}' missing 'elements'"
            
            for element in section["elements"]:
                if "model_attr" in element:
                    attr_name = element["model_attr"]
                    assert hasattr(model, attr_name), (
                        f"Model {model.__class__.__name__} is missing "
                        f"attribute '{attr_name}' defined in ui_schema as model_attr."
                    )

def test_ui_schema_commands_exist_and_callable(model_instances):
    """
    Validate that any 'command' declared in the ui_schema 
    corresponds to a callable method on the model instance.
    """
    for model in model_instances:
        schema = model.ui_schema
        for section in schema["sections"]:
            for element in section["elements"]:
                if "command" in element:
                    cmd_name = element["command"]
                    assert hasattr(model, cmd_name), (
                        f"Model {model.__class__.__name__} is missing "
                        f"method '{cmd_name}' defined in ui_schema as command."
                    )
                    
                    cmd_func = getattr(model, cmd_name)
                    assert callable(cmd_func), (
                        f"Model {model.__class__.__name__} attribute '{cmd_name}' "
                        f"is not callable, but it is defined as a command in ui_schema."
                    )
