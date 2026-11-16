"""Sorts a product into femme / homme / enfant and a category from its text (FR, EN, Darija/Arabic)."""
import re, unicodedata

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()

GENDER_RULES = [
    ("enfant", r"\b(enfant|enfants|kids?|bebe|baby|fille|garcon|junior|girls?|boys?|ans)\b|اطفال|أطفال|بيبي"),
    ("femme",  r"\b(femme|femmes|women|woman|ladies|dame|robe|jupe|abaya|caftan|kaftan|takchita|soutien|lingerie|legging)\b|نساء|نسائي"),
    ("homme",  r"\b(homme|hommes|men|man|masculin|gentleman)\b|رجال|رجالي"),
]
CATEGORY_RULES = [
    ("chaussures", r"chaussure|basket|sneaker|sandale|mocassin|botte|bottine|escarpin|shoe|mule|loafer|حذاء|صندل|سبرديلة"),
    ("sacs",       r"\bsac|pochette|cabas|bag|cartable|valise|حقيبة|صاك"),
    ("traditionnel", r"caftan|kaftan|takchita|djellaba|jellaba|gandoura|abaya|قفطان|تكشيطة|جلابة|عباية"),
    ("robes",      r"\brobe|dress|فستان|كسوة"),
    ("jeans",      r"\bjean|denim"),
    ("pantalons",  r"pantalon|trouser|chino|jogger|jogging"),
    ("vestes",     r"veste|blouson|manteau|doudoune|parka|blazer|jacket|coat"),
    ("pulls",      r"\bpull|sweat|hoodie|cardigan|gilet"),
    ("t-shirts",   r"t-?shirt|tee\b|polo|debardeur|top\b"),
    ("chemises",   r"chemise|shirt|blouse"),
    ("ensembles",  r"ensemble|set\b|survetement|tracksuit"),
    ("accessoires", r"casquette|ceinture|bijou|montre|lunette|echarpe|foulard|cap\b|bonnet"),
]

def classify(title, product_type, tags, gender_hint=None, category_hint=None):
    text = " ".join([title or "", product_type or "", " ".join(tags or [])])
    t = _norm(text) + " " + text   # keep raw for Arabic keywords
    gender = None
    for g, rx in GENDER_RULES:
        if re.search(rx, t):
            gender = g
            break
    gender = gender or gender_hint or "unisexe"
    category = next((c for c, rx in CATEGORY_RULES if re.search(rx, t)), category_hint or "autres")
    return gender, category

# ---------- Shopify feed ----------
