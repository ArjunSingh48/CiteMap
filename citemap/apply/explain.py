"""Phase 3b: plain-language explanations (English + Spanish), built from templates.

No AI: every sentence is assembled from the rule record and the engine's reasons, so the
explanation can never say something the rule record does not support.
"""
from __future__ import annotations

FACT_EN = {
    "year_built": "the year the building was built", "certificate_of_occupancy_date": "the exact certificate-of-occupancy date",
    "units": "the number of units", "landlord_unit_count": "how many units the owner owns in total",
    "owner_type": "who owns the building (person or company)", "owner_occupied": "whether the owner lives in the building",
    "unit_type": "the type of unit", "local_ordinance_text": "the text of the local ordinance (not in our sources)",
    "exact_effective_date": "the exact effective date",
    "publicly_funded_housing": "whether the building is publicly funded / affordable housing",
}
FACT_ES = {
    "year_built": "el año de construcción del edificio", "certificate_of_occupancy_date": "la fecha exacta del certificado de ocupación",
    "units": "el número de unidades", "landlord_unit_count": "cuántas unidades posee el propietario en total",
    "owner_type": "quién es el propietario (persona o empresa)", "owner_occupied": "si el propietario vive en el edificio",
    "unit_type": "el tipo de unidad", "local_ordinance_text": "el texto de la ordenanza local (no está en nuestras fuentes)",
    "exact_effective_date": "la fecha exacta de entrada en vigor",
    "publicly_funded_housing": "si el edificio es vivienda asequible o con fondos públicos",
}

SUMMARY_EN = {
    "rent_increase_limits": "The law limits rent increases for covered units{kv}.",
    "just_cause_eviction": "For covered units, the landlord needs one of the reasons the law lists (just cause) to end the tenancy.",
    "security_deposits": "The law limits the security deposit{kv}.",
    "application_screening_fees": "The law limits application / screening fees{kv}.",
    "screening_restrictions": "There are limits on how a landlord may screen or choose applicants (see the quoted law for which grounds).",
    "algorithmic_rent_setting": "This law restricts certain uses of algorithmic or coordinated rent-pricing tools; the quoted text says exactly which uses and who is covered.",
}
SUMMARY_ES = {
    "rent_increase_limits": "Los aumentos de renta están limitados{kv}.",
    "just_cause_eviction": "En las unidades cubiertas, el propietario necesita una de las causas que la ley enumera (causa justa) para terminar el contrato.",
    "security_deposits": "El depósito de garantía está limitado{kv}.",
    "application_screening_fees": "Las tarifas de solicitud / evaluación están limitadas{kv}.",
    "screening_restrictions": "Hay límites sobre cómo el propietario puede evaluar o elegir a los solicitantes (vea la ley citada para los motivos).",
    "algorithmic_rent_setting": "Esta ley restringe ciertos usos de herramientas algorítmicas o coordinadas para fijar la renta; el texto citado dice exactamente qué usos y a quién cubre.",
}


def summary(rule: dict, lang: str = "en") -> str:
    kv = rule.get("key_value")
    if kv == "No local rent control allowed":
        return ("State law bars local rent control, so there is no local rent cap here."
                if lang == "en" else "La ley estatal prohíbe el control local de rentas; aquí no hay tope local de renta.")
    tpl = (SUMMARY_EN if lang == "en" else SUMMARY_ES)[rule["category"]]
    kvs = ""
    if kv and any(ch.isdigit() for ch in kv):
        kvs = f" ({kv})"
    return tpl.format(kv=kvs)


def _cond_text(c: dict, lang: str) -> str:
    f, op, v = c["fact"], c["op"], c["value"]
    if f == "year_built":
        what = ("certificate of occupancy" if c.get("basis") == "certificate_of_occupancy" else "built") if lang == "en" \
            else ("certificado de ocupación" if c.get("basis") == "certificate_of_occupancy" else "construido")
        word = {"<": "before", "<=": "on or before", ">": "after", ">=": "on or after"}.get(op, op) if lang == "en" \
            else {"<": "antes de", "<=": "hasta el", ">": "después de", ">=": "desde el"}.get(op, op)
        return f"{what} {word} {v}"
    if f == "units":
        return f"{op} {v} units" if lang == "en" else f"{op} {v} unidades"
    return f"{f.replace('_', ' ')} {op} {v}"


def explain(rule: dict, decision, facts, juris, by_id: dict, lang: str = "en") -> str:
    en = lang == "en"
    s = summary(rule, lang)
    cite = rule["citation"]
    res = decision.result
    if res == "applies":
        met = [_cond_text(c, lang) for c, v in decision.reasons if isinstance(c, dict) and v is True]
        if met:
            why = (f" Covered because: {', '.join(met)}" if en else f" Aplica porque: {', '.join(met)}")
            if facts.year_built:
                why += f" (built {facts.year_built})" if en else f" (construido en {facts.year_built})"
            why += "."
        else:
            why = (" No coverage limit that rules this building out was found in the source." if rule["level"] == "state" or not rule["coverage_conditions"].get("all")
                   else "") if en else (" No se encontró en la fuente un límite de cobertura que excluya este edificio." if rule["level"] == "state" else "")
        return f"{s}{why} Source: {cite}." if en else f"{s}{why} Fuente: {cite}."
    if res == "unknown" and rule.get("text_in_corpus") is False:
        return ("A local ordinance on this topic is listed in the challenge sources, but its text is not in our corpus, "
                f"so we cannot say whether it covers this building (unknown). Source: {cite} ({rule.get('source_url') or 'link not available'})."
                if en else "Hay una ordenanza local sobre este tema en las fuentes, pero su texto no está en nuestro corpus; "
                f"no podemos decir si cubre este edificio (desconocido). Fuente: {cite} ({rule.get('source_url') or 'sin enlace'}).")
    if res == "unknown":
        names = [(FACT_EN if en else FACT_ES).get(m, m) for m in decision.missing_facts] or \
                (["whether the local rule covers this building"] if en else ["si la norma local cubre este edificio"])
        if en:
            return (f"{s} Whether it covers this building depends on {', '.join(names)}, which the public records "
                    f"used here do not include. Source: {cite}.")
        return f"{s} Que cubra este edificio depende de {', '.join(names)}, dato que no figura en los registros públicos usados. Fuente: {cite}."
    if res == "superseded":
        locs = [by_id[x]["citation"] for x in decision.superseded_by if x in by_id]
        if en:
            return (f"{s} This state rule covers the building, but the stricter local rule ({', '.join(locs)}) governs here, "
                    f"as the state text provides. Source: {cite}.")
        return f"{s} Esta norma estatal cubre el edificio, pero rige la norma local más estricta ({', '.join(locs)}). Fuente: {cite}."
    if res == "not_yet_effective":
        d = rule.get("effective_date") or "a future date"
        return (f"{s} Enacted but not yet in effect: it takes effect on {d}. Source: {cite}." if en
                else f"{s} Aprobada pero aún no vigente: entra en vigor el {d}. Fuente: {cite}.")
    if res == "pending":
        return (f"Proposed only: {cite} is a bill, not law. If enacted it would cover this address. It is not in force."
                if en else f"Solo propuesta: {cite} es un proyecto de ley, no una ley vigente. Si se aprueba, cubriría esta dirección.")
    return s
