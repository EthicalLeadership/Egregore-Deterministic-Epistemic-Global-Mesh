import re

with open('stress_test.py', 'r') as f:
    content = f.read()

# Fix 1: NORMAL -> HEALTHY
content = content.replace('FreezeState.NORMAL', 'FreezeState.HEALTHY')

# Fix 2: UNFROZEN -> RECONCILING  
content = content.replace('FreezeState.UNFROZEN', 'FreezeState.RECONCILING')

# Fix 3: fc.freeze(reason=...) -> fc.freeze_writes(reason=...)
content = content.replace('fc.freeze(reason=', 'fc.freeze_writes(reason=')

# Fix 4: fc.unfreeze(reason=... -> fc.reconcile(reason=...
content = content.replace('fc.unfreeze(reason=', 'fc.reconcile(reason=')

# Fix 5: fc.reset(reason=... -> fc.resume(reason=...
content = content.replace('fc.reset(reason=', 'fc.resume(reason=')

with open('stress_test.py', 'w') as f:
    f.write(content)

print("Fixed")
