from django.urls import path

from .views_fake_jp import FakeJpView
from .views_mypayments import MyPaymentsView
from .views_sgp import GetSGPCodeView

urlpatterns = [
    path("myPayments/", MyPaymentsView.as_view(), name="my_payments"),
    # Legacy wjapp URL (jp.identity_jsp): kept as-is so Jp needs no changes when talking to WJS instead.
    path("services/getSGPcodfpf.jsp", GetSGPCodeView.as_view(), name="get_sgp_code"),
    path("fake-jp/", FakeJpView.as_view(), name="fake_jp"),
]
