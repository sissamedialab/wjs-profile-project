class HtmxMixin:
    """Mixin to detect if request is an htmx request."""

    htmx = False

    def dispatch(self, request, *args, **kwargs):
        if "HX-Request" in request.headers and request.headers["HX-Request"]:
            self.htmx = True
        return super().dispatch(request, *args, **kwargs)
