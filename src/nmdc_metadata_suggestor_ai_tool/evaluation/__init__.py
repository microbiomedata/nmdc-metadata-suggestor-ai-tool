"""Scoring helpers for evaluating metadata suggestions against reference values.

Nothing here talks to a model or the network. The runners that do live under
``scripts/``; this package holds the parts worth unit-testing: how reference
values are read, how a suggestion is judged against them, and how two runs
are compared.
"""
