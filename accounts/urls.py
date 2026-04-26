from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'addresses', views.AddressViewSet, basename='address')

urlpatterns = [
    path("request-otp/", views.RequestOTPView.as_view(), name="request_otp"),
    path("verify-otp/", views.VerifyOTPView.as_view(), name="verify_otp"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("reset-password/", views.ResetPasswordView.as_view(), name="reset_password"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("refresh/", views.RefreshTokenCookieView.as_view(), name="token_refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("", include(router.urls)),
]
