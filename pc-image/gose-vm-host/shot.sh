#!/bin/sh
# global screenshot — capture the current screen (host-side, sees games) into Pictures
curl -s -X POST http://127.0.0.1:8780/capture/shot >/dev/null 2>&1
