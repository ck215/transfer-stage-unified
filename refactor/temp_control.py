import tkinter as tk
from controllers.temp_controller import TempController

def main(port):
    root = tk.Tk()
    app = TempController(root, port)
    root.mainloop()

if __name__ == '__main__':
    main('COM5')
