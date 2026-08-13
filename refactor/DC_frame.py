import tkinter as tk
import controllerDrive
import serialDrive

from models.DC_model import DCModel
from views.DC_view import DCView
from controllers.DC_controller import DCController

def main(port, controllerID, active_claims, process_name):
    print("[main] Starting main loop")

    # Setup GUI class and its members
    root = tk.Tk()
    print("[main] Initializing GUI...")

    # Connect Arduino via serial
    print("[main] Initializing arduino connection...")
    serial = serialDrive.SerialArduino(port=port)

    # Setup controller class
    print("[main] Initializing controller polling class...")
    hw_controller = controllerDrive.ControllerPoller(controllerID, active_claims, process_name)
    
    model = DCModel()
    model.active_claims = active_claims
    model.serial_port.set(port)

    app_controller = DCController(root, model, hw_controller, serial, process_name)
    view = DCView(root, model, app_controller)
    app_controller.set_view(view)
    
    # Run the GUI application
    print("[main] Launching GUI...")
    root.mainloop()

    # Shutdown code once GUI is closed
    print("[main] Finished main loop, program terminated.")

if __name__ == "__main__":
    # Test arguments
    main("/dev/ttys00X", 0, {}, "DC")
