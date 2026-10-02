"""Validate the closed prerequisite grammar. PR6A does not evaluate predicates."""
from __future__ import annotations

from .validation import choice, integer, require, rule_id, sequence, shape, text


def validate_predicate(value: dict, path: str) -> set[str]:
    """Return referenced stable rule IDs after checking every node and field."""
    require(type(value) is dict and type(value.get("op")) is str,
            "invalid_predicate", path)
    op = value["op"]
    if op in {"all", "any"}:
        shape(value, "op children", path)
        sequence(value["children"], path + ".children")
        require(bool(value["children"]), "invalid_predicate", path)
        refs = set()
        for i, child in enumerate(value["children"]):
            refs.update(validate_predicate(child, f"{path}.children[{i}]"))
        return refs
    if op == "not":
        shape(value, "op child", path)
        return validate_predicate(value["child"], path + ".child")
    if op in {"feat", "class", "ancestry", "access"}:
        shape(value, "op id", path)
        return {rule_id(value["id"], path + ".id", None if op == "access" else op)}
    if op == "level":
        shape(value, "op min", path)
        integer(value["min"], 1, 20, path + ".min")
    elif op == "proficiency":
        shape(value, "op statistic rank", path)
        text(value["statistic"], path + ".statistic", pattern=r"[a-z]+(?:-[a-z]+)*")
        choice(value["rank"], {"untrained", "trained", "expert", "master", "legendary"},
               path + ".rank")
    elif op == "attribute":
        shape(value, "op attribute min", path)
        choice(value["attribute"], {"str", "dex", "con", "int", "wis", "cha"},
               path + ".attribute")
        integer(value["min"], -5, 10, path + ".min")
    elif op in {"trait", "tradition", "deity", "sanctification", "rarity"}:
        shape(value, "op value", path)
        text(value["value"], path + ".value", pattern=r"[a-z0-9]+(?:-[a-z0-9]+)*")
        enums = {"tradition": {"arcane", "divine", "occult", "primal"},
                 "sanctification": {"holy", "unholy"},
                 "rarity": {"common", "uncommon", "rare", "unique"}}
        if op in enums:
            choice(value["value"], enums[op], path + ".value")
    else:
        require(False, "invalid_predicate", path)
    return set()
