"""The monthly closing: close a month, read the closings, verify one, reopen one (TASK-009).

The API only INSERTs the row `monthly_closings`: its trigger computes every figure, the hash and
the breakdown from the ledger (docs/financial-model.md). Nothing here adds up money.
"""
