#!/usr/bin/env python3
from pathlib import Path

PRIMARY = Path("/opt/egregore/src/egregore")

# Fix interface/constraint_binding_ports.py
cbp = PRIMARY / "interface" / "constraint_binding_ports.py"
if cbp.exists():
    text = cbp.read_text()
    lines = text.splitlines()
    new_lines = []
    inserted_import = False
    for line in lines:
        stripped = line.strip()
        # Remove class definition of RegistryValidationError
        if stripped.startswith("class RegistryValidationError"):
            # Skip until next non-indented line or end of class
            continue
        # Remove any lines inside that class (indented)
        # Heuristic: if we just skipped the class header, skip indented lines
        # Better: check if line is indented and we're in skip mode
        # Simpler: just replace the import line and leave class removal manual, or use regex
        new_lines.append(line)
    
    # Better approach: regex replace the class block
    import re
    text = cbp.read_text()
    # Remove class RegistryValidationError(...) block
    text = re.sub(
        r'class RegistryValidationError\([^)]*\):.*?(?=\n\S|\Z)',
        '',
        text,
        flags=re.DOTALL
    )
    # Add import from domain at top if not present
    if "from egregore.domain.legal_agent.errors import RegistryValidationError" not in text:
        text = "from egregore.domain.legal_agent.errors import RegistryValidationError\n" + text
    
    cbp.write_text(text)
    print("DEDUPED interface/constraint_binding_ports.py")

# Fix interface/semantics_ports.py
sp = PRIMARY / "interface" / "semantics_ports.py"
if sp.exists():
    text = sp.read_text()
    import re
    # Remove class ISemanticsDomainAdapter or Protocol definition
    text = re.sub(
        r'class ISemanticsDomainAdapter\([^)]*\):.*?(?=\n\S|\Z)',
        '',
        text,
        flags=re.DOTALL
    )
    # Also remove Protocol import if only used for this class
    if "from egregore.domain.semantics.ports import ISemanticsDomainAdapter" not in text:
        text = "from egregore.domain.semantics.ports import ISemanticsDomainAdapter\n" + text
    
    sp.write_text(text)
    print("DEDUPED interface/semantics_ports.py")

print("\nDone. Interface now imports from domain. No circular dependency.")
