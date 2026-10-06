"""
Article -> JCAP/EM-style metadata XML export.

Architecture (see wjs_review's CLAUDE.md and the export spec for the full rationale):

- ``dto.py`` -- plain ``@dataclass`` objects, fully independent of Django models.
- ``formatters.py`` -- small, pure, stateless value formatters (language codes, dates).
- ``mappers.py`` -- free functions ``Article``/``FrozenAuthor``/... -> ``dto.py`` instances,
  doing all the ORM lookups and business-logic decisions.
- ``service.py`` -- ``serialize_article_to_metadata_xml(article)``, the public entry point.
"""
