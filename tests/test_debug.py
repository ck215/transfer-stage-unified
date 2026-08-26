import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mvc-refactor', 'src')))
import matplotlib
print("MATPLOTLIB PATH:", matplotlib.__file__)
