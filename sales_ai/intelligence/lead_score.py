# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Lead scoring based on historical conversion patterns.

Scores each open lead 0–100 by comparing its attributes (source, territory, industry)
against what converted leads looked like in the past. This is pattern matching on the
company's own data, not a generic model.

A lead whose source converted 60% of the time scores higher than one whose source
converted 10%. Multiple signals are combined with simple weights.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt


# Weights for each scoring dimension (must sum to 1.0)
WEIGHTS = {
	"utm_source": 0.35,
	"territory": 0.25,
	"industry": 0.20,
	"recency": 0.20,
}


def score_leads(company: str | None = None, limit: int = 20) -> dict:
	"""Score all open leads and return them ranked by conversion likelihood."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	# Get historical conversion rates by attribute
	source_rates = _conversion_rates_by("utm_source", company)
	territory_rates = _conversion_rates_by("territory", company)
	industry_rates = _conversion_rates_by("industry", company)

	# Get open leads
	open_leads = frappe.db.sql(
		"""
		SELECT name, lead_name, utm_source, territory, industry, company_name,
		       creation, status, lead_owner
		FROM `tabLead`
		WHERE status NOT IN ('Converted', 'Do Not Contact')
		  AND company = %s
		ORDER BY creation DESC
		""",
		(company,),
		as_dict=True,
	)

	if not open_leads:
		return {"leads": [], "total": 0, "message": "No open leads found."}

	# Get the age range for recency scoring
	if open_leads:
		max_age = max(
			(frappe.utils.date_diff(frappe.utils.nowdate(), l.creation) for l in open_leads),
			default=1,
		)
		max_age = max(max_age, 1)  # avoid division by zero

	scored = []
	for lead in open_leads:
		source_score = source_rates.get(lead.utm_source, 0) if lead.utm_source else 0
		territory_score = territory_rates.get(lead.territory, 0) if lead.territory else 0
		industry_score = industry_rates.get(lead.industry, 0) if lead.industry else 0

		# Recency: newer leads score higher (they haven't gone cold yet)
		age_days = frappe.utils.date_diff(frappe.utils.nowdate(), lead.creation)
		recency_score = max(0, (1 - age_days / max_age)) * 100

		total_score = flt(
			source_score * WEIGHTS["utm_source"]
			+ territory_score * WEIGHTS["territory"]
			+ industry_score * WEIGHTS["industry"]
			+ recency_score * WEIGHTS["recency"],
			1,
		)

		factors = []
		if lead.utm_source and source_score > 0:
			factors.append(f"Source '{lead.utm_source}' converts at {flt(source_score, 1)}%")
		if lead.territory and territory_score > 0:
			factors.append(f"Territory '{lead.territory}' converts at {flt(territory_score, 1)}%")
		if lead.industry and industry_score > 0:
			factors.append(f"Industry '{lead.industry}' converts at {flt(industry_score, 1)}%")
		if age_days <= 7:
			factors.append("Fresh lead (less than a week old)")
		elif age_days <= 30:
			factors.append(f"Recent lead ({age_days} days old)")
		else:
			factors.append(f"Aging lead ({age_days} days old)")

		scored.append({
			"lead": lead.name,
			"lead_name": lead.lead_name,
			"company_name": lead.company_name,
			"source": lead.utm_source,
			"territory": lead.territory,
			"industry": lead.industry,
			"status": lead.status,
			"lead_owner": lead.lead_owner,
			"score": total_score,
			"band": "High" if total_score >= 60 else "Medium" if total_score >= 30 else "Low",
			"factors": factors,
		})

	scored.sort(key=lambda x: x["score"], reverse=True)

	return {
		"leads": scored[:limit],
		"total": len(scored),
		"conversion_rates": {
			"by_source": source_rates,
			"by_territory": territory_rates,
			"by_industry": industry_rates,
		},
	}


def _conversion_rates_by(field: str, company: str) -> dict[str, float]:
	"""Calculate conversion rate for each value of a field."""
	rows = frappe.db.sql(
		f"""
		SELECT `{field}` AS attr,
		       COUNT(*) AS total,
		       SUM(CASE WHEN status = 'Converted' THEN 1 ELSE 0 END) AS converted
		FROM `tabLead`
		WHERE company = %s
		  AND `{field}` IS NOT NULL
		  AND `{field}` != ''
		GROUP BY `{field}`
		HAVING total >= 2
		""",
		(company,),
		as_dict=True,
	)
	return {r.attr: flt(r.converted / r.total * 100, 1) for r in rows if r.total}
