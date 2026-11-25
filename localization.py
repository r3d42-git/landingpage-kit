"""Request language selection and translations carried alongside status readings."""

import re


class Text(str):
    """An application-owned German string with its English rendering and optional metric key."""

    def __new__(cls, german, english, *, key=None):
        result = super().__new__(cls, german)
        result.english = english
        result.key = key
        return result


def translate(value, language):
    return value.english if language == "en" and isinstance(value, Text) else str(value)


def request_language(header):
    choices = []
    for entry in header.split(","):
        tag, *parameters = entry.lower().split(";")
        if not re.fullmatch(r"(?:[a-z]+(?:-[a-z0-9]+)*|\*)", tag.strip()):
            continue
        language = tag.strip().split("-", 1)[0]
        if language not in {"de", "en", "*"}:
            continue
        quality = 1.0
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name.strip() == "q":
                value = value.strip()
                quality = float(value) if re.fullmatch(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)", value) else 0
                break
        if quality > 0:
            choices.append((quality, language))
    language = max(choices, key=lambda item: item[0])[1] if choices else "de"
    return "de" if language == "*" else language


def number(value, decimals=0, *, grouping=True):
    spec = f"{',' if grouping else ''}.{decimals}f"
    english = format(value, spec)
    return Text(english.translate(str.maketrans(",.", ".,")), english)


def phrase(german, english, *values):
    return Text(
        german.format(*(translate(value, "de") for value in values)),
        english.format(*(translate(value, "en") for value in values)),
    )


def restore_text(fields, name, limit):
    """Read opt-in translations from host snapshots and ignore invalid alternatives."""
    value, english = fields.get(name), fields.get(f"{name}_en")
    if not isinstance(value, str) or not isinstance(english, str) or len(english) > limit:
        return value
    if (name != "value" and not english.strip()) or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in english
    ):
        return value
    key = fields.get("key") if name == "label" else None
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key):
        key = None
    return Text(value, english, key=key)


def encode_translations(reading):
    """Keep both strings when storing a reading or writing a host snapshot."""
    def encode(fields):
        result = dict(fields)
        for field, value in fields.items():
            if isinstance(value, Text):
                result[f"{field}_en"] = value.english
                if field == "label" and value.key is not None:
                    result["key"] = value.key
        return result

    result = encode(reading)
    result["metrics"] = [encode(metric) for metric in reading["metrics"]]
    return result


def render_reading(reading, language):
    """Expose one language and stable metric keys without altering the stored reading."""
    def render(fields):
        result = dict(fields)
        for field in ("label", "detail", "value"):
            english = result.pop(f"{field}_en", None)
            if language == "en" and english is not None:
                result[field] = english
        return result

    result = render(encode_translations(reading))
    result["metrics"] = [render(metric) for metric in result["metrics"]]
    return result
