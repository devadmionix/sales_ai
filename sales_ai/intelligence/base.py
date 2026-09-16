# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The shape every scorer produces, and the one way any of it gets stored.

Split from the scorers on purpose. A `Score` is a plain value with no database behind it,
so the arithmetic in `churn.py` and whatever follows it can be checked against made-up
numbers in milliseconds. `record` is the only function here that touches a site, and it is
deliberately dull — it decides nothing, it just writes down what it was handed.

The one rule worth stating out loud: **a scorer that cannot see enough to have an opinion
returns `None`, not a middling number.** Fifty out of a hundred reads like a considered
judgement and is indistinguishable from one at the point it gets acted on. Silence is the
only honest output when the evidence is not there, and it is the same rule the eval harness
applies to itself: untested is not passed.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import now

# Scores are 0 to 100 because that is the range the doctype accepts and the range a person
# reads without being told the units.
FLOOR = 0.0
CEILING = 100.0


@dataclass(frozen=True)
class Factor:
	"""One reason the number is what it is.

	`detail` carries the figures the factor was read off, so the contribution can be checked
	by hand rather than taken on trust. A factor whose detail is empty is a label, and a
	label is not an explanation.
	"""

	label: str
	contribution: float
	detail: str = ""


@dataclass(frozen=True)
class Score:
	"""A judgement, its reasons, and the sentence a person reads first.

	`summary` is assembled by the scorer from its own factors. It is never generated, so it
	can never say something the factors do not support.
	"""

	value: float
	factors: tuple[Factor, ...]
	summary: str


def clamp(value: float) -> float:
	"""Hold a score inside the range whatever the factors add up to.

	Contributions are written to be readable — "twice as overdue as usual, +40" — not to be
	guaranteed to sum inside the range. Clamping here means a scorer can be tuned without
	anyone having to re-check that the worst case still lands under a hundred.
	"""
	return max(FLOOR, min(CEILING, value))


def record(
	kind: str,
	subject_doctype: str,
	subject_name: str,
	score: Score,
	model_version: str,
) -> str:
	"""Store a score, replacing whatever was thought about the same subject before.

	There is one row per subject per kind, guaranteed by the computed name rather than by
	this function remembering to look. Yesterday's opinion is overwritten, not appended to;
	the trail of what changed is the version log, which is where a trail belongs.

	Permissions are ignored because nobody has them. The doctype grants no create or write
	to any role — it is written by this layer and read by everyone else — so the only way to
	save is to say so explicitly.
	"""
	name = f"{frappe.scrub(kind)}::{subject_doctype}::{subject_name}"[:140]

	if frappe.db.exists("Sales AI Insight", name):
		doc = frappe.get_doc("Sales AI Insight", name)
		doc.factors = []
	else:
		doc = frappe.new_doc("Sales AI Insight")
		doc.kind = kind
		doc.subject_doctype = subject_doctype
		doc.subject_name = subject_name

	doc.value = score.value
	doc.summary = score.summary
	doc.model_version = model_version
	doc.computed_on = now()
	for factor in score.factors:
		doc.append(
			"factors",
			{
				"label": factor.label,
				"contribution": factor.contribution,
				"detail": factor.detail,
			},
		)

	doc.save(ignore_permissions=True)
	return doc.name
