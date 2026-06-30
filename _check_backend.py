#!/usr/bin/env python3
"""Check which pystray backend is loaded."""
import pystray
import pystray._util

# pystray selects backend at import time, check which module Icon comes from
print(f"Icon class: {pystray.Icon}")
print(f"Module: {pystray.Icon.__module__}")
