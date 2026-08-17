# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Paraphrase families, splits, and the honest reading of a pass rate.

Three pieces of the ratified measurement method live here, because they are the
same idea seen from three sides: ONE scenario is not one sentence, and one pass
rate is not a fact.

  * FAMILIES (§4 guard 6). Each core cell carries alternate wordings of its
    turns; expansion turns those into sibling conversations sharing the cell's
    assertions. A family passes when at least four of its five members pass, so
    a model that lands one wording by keyword accident does not read as
    understanding, and a single unlucky phrasing does not read as a regression.
  * SPLITS (§4 guard 4). Training-visible / validation / held-out, assigned at
    FAMILY level — putting one wording of a request in training and another in
    the held-out set would leak the answer and flatter the round. Assignment is
    derived from the family id by hash, so it is stable across runs and machines
    without a stored file, and re-deriving it is not a coin flip.
  * THE NOISE FLOOR (§4 guard 5). At ~140 scenarios a difference of a few
    points is variance. Bootstrap confidence intervals accompany a rate, and
    two runs whose intervals overlap are not a claimed improvement.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass

from intergen.tests.conversations import Conversation

# Separates a base cell's id from the wording label in a variant's id, so a
# family can always be recovered from an id by splitting on it.
VARIANT_SEP = "#"

# The splits, in the ratified order and proportion.
SPLIT_TRAIN = "train_visible"
SPLIT_VALIDATION = "validation"
SPLIT_HELD_OUT = "held_out"
SPLIT_RATIOS: tuple[tuple[str, float], ...] = (
    (SPLIT_TRAIN, 0.70), (SPLIT_VALIDATION, 0.15), (SPLIT_HELD_OUT, 0.15),
)

# A family passes when this many of its members do. Five wordings, four must
# hold: one wording may fail without condemning the class, two may not.
FAMILY_PASS_NUMERATOR = 4
FAMILY_PASS_DENOMINATOR = 5


def family_id_of(conversation_id: str) -> str:
    """The family a conversation id belongs to — itself, for a base cell."""
    return conversation_id.split(VARIANT_SEP, 1)[0]


def expand_paraphrase_families(
        conversations: list[Conversation]) -> list[Conversation]:
    """Expand each cell's alternate wordings into sibling conversations.

    One turn varies at a time: the sibling swaps that turn's text and leaves
    every other turn at its base wording, so a family member isolates the effect
    of a single wording rather than a combination of them. Assertions, category,
    capabilities and outcome are inherited unchanged — that is the whole point,
    since the class invariant is what must hold across wordings.

    A cell with no phrasings passes through untouched, so this is safe to run
    over the whole corpus.
    """
    out: list[Conversation] = []
    for conv in conversations:
        has_phrasings = any(turn.phrasings for turn in conv.turns)
        if not has_phrasings:
            out.append(conv)
            continue
        # The BASE that goes out has its wordings consumed too. Emitting the
        # original object here left the wordings attached to it, so expanding an
        # already-expanded list produced the siblings a second time — the
        # expansion has to be safe to run over a corpus that may already have
        # been through it. The caller's own list is not touched.
        base = deepcopy(conv)
        for turn in base.turns:
            turn.phrasings = []
        out.append(base)
        seen: set[str] = {conv.id}
        for turn_index, turn in enumerate(conv.turns):
            for phrasing in turn.phrasings:
                key = (phrasing.label if len(conv.turns) == 1
                       else f"t{turn_index + 1}-{phrasing.label}")
                variant_id = f"{conv.id}{VARIANT_SEP}{key}"
                if variant_id in seen:
                    raise ValueError(
                        f"paraphrase family for {conv.id!r} produced a duplicate "
                        f"member id {variant_id!r} — two wordings share a label")
                seen.add(variant_id)
                clone = deepcopy(conv)
                clone.id = variant_id
                clone.paraphrase_of = conv.id
                clone.turns[turn_index].user = phrasing.text
                # Consumed into the member's own text; dropping them keeps a
                # second expansion idempotent.
                clone.turns[turn_index].phrasings = []
                out.append(clone)
    return out


