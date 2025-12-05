# core/parser.py
import re
from typing import List, Tuple, Optional
from core import subjects as subj_mod

# parse multi-subject line like:
# "Алгебра - упр 5 Геометрия упр 3 Русский - сочинение"
# returns list of (subject_normalized, task_text)

SUBJECT_REGEX = None  # will create dynamically from subjects list

def _build_subject_regex():
    keys = []
    for s, aliases in subj_mod.SUBJECT_ALIASES.items():
        keys.append(re.escape(s))
        for a in aliases:
            keys.append(re.escape(a))
    # sort by length desc to match longer names first
    keys_sorted = sorted(set(keys), key=lambda x: -len(x))
    pattern = r'(?i)\b(' + '|'.join(keys_sorted) + r')\b'
    return re.compile(pattern)

def parse_multi(text: str) -> List[Tuple[str,str]]:
    global SUBJECT_REGEX
    if SUBJECT_REGEX is None:
        SUBJECT_REGEX = _build_subject_regex()
    # Find subject positions
    parts = []
    # normalize dashes
    text_clean = re.sub(r'[\–\—]', '-', text)
    matches = list(SUBJECT_REGEX.finditer(text_clean))
    if not matches:
        # fallback: try split by semicolon/newline
        lines = re.split(r'[\n;]+', text_clean)
        for ln in lines:
            m = SUBJECT_REGEX.search(ln)
            if m:
                subj_raw = m.group(1)
                subject = subj_mod.normalize_subject(subj_raw)
                task = ln[m.end():].strip(" -:—–")
                parts.append((subject, task if task else ""))
        return parts
    for idx, m in enumerate(matches):
        subj_raw = m.group(1)
        start = m.end()
        end = matches[idx+1].start() if idx+1 < len(matches) else len(text_clean)
        task = text_clean[start:end].strip(" -:;")
        subject = subj_mod.normalize_subject(subj_raw)
        parts.append((subject, task if task else ""))
    return parts

# detection of request phrase and subject extraction
REQUEST_TOKENS = [
    'скиньте дз','скинь дз','скиньте домашку','скинь домашку','кинь дз','кинь домашку',
    'отправьте дз','отправь дз','дай дз','покажи дз','что задали','какая домашка','бот дз',
    'скажи дз','скажите дз','домашка','домашнее задание','что задали на завтра'
]

def is_homework_request_and_extract_subject(text: str) -> Tuple[bool, Optional[str]]:
    t = text.lower()
    # if explicit subject phrase "по <subject>"
    m = re.search(r'по\s+([а-яёa-z\-\s]+)', t)
    if m:
        candidate = m.group(1).strip()
        candidate = re.sub(r'[^a-zа-яё\s\-]', '', candidate)
        subj = subj_mod.normalize_subject(candidate)
        return True, subj
    # token presence
    for tok in REQUEST_TOKENS:
        if tok in t:
            # try to extract subject in the same message
            subj = subj_mod.find_subject_in_text(t)
            return True, subj
    # detect "какая домашка по X" patterns
    if re.search(r'какая.*домаш', t) or re.search(r'что.*задали', t):
        subj = subj_mod.find_subject_in_text(t)
        return True, subj
    return False, None
