#!/bin/bash
cd ~/Documents/GitHub/transfer-stage-unified/
source .venv/bin/activate
python3 firmware/flash_firmware.py "$@"
