# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Looking at reminders, and changing the ones that are yours.

Creating one is `create_follow_up` in `tools.write`, next to the other things that write to
a record. These two are the other half: seeing what is outstanding, and dealing with it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard import followups
from sales_ai.llm.tool import tool
from sales_ai.tools import register
from sales_ai.tools.read import SalesDocType

FollowUpStatus = Literal["Open", "Closed", "Cancelled"]


@tool(
	writes=False,
	risk="none",
	description=f"""List follow-up reminders.

Use this to answer "what have I got on", "what is outstanding on this account", or to find
a reminder before changing it. Only reminders this user is allowed to see come back.

Defaults to open ones. At most {followups.MAX_LIST}.""",
)
def list_follow_ups(
	doctype: Annotated[SalesDocType | None, "Only reminders about this type of record."] = None,
	name: Annotated[str | None, "Only reminders about this exact record."] = None,
	user: Annotated[str | None, "Only reminders on this colleague's list, by login email."] = None,
	status: Annotated[FollowUpStatus, "Which reminders to list."] = "Open",
	limit: Annotated[int, "How many to return."] = 20,
) -> dict[str, Any]:
	return followups.list_follow_ups(
		doctype=doctype, name=name, user=user, status=status, limit=limit
	)


@tool(
	writes=True,
	# Changes one row on a to-do list. Closing somebody's reminder loses the fact that it
	# was outstanding, which is why it is not "low".
	risk="medium",
	action="change follow-up {name}",
	description="""Change a follow-up reminder: move its date, rewrite it, close it, or hand
it to a colleague.

Only reminders on this user's own list, or ones they created, can be changed. Find the
reminder's ID with list_follow_ups first.

Use "Closed" when the follow-up was done, and "Cancelled" when it turned out not to be
needed. They are not the same thing and the difference is what the user will be looking at
later.

A colleague you hand it to must already be able to see the record it is about.""",
)
def update_follow_up(
	name: Annotated[str, "The reminder's ID, from list_follow_ups."],
	date: Annotated[str | None, "New date, as YYYY-MM-DD."] = None,
	description: Annotated[str | None, "New text, in plain text."] = None,
	status: Annotated[FollowUpStatus | None, "New status."] = None,
	allocate_to: Annotated[str | None, "Hand it to this colleague, by login email."] = None,
) -> dict[str, Any]:
	return followups.update_follow_up(
		name,
		date=date,
		description=description,
		status=status,
		allocate_to=allocate_to,
		tool="update_follow_up",
	)


register(list_follow_ups)
register(update_follow_up)
