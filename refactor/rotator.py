import sys
import os

# Add src to sys.path so we can import lib
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk
from models.rotator_model import RotatorModel
from views.rotator_view import RotatorView
from controllers.rotator_controller import RotatorController

def main(port="COM1"):
    root = tk.Tk()
    
    model = RotatorModel(default_port=port)
    controller = RotatorController(model)
    view = RotatorView(root, model, controller)
    
    controller.set_view(view, root)
    
    root.mainloop()

if __name__ == "__main__":
    main("COM1")
