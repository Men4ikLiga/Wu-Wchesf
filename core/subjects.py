# core/subjects.py
from typing import Dict, List, Optional
import re
import config

# Build alias map from config
SUBJECT_ALIASES = {}
for k, v in config.SUBJECT_ALIASES.items():
    # include main name and variants
    keys = [k] + v
    SUBJECT_ALIASES[k] = [x.lower() for x in keys]

# Normalize subject name to canonical
def normalize_subject(name: str) -> str:
    if not name:
        return name
    t = name.strip().lower()
    for canon, aliases in SUBJECT_ALIASES.items():
        for a in aliases:
            if t == a or a in t or t in a:
                return canon
    # fallback: return title-cased
    return name.strip().title()

def find_subject_in_text(text: str) -> Optional[str]:
    t = text.lower()
    for canon, aliases in SUBJECT_ALIASES.items():
        for a in aliases:
            if re.search(r'\b' + re.escape(a) + r'\b', t):
                return canon
    return None
