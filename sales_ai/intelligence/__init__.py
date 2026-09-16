# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The part of the app that forms opinions, and the only part allowed to write them down.

Everything in here is arithmetic over records that already exist. No model is called, no
prompt is built, and nothing is generated. That is the whole point: a score the agent can
cite has to be one a person can re-derive by hand from the same rows, or citing it is just
laundering a guess through a doctype.

The agent reads these. It has no tool that writes one.
"""
