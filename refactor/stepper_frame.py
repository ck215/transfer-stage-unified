import tkinter as tk
import sys
import os

# Add src to sys.path so imports work
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from models.stepper_model import StepperModel
from views.stepper_view import StepperView
from controllers.stepper_controller import StepperController
import src.serialDrive as serialDrive
import src.controllerDrive as controllerDrive

def main(port=None, controllerID=0, active_claims=None, process_name="main_process"):
    if active_claims is None:
        active_claims = {}

    print("[main] Starting main loop")

    serial_port = port
    # If no port provided, use a default string that can be edited in GUI
    if not serial_port:
        serial_port = "/dev/ttys00X"

    # Setup GUI class and its members
    root = tk.Tk()
    print("[main] Initializing GUI...")

    # Initialize the model
    model = StepperModel()
    model.serial_port.set(serial_port)

    # Initialize arduino connection
    print("[main] Initializing arduino connection...")
    serial = serialDrive.SerialArduino(port=serial_port)

    # Setup controller polling class
    print("[main] Initializing controller polling class...")
    controller_driver = controllerDrive.ControllerPoller(controllerID, active_claims, process_name)
    
    # Initialize the AppLogic (Controller)
    print("[main] Initializing App Logic...")
    app_controller = StepperController(root, model, controller_driver, serial, active_claims, process_name)

    # Initialize the view
    view = StepperView(root, model, app_controller)
    app_controller.set_view(view)

    print("[main] Launching GUI...")
    root.mainloop()

    print("[main] Finished main loop, program terminated.")


if __name__ == "__main__":
    # In original stepper_frame.py, main() is called without args
    # But it accepts 4 parameters. We will provide defaults here if they are missing
    try:
        main()
    except TypeError:
        # Fallback if the signature of main was expecting them differently
        main(None, 0, {}, "main_process")
