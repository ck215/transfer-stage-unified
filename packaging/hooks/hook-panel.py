# The station's `panel` is its own top-level module (src/panel.py), not the
# HoloViz `panel` package that pyinstaller-hooks-contrib ships a hook for.
# A hook here takes precedence over the contrib one, so that hook's
# collect_data_files("panel") / collect_submodules("panel.models") never run
# against the wrong module. Nothing to collect: panel.py is pure Python.
hiddenimports = []
datas = []