@dataclass
class FamilyResult:
    """How one family fared: its members, how many passed, and the verdict."""
    family: str
    members: list[str]
    passed: int
    total: int
    grade: str          # PASS / FAIL
    member_grades: dict[str, str]

    @property
    def unanimous(self) -> bool:
        return self.passed == self.total

    def to_dict(self) -> dict:
        return {
            "family": self.family, "members": list(self.members),
            "passed": self.passed, "total": self.total, "grade": self.grade,
            "unanimous": self.unanimous, "member_grades": dict(self.member_grades),
        }


def _threshold_for(total: int) -> int:
    """How many members must pass, scaled to the family's real size.

    The ratified bar is four of five. A family that is smaller (or larger) than
    five uses the same proportion, rounded up, and a family of one must pass —
    scaling by proportion rather than pinning the literal 4 keeps a three-member
    family from passing on two out of three, which four-of-five would not allow.
    """
    if total <= 1:
        return total
    scaled = (total * FAMILY_PASS_NUMERATOR + FAMILY_PASS_DENOMINATOR - 1) \
        // FAMILY_PASS_DENOMINATOR
    return max(1, min(total, scaled))


def grade_families(conversation_results: list[dict]) -> list[FamilyResult]:
    """Group graded conversations into families and grade each family.

    A member counts as passing on a PASS grade only: MIXED means something was
    wrong with it, and a family verdict that treated MIXED as success would be
    the same kind of flattery the binding-judge change removed.
    """
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for conv in conversation_results:
        fam = conv.get("paraphrase_of") or family_id_of(conv.get("id", ""))
        if fam not in grouped:
            grouped[fam] = []
            order.append(fam)
        grouped[fam].append(conv)

    results: list[FamilyResult] = []
    for fam in order:
        members = grouped[fam]
        member_grades = {m.get("id", ""): m.get("grade", "FAIL") for m in members}
        passed = sum(1 for g in member_grades.values() if g == "PASS")
        total = len(members)
        results.append(FamilyResult(
            family=fam, members=[m.get("id", "") for m in members],
            passed=passed, total=total,
            grade="PASS" if passed >= _threshold_for(total) else "FAIL",
            member_grades=member_grades))
    return results


def family_variance(results: list[FamilyResult]) -> list[FamilyResult]:
    """The families that did NOT hold across their wordings — the path-luck
    subset, which is the reason families exist at all."""
    return [r for r in results if not r.unanimous]


def split_for_family(family: str, *, salt: str = "") -> str:
    """Which split a family belongs to.

    Derived from the family id (and an optional salt, so a deliberate reshuffle
    is possible without hand-editing anything), never drawn at random: two
    machines, two runs and two people must agree on what is held out, and a
    stored assignment file is one more thing that can drift from the corpus.
    """
    digest = hashlib.sha256(f"{salt}|{family}".encode()).digest()
    # 16 bits is ample resolution for three buckets and keeps the arithmetic
    # readable; the value is a fraction of the way through the unit interval.
    position = int.from_bytes(digest[:2], "big") / 65536.0
    cumulative = 0.0
    for name, ratio in SPLIT_RATIOS:
        cumulative += ratio
        if position < cumulative:
            return name
    return SPLIT_RATIOS[-1][0]


def assign_splits(families: list[str], *, salt: str = "") -> dict[str, str]:
    """Split assignment for every family, family-level by construction."""
    return {fam: split_for_family(fam, salt=salt) for fam in families}


def split_of_conversation(conversation_id: str, *, salt: str = "") -> str:
    """The split a conversation inherits from its family. Every wording of a
    request lands in the same split — the leak this prevents is the whole
    reason the assignment is family-level."""
    return split_for_family(family_id_of(conversation_id), salt=salt)


def families_due_for_refresh(families: list[str], *, round_index: int,
                             fraction: float = 0.20) -> list[str]:
    """The share of families that rotate to freshly-authored wordings this round.

    A rolling window over the families in a stable order: round 0 takes the
    first fifth, round 1 the next, and so on, wrapping. Nothing ossifies, and
    no family is refreshed twice before every family has been refreshed once —
    which a random draw would not guarantee.
    """
    if not families or fraction <= 0:
        return []
    ordered = sorted(families)
    size = max(1, round(len(ordered) * fraction))
    start = (round_index * size) % len(ordered)
    picked = [ordered[(start + i) % len(ordered)] for i in range(size)]
    # A wrap can revisit a family within one round's window on a small corpus;
    # de-duplicate while keeping order so the list means what it says.
    seen: set[str] = set()
    return [f for f in picked if not (f in seen or seen.add(f))]
