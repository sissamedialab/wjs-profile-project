"""Check the migration graph of this plugin.

Tests skip migrations (``SkipMigrations``), so a conflict between two migrations with the same parent would only
show up on deploy: check it here, reading the migration modules directly.
"""

import importlib
import pkgutil

from plugins.wjs_review import migrations as wjs_review_migrations


def test_wjs_review_migrations_have_a_single_leaf():
    """Every migration but one is the parent of another: two leaves would make ``migrate`` refuse to run."""
    names = [module.name for module in pkgutil.iter_modules(wjs_review_migrations.__path__)]
    parents = set()
    for name in names:
        migration = importlib.import_module(f"{wjs_review_migrations.__name__}.{name}").Migration
        parents.update(dependency for app, dependency in migration.dependencies if app == "wjs_review")
    leaves = sorted(set(names) - parents)
    assert len(leaves) == 1, f"wjs_review migrations should have a single leaf, found: {leaves}"
