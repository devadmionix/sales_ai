# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""One remembered judgement about one record.

The name is computed rather than generated, so that "one insight per subject per kind" is
enforced by the primary key instead of by every caller remembering to look first. A second
nightly run does not append a second opinion; it overwrites the first, and the history of
what was thought last week lives in the version log if anybody wants it.

Nothing here decides anything. The scoring lives in `sales_ai/intelligence/`, and this
doctype's only job is to refuse to store a score that cannot be explained or believed.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

# Where Low becomes Medium and Medium becomes High. Thirds, because the bands are a reading
# aid and any other split would be a claim about the distribution that we have not earned.
MEDIUM = 34.0
HIGH = 67.0


class SalesAIInsight(Document):
	def autoname(self) -> None:
		self.name = f"{frappe.scrub(self.kind)}::{self.subject_doctype}::{self.subject_name}"[:140]

	def validate(self) -> None:
		self._check_value()
		self._check_explainable()
		self._set_band()

	def _check_value(self) -> None:
		self.value = flt(self.value, 1)
		if not 0 <= self.value <= 100:
			frappe.throw(
				_("A score of {0} is outside 0 to 100. This is a bug in the scorer, not a finding.").format(
					self.value
				)
			)

	def _check_explainable(self) -> None:
		"""A number with no reasons is a number nobody can argue with, which is the problem.

		Refused rather than stored with an empty table, because an unexplained score still
		shows up in a list view and still gets acted on, and by then the fact that nothing
		backs it is invisible.
		"""
		if not self.factors:
			frappe.throw(_("A score with no factors cannot be explained, so it is not stored."))

	def _set_band(self) -> None:
		"""Always derived, never accepted from the caller.

		The band says how big the value is, not whether it is good news. High churn risk is
		bad and a high lead score is good; reading that off is the reader's job.
		"""
		if self.value >= HIGH:
			self.band = "High"
		elif self.value >= MEDIUM:
			self.band = "Medium"
		else:
			self.band = "Low"
