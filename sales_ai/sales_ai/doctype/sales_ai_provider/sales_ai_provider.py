# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from sales_ai.llm.model import validate_params_field


class SalesAIProvider(Document):
	def validate(self) -> None:
		validate_params_field(self.extra_params, "Extra Params")
