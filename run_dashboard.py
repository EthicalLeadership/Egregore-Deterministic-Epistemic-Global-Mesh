#!/usr/bin/env python3
import os, sys
sys.path.insert(0, 'src')

# Set API keys BEFORE importing the app (middleware loads at import time)
api_key = open('secrets/api_key.hex').read().strip()
os.environ.setdefault('EGREGORE_API_KEYS', api_key + ':test:admin:admin')
os.environ.setdefault('EGREGORE_ZARC_SIGNING_KEY_HEX', open('secrets/signing_key.pem').read().strip())

from egregore.shared.freeze_state import FreezeController
from egregore.interface.bootstrap import create_app

freeze_ctrl = FreezeController(tenant_id='default')

class AuthContext:
    operator_id = 'system'
    roles = {'operator','admin'}

app = create_app(freeze_controller=freeze_ctrl)
app.state.auth_context = AuthContext()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8443)
