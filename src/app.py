import os
import sys
import tkinter as tk
from tkinter import ttk

def launch_legacy():
    print("[Launcher] Starting Legacy Tkinter Dashboard...")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.join(os.path.dirname(__file__), "app_legacy.py")])

def launch_pyside():
    print("[Launcher] Starting PySide6 Dashboard...")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.join(os.path.dirname(__file__), "app_pyside.py")])

def main():
    if "--legacy" in sys.argv:
        launch_legacy()
    elif "--pyside" in sys.argv:
        launch_pyside()

    root = tk.Tk()
    root.title("Unified Stage Controller - Launcher")
    root.geometry("400x200")
    root.eval('tk::PlaceWindow . center')

    ttk.Label(root, text="Select Dashboard UI Engine", font=("Helvetica", 16, "bold")).pack(pady=20)

    btn_frame = ttk.Frame(root)
    btn_frame.pack(pady=10)

    btn_pyside = ttk.Button(btn_frame, text="Launch PySide6 (New)", command=launch_pyside, width=25)
    btn_pyside.grid(row=0, column=0, padx=10, pady=10)

    btn_legacy = ttk.Button(btn_frame, text="Launch Tkinter (Legacy)", command=launch_legacy, width=25)
    btn_legacy.grid(row=1, column=0, padx=10, pady=10)

    root.mainloop()

if __name__ == "__main__":
    main()
