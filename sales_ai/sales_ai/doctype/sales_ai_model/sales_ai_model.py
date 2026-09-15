# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai.llm.model import validate_params_field


class SalesAIModel(Document):
	def validate(self) -> None:
		validate_params_field(self.params, "Params")
		self._require_credentials()

	def _require_credentials(self) -> None:
		"""A model with neither a provider nor its own endpoint can never be called."""
		if self.provider or self.base_url:
			return
		if self.get_password("api_key", raise_exception=False):
			return
		frappe.throw(
			_("Link a Provider, or set an API Key or Base URL on the model itself."),
			title=_("Missing Credentials"),
		)
