"""Respuestas sintéticas para probar el pipeline sin AWS (llm.provider = "mock").

No pretenden acertar: usan coincidencia de palabras para que el formato sea el real
y los pasos se puedan ejecutar de punta a punta.
"""

import json
import re

SIGNALS = [
    ("I04", r"for (dry skin|sleep|energy|hydration|pain|constipation)|hydration|immun|gut|relief"),
    ("I08", r"sugar free|zero sugar|organic|decaf|gluten free|keto|unscented|fragrance free|non toxic|natural"),
    ("I03", r"\b(kids?|baby|toddler|men|women|seniors?|girls|boys)\b"),
    ("I01", r"\b(travel|gym|workout|party|christmas|halloween|summer|office)\b"),
    ("I07", r"for keurig|compatible|vertuo|for iphone|replacement"),
    ("I02", r"\bgifts?\b"),
    ("I06", r"\d+ ?(pack|count|ct)\b|bulk|mini|variety pack|packets|powder|travel size|refill|gallon"),
    ("I05", r"\b(vanilla|chocolate|strawberry|cherry|mango|black|white|pink|wireless|leather)\b"),
    ("I10", r"cheap|budget|affordable|under \$?\d+|premium|luxury"),
    ("I14", r"\bvs\b|alternative|dupe"),
]
STOP = {"for", "and", "with", "the", "of", "a", "an", "in", "on", "to", "by", "set", "other", "accessories", "products"}


def _toks(s):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


def _rows(user):
    return re.findall(r"^(K\d+)\t(.+)$", user, re.M)


def _classify_category(system, user):
    cats = [(m.group(1), _toks(m.group(2))) for m in re.finditer(r"^(C\d{3}) \| (.+)$", system, re.M)]
    out = []
    for kid, kw in _rows(user):
        kt = _toks(kw)
        best, score = "XX", 0
        for cid, ct in cats:
            s = len(kt & ct)
            if s > score:
                best, score = cid, s
        out.append(f"{kid}|{best}")
    return "\n".join(out)


def _classify_leaf(system, user):
    nodes = [(m.group(1), _toks(m.group(2))) for m in re.finditer(r"^([LP]\d+) \| (.+)$", system, re.M)]
    out = []
    for kid, kw in _rows(user):
        kt = _toks(kw)
        best, score = "NX", 0
        for code, nt in nodes:
            s = len(kt & nt)
            if s > score:
                best, score = code, s
        intent, need = "G", ""
        for code, rx in SIGNALS:
            m = re.search(rx, kw)
            if m:
                intent, need = code, m.group(0)
                break
        if best == "NX":
            intent, need = "", ""
        out.append(f"{kid}|{best}|{intent}|{need}|")
    return "\n".join(out)


def _group(user):
    return "\n".join(f"{m.group(1)}|{m.group(2).strip()}" for m in re.finditer(r"(N\d{5}) \| ([^|]+) \|", user))


def _generate(system, user):

    briefs = json.loads(user[user.index("[") :])
    out = []
    for b in briefs:
        p, n = b["product"], b["need"]
        np_ = f"{n + ' ' if n else ''}{p}"
        q = [
            f"What's a good {np_}?",
            f"Which {np_} should I get?",
            f"What {np_} do you recommend?",
            f"Can you recommend a {np_}?",
            f"Looking for a {np_}, any suggestions?",
            f"Is there a good {np_} to buy?",
        ]
        m = re.search(r"write (\d+) candidate", system)
        q = q[: int(m.group(1)) if m else 3]
        out.append({"id": b["id"], "candidates": [{"s": s, "text": t} for s, t in zip("ABCDEF", q, strict=False)]})
    return json.dumps(out)


def _judge(user):

    items = json.loads(user)
    crit = ["realism", "single_need", "product_seeking", "neutrality", "naturalness"]
    return json.dumps(
        [
            {"id": it["id"], "reason": "mock", "scores": {k: dict.fromkeys(crit, 4) for k in it["options"]}}
            for it in items
        ]
    )


def mock_respond(stage, system, user):
    """Respuesta sintética con el formato real de cada etapa."""
    if stage == "group":
        return _group(user)
    if stage == "generate":
        return _generate(system, user)
    if stage == "judge":
        return _judge(user)
    if stage == "classify_category":
        return _classify_category(system, user)
    if stage == "classify_leaf":
        return _classify_leaf(system, user)
    if stage == "merge_siblings":
        return "NONE"
    raise NotImplementedError(f"mock sin respuesta para la etapa '{stage}'")
