# modules/search.py
from core import homework as hw
from typing import List, Tuple

def search_homework(query: str) -> List[Tuple]:
    q = query.lower()
    rows = hw.list_homework()
    results = []
    for r in rows:
        if q in r[2].lower() or q in r[1].lower():
            results.append(r)
    return results
