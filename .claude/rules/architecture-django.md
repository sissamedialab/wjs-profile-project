---
description: Architecture rules for Django — SOLID principles, design patterns, and app conventions
---

# WJS Django Architecture Rules

## SOLID principles

- **Single Responsibility**: one class/module = one concern
- **Open/Closed**: extend via subclassing or registry, not by editing core code
- **Liskov Substitution**: subclasses must honour parent contracts
- **Interface Segregation**: small focused interfaces over fat ones
- **Dependency Inversion**: depend on abstractions; inject concrete implementations

## Model architecture

All model filtering (except filtering logic which uses django-filter for user input based filters) must be wrapped
in custom queryset attached to each model. Avoid filtering directly against `.objects` in views, forms, or business
logic classes — always go through a queryset method defined on the model's manager, so the filtering logic stays
in one place and is reusable and testable in isolation.

## Forms architecture

Forms must be used directly in case the form job is to:

- validate input
- save model and its related objects

In case side-effects are required, form save method must be overridden to instantiate a custom business logic class (see below),
call the `run` method defined by the business logic class, refresh the instance and return it
If any ValidationError is raised, it must be added as a non-field-related error.

Sample pattern:

```python
def save(self, commit: bool = True) -> Booking:
    try:
        service = self.get_logic_instance()
        service.run()
    except ValidationError as e:
        self.add_error(None, e)
        raise
    self.instance.refresh_from_db()
    return self.instance
```

The form must be independent from the view and the request data: all context-related data must be passed to the form as arguments to the constructor.

## Business logic architecture

When a business logic class is needed, it must be designed as a dataclass that defines as attribute all the data required to run the logic (except runtime-behavior altering arguments).

It must define a `run` method that executes the logic, which can take optional parameters to the behavior.

The business logic class must be independent from the request data: all context-related data must be passed as arguments to the constructor.

Avoid to overload a business logic class with too much logic: it should only handle a single use case. If multiple use cases share a lot of logic, consider extracting the shared behaviour into a mixin or a separate helper class that each business logic class composes, rather than subclassing one business logic class from another.

The `run` method must run its logic in a transaction. If race conditions are possible, consider using `select_for_update` method to fetch a locked instance.

After the transaction is initiated and object is fetched (if locking is needed), the logic must check that requirements are met before proceeding: if any requirement fails,
a ValidationError must be raised in the `run` method.

The business logic class must be executable outside of a view / form.

## Views architecture

View must include the least amount of code possible.

The goal of a view must be gather user input and create the context for template rendering.

Objects filtering must be offloaded to django-filter when more that 3 user filter criteria is used or when filters requires
user to select items from an existing list. Implicit filters (like permission-based filters, tenant scoping, or
soft-delete visibility) belong in the model's custom queryset (see *Model architecture* above), not duplicated in
every view.

Form-based views must offload all the logic to the linked form for validation, save logic.

When the same logic is used in multiple views, create a mixin to wrap the logic and subclass views classes from this mixin
to reuse the logic.

In case a separate business logic class is used by the form, the `form_valid` method must wrap `form.save` call in a `try` / `except` block:
in case any ValidationError is raised, the except must call `self.form_invalid` method to let the validation error being managed.

Sample code:

```python
def form_valid(self, form: BookingForm) -> HttpResponse:
    try:
        return super().form_valid(form)
    except (ValidationError) as e:
        return super().form_invalid(form)
```

## Registry / Decorator pattern

Prefer registries over direct imports for extensible, plugin-like behaviour. Components
register themselves; core code iterates the registry rather than importing each component.

## Dependency Injection

Pass dependencies into constructors or factory functions. Avoid module-level singletons
and global state that makes testing and substitution difficult.

## Reusable packages vs project code

- Reusable logic → extract to a `nephila-apps` / `nephila-widgets` package with its own
  towncrier changelog and semver versioning
- Project-specific logic → keep in the project app; do not prematurely extract
- The boundary: if two unrelated projects need the same code, it belongs in a package

## Dependency selection

- Always verify the compatibility of the selected library with the project
- Avoid using libraries that are not actively maintained
- Prefer libraries with a permissive license (MIT, Apache, BSD, etc.)
