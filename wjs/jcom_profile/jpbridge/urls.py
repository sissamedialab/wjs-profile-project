import re

from django.conf import settings
from django.urls import path, re_path

from .views_fake_jp import FakeJpView
from .views_mypayments import MyPaymentsView
from .views_sgp import GetSGPCodeView

# Built from settings.WJS_JP_URLS so the accepted <service_prefix> values are never out of sync
# with the per-journal (url, service_prefix) tuples that define them - a local settings override
# (e.g. a test deployment's own convention) is automatically picked up too, since this module is
# only imported once Django settings are fully loaded. See resolve_service_prefix() in
# jpbridge/logic.py.
_service_prefixes = sorted({entry[1] for entry in settings.WJS_JP_URLS.values() if entry})
_service_prefix_pattern = "|".join(re.escape(prefix) for prefix in _service_prefixes)

urlpatterns = [
    path("myPayments/", MyPaymentsView.as_view(), name="my_payments"),
    # Legacy wjapp URL (jp.identity_jsp): kept as-is so Jp needs no changes when talking to WJS
    # instead. See GetSGPCodeView.
    re_path(
        rf"^(?P<service_prefix>{_service_prefix_pattern})/services/getSGPcodfpf\.jsp$",
        GetSGPCodeView.as_view(),
        name="get_sgp_code",
    ),
    path("fake-jp/", FakeJpView.as_view(), name="fake_jp"),
]
