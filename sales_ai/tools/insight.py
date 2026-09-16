# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools that read what the app already worked out, rather than working it out again.

The scores behind these are arithmetic, computed overnight by `sales_ai/intelligence/` and
stored with their reasons. The model can quote them and cite the factors; it cannot produce
one, adjust one, or reason its way to a different number and present that as the score.

The description below spends most of its words on what an absent score means, because that
is the thing a model gets wrong. A customer with no churn score is not a safe customer — it
is a customer the model declined to judge. Left unsaid, "no risk found" is exactly how it
would be reported.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard.insights import DEFAULT_LIMIT, MAX_LIMIT, read_insights
from sales_ai.intelligence.churn import KIND, MIN_GAPS
from sales_ai.llm.tool import tool
from sales_ai.tools import register

Band = Literal["Low", "Medium", "High"]


@tool(
	writes=False,
	risk="none",
	description=f"""Read the churn risk already computed for customers.

Churn risk is how overdue a customer is against their own ordering rhythm, not against the
calendar, softened when there is an open opportunity or a recent quotation. It is scored 0
to 100 overnight by fixed arithmetic — never by a model — and every score comes back with
the factors it was built from. Cite those factors rather than restating the number alone.

A customer that is absent from the results has **not** been judged low risk. Customers with
fewer than {MIN_GAPS + 1} orders have no rhythm to be late against, so they are deliberately
left unscored. Say that they are unscored; do not report them as safe, and do not estimate a
score yourself.

Each score carries the date it was computed. If it matters that a score is a day or two old,
say so rather than implying it is live.

Results cover only the customers the current user is allowed to see.""",
)
def get_churn_risk(
	customer: Annotated[
		str | None, "One customer to look up. Omit to list the riskiest customers."
	] = None,
	band: Annotated[
		Band | None, "Only return scores in this band. Use 'High' for the accounts at risk."
	] = None,
	limit: Annotated[int, f"How many customers to return, up to {MAX_LIMIT}."] = DEFAULT_LIMIT,
) -> dict[str, Any]:
	return read_insights(
		KIND,
		subject_doctype="Customer",
		subject_name=customer,
		band=band,
		limit=limit,
	)


register(get_churn_risk)
