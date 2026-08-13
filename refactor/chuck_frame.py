import tkinter as tk
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import controllerDrive
import serialDrive

from refactor.models.chuck_model import ChuckModel
from refactor.views.chuck_view import ChuckView
from refactor.controllers.chuck_controller import ChuckController

def main(port, controllerID, active_claims, process_name):
    print("[main] Starting main loop")

    root = tk.Tk()
    print("[main] Initializing models...")
    model = ChuckModel()
    model.serial_port.set(port)

    print("[main] Initializing arduino connection...")
    hw_serial = serialDrive.SerialArduino(port=port)

    print("[main] Initializing controller polling class...")
    hw_controller = controllerDrive.ControllerPoller(controllerID, active_claims, process_name)
    
    print("[main] Initializing controller...")
    controller = ChuckController(root, model, hw_controller, hw_serial, active_claims, process_name)

    print("[main] Initializing view...")
    view = ChuckView(root, model, controller)
    controller.set_view(view)

    print("[main] Launching GUI...")
    controller.start()
    
    print("\nWelcome to the Transfer Stage Control Interface")
    root.mainloop()

    print("[main] Finished main loop, program terminated.")

if __name__ == "__main__":
    pass
