"""Iri Collections - URL Configuration"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView

# ─── Page Views ─────────────────────────────────────────────────
urlpatterns = [
    # Django admin
    path("django-admin/", admin.site.urls),
    # API endpoints
    path("api/auth/", include("accounts.urls")),
    path("api/store/", include("store.urls")),
    path("api/payments/", include("payments.urls")),
    # Frontend pages (served by Django templates)
    path("", TemplateView.as_view(template_name="index.html"), name="home"),
    path("login/", TemplateView.as_view(template_name="login.html"), name="login-page"),
    path(
        "signup/", TemplateView.as_view(template_name="signup.html"), name="signup-page"
    ),
    path(
        "catalog/",
        TemplateView.as_view(template_name="catalog.html"),
        name="catalog-page",
    ),
    path(
        "product/<slug:slug>/",
        TemplateView.as_view(template_name="product_detail.html"),
        name="product-page",
    ),
    path("cart/", TemplateView.as_view(template_name="cart.html"), name="cart-page"),
    path("wishlist/", TemplateView.as_view(template_name="wishlist.html"), name="wishlist-page"),
    path(
        "checkout/",
        TemplateView.as_view(template_name="checkout.html"),
        name="checkout-page",
    ),
    path(
        "orders/", TemplateView.as_view(template_name="orders.html"), name="orders-page"
    ),
    path(
        "invoice/<int:pk>/",
        TemplateView.as_view(template_name="invoice.html"),
        name="invoice-page",
    ),
    path("shipping/", TemplateView.as_view(template_name="shipping.html"), name="shipping-page"),
    path("returns/", TemplateView.as_view(template_name="returns.html"), name="returns-page"),
    path("contact/", TemplateView.as_view(template_name="contact.html"), name="contact-page"),
    path("about/", TemplateView.as_view(template_name="about.html"), name="about-page"),
    path(
        "admin-dashboard/",
        TemplateView.as_view(template_name="admin_dashboard.html"),
        name="admin-dashboard",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
