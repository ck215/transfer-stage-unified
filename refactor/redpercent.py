import tkinter as tk
from models.redpercent_model import RedPercentModel
from views.redpercent_view import RedPercentView
from controllers.redpercent_controller import RedPercentController

def main():
    root = tk.Tk()
    model = RedPercentModel()
    view = RedPercentView(root)
    controller = RedPercentController(model, view)
    root.mainloop()

if __name__ == "__main__":
    main()
