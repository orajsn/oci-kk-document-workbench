"""Recognize a confirmed transcription on a legacy KK; never pad or change digits."""
from collections import Counter
import re


def printed_digits(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:[.\s]+[0-9]+)*', value.strip()):
        return None
    return re.sub(r'[.\s]', '', value.strip())


def eligible_numbers(payload):
    if payload.get('layout_family') != 'legacy':
        return set()
    counts = Counter(printed_digits(row.get('nik_raw')) for row in payload['members'])
    short = {v: count for v, count in counts.items() if v and len(v) == 13}
    if sum(short.values()) < 2:
        return set()
    return {v for v, count in short.items() if count == 1}


def confirmed(eligible, reasons, stored, reading, state, uncertain):
    digits = printed_digits(stored)
    return (state == 'returned' and uncertain is False and 'invalid_format' in reasons
            and not {'birth_conflict', 'duplicate'}.intersection(reasons)
            and digits in eligible and printed_digits(reading) == digits)
