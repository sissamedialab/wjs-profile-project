"""
Historical replay of the automatic editor assignment, with its management command and tests.

Throw-away tooling for an imported copy of production, kept in its own subpackage so that removing it is
deleting this directory plus the one-line ``management/commands/replay_automatic_assignment.py`` shim
(Django only finds management commands in the app's own ``management/commands``).
"""
