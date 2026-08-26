import pytest
import tkinter as tk
from tkinter import messagebox
import threading
import multiprocessing
import sys

def call_massive_string():
    sys.path.append('src')\n    import mainGUI\n    root = tk.Tk()
    root.withdraw()
    massive_string = "A" * 10000000
    messagebox.showerror("Massive Error", massive_string, master=root)

def call_multithreaded():
    sys.path.append('src')\n    import mainGUI\n    root = tk.Tk()
    root.withdraw()
    
    def call_error():
        messagebox.showerror("Thread Error", "Error from background thread", master=root)

    t1 = threading.Thread(target=call_error)
    t2 = threading.Thread(target=call_error)
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()

def test_massive_string_hangs_or_crashes():
    p = multiprocessing.Process(target=call_massive_string)
    p.start()
    p.join(timeout=3)
    if p.is_alive():
        p.terminate()
        p.join()
        pytest.fail("Legacy UI error dialog hangs indefinitely when given massive strings.")
    elif p.exitcode != 0:
        pytest.fail(f"Legacy UI error dialog crashed with exit code {p.exitcode} when given massive strings.")

def test_multithreaded_hangs_or_crashes():
    p = multiprocessing.Process(target=call_multithreaded)
    p.start()
    p.join(timeout=3)
    if p.is_alive():
        p.terminate()
        p.join()
        pytest.fail("Legacy UI error dialog hangs indefinitely when called from multiple threads.")
    elif p.exitcode != 0:
        pytest.fail(f"Legacy UI error dialog crashed with exit code {p.exitcode} when called from multiple threads.")

def call_non_string():
    sys.path.append('src')\n    import mainGUI\n    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("List Error", ["Error", "List"], master=root)
    except Exception as e:
        sys.exit(0) # Proper handling, it's an exception, not a crash or hang

def test_non_string_hangs_or_crashes():
    p = multiprocessing.Process(target=call_non_string)
    p.start()
    p.join(timeout=3)
    if p.is_alive():
        p.terminate()
        p.join()
        pytest.fail("Legacy UI error dialog hangs indefinitely when given a list.")
    elif p.exitcode != 0:
        pytest.fail(f"Legacy UI error dialog crashed with exit code {p.exitcode} when given a list.")
