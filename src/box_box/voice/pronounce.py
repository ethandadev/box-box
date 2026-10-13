"""How the engineer says names: drivers, corners and places, in IPA for the Kokoro voice.

The voice's dictionary guesses foreign names from English spelling ("Leclerc" came out as "LECK-lurk",
"Ocon" as "AH-kun"). Here are the pronunciations an F1 pit wall actually uses. Text is phonemized in
chunks around these names, and the names are dropped in as phonemes.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# Pronunciations as said on F1 radio and TV (anglicised where the paddock anglicises). Written in
# espeak-style IPA with the stress mark just before the vowel; every symbol must be in Kokoro's
# vocabulary (anything else is silently dropped: a test checks).
LEXICON: dict[str, str] = {
    # drivers
    "Leclerc": "ləklˈɛɹ",
    "Charles": "ʃˈɑɹl",
    "Ocon": "oʊkˈɔn",
    "Esteban": "ɛstˈeɪbɑn",
    "Sainz": "sˈaɪnz",
    "Piastri": "piˈæstɹi",
    "Bortoleto": "bˌɔɹtoʊlˈɛtoʊ",
    "Hadjar": "ɑdʒˈɑɹ",
    "Isack": "ˈiːzæk",
    "Gasly": "ɡæzlˈiː",
    "Perez": "pˈɛɹɛz",
    "Pérez": "pˈɛɹɛz",
    "Hulkenberg": "hˈʌlkənbɜːɡ",
    "Hülkenberg": "hˈʌlkənbɜːɡ",
    "Bottas": "bˈɑtəs",
    "Valtteri": "vˈɑltɛɹi",
    "Verstappen": "vɚstˈæpən",
    "Kimi": "kˈiːmi",
    "Antonelli": "ˌæntoʊnˈɛli",
    "Ricciardo": "ɹɪkˈɑɹdoʊ",
    "Zhou": "dʒˈoʊ",
    "Guanyu": "ɡwˈɑnjuː",
    "Tsunoda": "tsuːnˈoʊdə",
    "Yuki": "jˈuːki",
    "Magnussen": "mˈæɡnʊsən",
    "Doohan": "dˈuːən",
    "Colapinto": "kˌoʊləpˈintoʊ",
    "Lindblad": "lˈɪndblɑd",
    "Albon": "ˈælbɑn",
    "Fernando": "fɚnˈændoʊ",
    "Lando": "lˈændoʊ",
    "Oscar": "ˈɑskɚ",
    "Andrea": "ɑndɹˈeɪə",
    "Nico": "nˈiːkoʊ",
    "Sargeant": "sˈɑɹdʒənt",
    "Fittipaldi": "fˌɪtipˈɑldi",
    "Vesti": "vˈɛsti",
    "Iwasa": "iwˈɑsə",
    "Maini": "mˈaɪni",
    "Crawford": "kɹˈɔfɚd",
    "Fornaroli": "fˌɔɹnəɹˈoʊli",
    "Dunne": "dˈʌn",
    # places and corners
    "Eau Rouge": "ˌoʊ ɹˈuːʒ",
    "Raidillon": "ɹˌeɪdiˈjɔn",
    "La Source": "lɑ sˈʊɹs",
    "Les Combes": "leɪ kˈɔmb",
    "Pouhon": "puˈɔn",
    "Fagnes": "fˈɑnjə",
    "Stavelot": "stˈævəloʊ",
    "Blanchimont": "blˈɑnʃimɔn",
    "Malmedy": "mˈælmədi",
    "Rivage": "ɹivˈɑʒ",
    "Copse": "kˈɑps",
    "Becketts": "bˈɛkɪts",
    "Maggotts": "mˈæɡəts",
    "Luffield": "lˈʌfiːld",
    "Woodcote": "wˈʊdkoʊt",
    "Parabolica": "pˌæɹəbˈɑlɪkə",
    "Lesmo": "lˈɛzmoʊ",
    "Roggia": "ɹˈoʊdʒə",
    "Ascari": "ɑskˈɑɹi",
    "Monza": "mˈɔnzə",
    "Imola": "ˈiːmoʊlə",
    "Tamburello": "tˌæmbʊɹˈɛloʊ",
    "Tosa": "tˈoʊzə",
    "Piratella": "pˌiɹɑtˈɛlə",
    "Acque Minerali": "ˈɑkweɪ mˌinɛɹˈɑli",
    "Variante Alta": "vˌɑɹiˈɑnteɪ ˈɑltə",
    "Rivazza": "ɹivˈɑtsə",
    "Zandvoort": "zˈændvɔɹt",
    "Tarzan": "tˈɑɹzən",
    "Hugenholtz": "hˈjuːɡənhoʊlts",
    "Scheivlak": "skˈaɪvlɑk",
    "Arie Luyendyk": "ˈɑɹi lˈaɪəndaɪk",
    "Suzuka": "suːzˈuːkə",
    "Degner": "dˈɛɡnɚ",
    "Interlagos": "ˌɪntɚlˈɑɡoʊs",
    "Juncao": "ʒʊŋsˈaʊ",
    "Descida do Lago": "dɛsˈiːdə du lˈɑɡoʊ",
    "Sainte Devote": "sˌænt dəvˈɔt",
    "Massenet": "mˌæsənˈeɪ",
    "Mirabeau": "mˌiɹəbˈoʊ",
    "Portier": "pɔɹtjˈeɪ",
    "Rascasse": "ɹæskˈæs",
    "Anthony Noghes": "ˈæntəni nˈɔʒ",
    "Tabac": "tæbˈæk",
    "Remus": "ɹˈeɪmʊs",
    "Schlossgold": "ʃlˈɔsɡoʊld",
    "Niki Lauda": "nˈɪki lˈaʊdə",
    "Baku": "bɑkˈuː",
    "Jeddah": "dʒˈɛdə",
    "Losail": "loʊsˈaɪl",
    "Hungaroring": "hˈʌŋɡəɹoʊɹˌɪŋ",
    "Montreal": "mˌʌntɹiˈɔl",
    "Sakhir": "sɑkˈɪɹ",
    "Shanghai": "ʃæŋhˈaɪ",
    "Madrid": "mədɹˈɪd",
}

# Paddock words the voice's dictionary gets wrong, said the way the pit wall says them. Matched in any
# case ("Inters", "inters"). "Inters" is a quick IN-terz, not in-TERZ.
JARGON: dict[str, str] = {
    "inters": "ˈɪntɚz",
    "inter": "ˈɪntɚ",
    "outlap": "ˈaʊtlæp",
    "inlap": "ˈɪnlæp",
    "hotlap": "hˈɑtlæp",
    "quali": "kwˈɑli",
    "pirelli": "pɪɹˈɛli",
    "ferrari": "fəɹˈɑɹi",
    "mclaren": "məklˈæɹən",
    "aston": "ˈæstən",
    "sauber": "sˈaʊbɚ",
    "haas": "hˈɑs",
    "tifosi": "tifˈoʊzi",
    "parc ferme": "pˈɑɹk fɛɹmˈeɪ",
    "parc fermé": "pˈɑɹk fɛɹmˈeɪ",
    "kerbs": "kˈɜːbz",
    "kerb": "kˈɜːb",
    "deg": "dˈɛɡ",
    "monaco": "mˈɑnəkoʊ",
}

_NAMES = sorted(LEXICON, key=len, reverse=True)  # longest first: "Eau Rouge" before "Rouge"
_WORDS = sorted(JARGON, key=len, reverse=True)
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in _NAMES) + "|(?i:" + "|".join(re.escape(w) for w in _WORDS) + r"))('s)?\b"
)


def _ipa(word: str) -> str:
    return LEXICON.get(word) or JARGON[word.lower()]


def has_names(text: str) -> bool:
    return bool(_PATTERN.search(text))


def to_phonemes(text: str, phonemize: Callable[[str], str]) -> str:
    """Phonemize `text`, using the lexicon for known names and `phonemize` for everything else."""
    out: list[str] = []
    pos = 0
    for m in _PATTERN.finditer(text):
        before = text[pos : m.start()]
        if before.strip():
            out.append(phonemize(before).strip())
        ipa = _ipa(m.group(1))
        if m.group(2):  # possessive: Leclerc's
            ipa += "z"
        out.append(ipa)
        pos = m.end()
        # Keep punctuation that follows a name (it shapes the pause and the intonation).
        punct = re.match(r"[,.!?;:]+", text[pos:])
        if punct:
            out[-1] += punct.group(0)
            pos += punct.end()
    rest = text[pos:]
    if rest.strip():
        out.append(phonemize(rest).strip())
    return " ".join(p for p in out if p)
