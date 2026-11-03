"""What a person who does not exist cannot say: that they bought it, used it or got results.

A generated host may show a product and explain what can be seen. Saying they lived it is a
fake testimonial, and a label does not cure that. These patterns read words, so they catch
the common forms and no more. Not legal advice.
"""

from __future__ import annotations

import re

NOT_LEGAL_ADVICE = "This is a check, not legal advice."
NOW = r"(?:just |finally |recently |actually |already |first |personally |really |always |now |only |also |even )?"
COUNT = r"(?:a|one|two|three|four|five|six|eight|ten|\d+|a few|a couple of)"
CLAIMS = (
    rf"\bi {NOW}(?:bought|purchased|ordered|paid for|picked up|got mine|got myself|got this|got these|got one)\b",
    rf"\bi(?:'ve| have) {NOW}(?:bought|purchased|ordered|used|tried|tested|had|worn|taken)\b",
    rf"\bi {NOW}(?:tried|tested|use|used|wear|wore|switched|swear by|rely on|recommend|love (?:this|it|these|mine)|started using)\b",
    r"\bi(?:'ve| have) been (?:using|taking|wearing|drinking|trying|testing)\b",
    r"\bsince i (?:started|began|switched|tried)\b",
    r"\bi can(?:'t|not) live without\b",
    r"\bi(?:'m| am) obsessed\b",
    r"\bi (?:saw|noticed|felt|lost|gained) (?:results|a difference|the difference|\d+)\b",
    r"\b(?:changed|saved|transformed|fixed|cured|cleared|healed) my \w+",
    r"\bmy (?:results|skin|hair|acne|pain|sleep|energy|weight|wrinkles|routine|experience|order|purchase|before and after|favou?rite|go to|go-to|holy grail)\b",
    r"\b(?:worked|works|helped) (?:for )?me\b",
    r"\bbefore (?:and|&) after (?:photos?|pictures?|pics?|shots?|results?)\b",
    rf"\bafter (?:just |only )?{COUNT} (?:days?|weeks?|months?)(?: of)?(?: (?:using|use|taking|wearing|trying)\b|,? i\b|,? my\b)",
    r"\bverified (?:buyer|purchase|customer|review|user)\b",
    r"\b(?:real|actual|happy|satisfied|genuine) (?:customers?|buyers?|users?|reviews?|results?)\b",
    r"\b(?:customer|honest) review\b",
    r"\bas a (?:customer|buyer|user)\b",
    r"\bi(?:'m| am) a (?:customer|buyer|user)\b",
    r"\bnot (?:sponsored|an ad|a paid)\b",
)
DISCLOSES = re.compile(
    r"\bai[- ]generated\b|\bgenerated (?:by|with|using) ai\b|\bsynthetic (?:performer|person|actor|host)\b"
    r"|\bai (?:actor|avatar|host|presenter|performer|person)\b|\bdigitally (?:created|generated)\b|\bnot a real person\b",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    straight = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", straight).lower()


def experience(text: str) -> list[str]:
    """The phrases here that claim a lived experience, as found, each once."""
    plain = _plain(text)
    return list(dict.fromkeys(match.group(0) for pattern in CLAIMS for match in re.finditer(pattern, plain)))


def usable_label(label: str) -> bool:
    """A project's own label counts only if it could tell a viewer something: two words, or a known disclosure.

    A space or one letter is inside almost any text, so it would pass every caption as a disclosure.
    """
    plain = _plain(label).strip()
    return len(re.findall(r"\w+", plain)) >= 2 or bool(DISCLOSES.search(plain))


def discloses(text: str, label: str = "") -> bool:
    """Whether a string tells the viewer this person is generated."""
    return bool(DISCLOSES.search(text)) or (usable_label(label) and _plain(label).strip() in _plain(text))
