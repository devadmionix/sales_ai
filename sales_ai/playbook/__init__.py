# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Deterministic recipes: the steps are a person's, only the judgement is the model's.

A playbook sits between a chat turn, where the model decides everything, and a scheduled
script, where nothing is decided at all. The order of the steps, which tools run and with
what arguments are all fixed by whoever wrote the playbook. An AI step can read the work
so far and form an opinion, but it cannot call a tool and cannot change a record — so a
playbook's blast radius is exactly the tool steps you can see in the table.

Tool steps are not privileged. They go through `sales_ai.guard.policy` like any other
call, which means the same approval rules, the same refusals and the same write cap apply
whether a tool was chosen by the model or written into a playbook by hand.
"""

from sales_ai.playbook.engine import advance, answer, dry_run, resume_due, start

__all__ = ["advance", "answer", "dry_run", "resume_due", "start"]
