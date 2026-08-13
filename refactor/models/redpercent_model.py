class RedPercentModel:
    def __init__(self):
        self.monitoring = False
        self.focus_area = None
        self.baseline_red = 0.0
        self.current_red = 0.0
        self.last_printed_red = -1.0
        self.log_data = []

    def add_log(self, entry):
        self.log_data.append(entry)
