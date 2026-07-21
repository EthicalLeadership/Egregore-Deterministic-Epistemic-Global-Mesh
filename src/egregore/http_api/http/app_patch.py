#!/usr/bin/env python3
"""Patch app.py to include chat router."""

import sys

app_file = "/opt/egregore/src/egregore/http_api/http/app.py"
with open(app_file) as f:
    content = f.read()

# Check if already patched
if "chat_router" in content:
    print("Already patched")
    sys.exit(0)

# Add import after existing imports
import_line = "from egregore.http_api.http.v1.intake import router as intake_router"
new_import = """from egregore.http_api.http.v1.intake import router as intake_router
from egregore.http_api.http.v1.chat import router as chat_router"""

content = content.replace(import_line, new_import)

# Add router to list
router_line = "        intake_router,"
new_router = """        intake_router,
        chat_router,"""

content = content.replace(router_line, new_router)

with open(app_file, "w") as f:
    f.write(content)

print("Patched app.py with chat router")
