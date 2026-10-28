"""Point liblsl at plux-lsl's lsl_api.cfg (errors only). Import before pylsl."""

import os
from pathlib import Path

os.environ.setdefault("LSLAPICFG", str(Path(__file__).resolve().parent / "lsl_api.cfg"))
