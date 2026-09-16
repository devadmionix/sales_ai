# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Measuring whether the agent is good enough to be trusted with the autonomy it has.

`metrics` scores one finished run against one case and knows nothing about the database.
`runner` executes the golden dataset and records the result. The split is so the scoring
rules can be tested exhaustively without a provider, which is the only way they get
tested often enough to be believed.
"""
