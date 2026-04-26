from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r"admin/products", views.AdminProductViewSet, basename="admin-products")
router.register(
    r"admin/categories", views.AdminCategoryViewSet, basename="admin-categories"
)

urlpatterns = [
    # Public
    path("categories/", views.CategoryListView.as_view(), name="category-list"),
    path("products/", views.ProductListView.as_view(), name="product-list"),
    path(
        "products/<slug:slug>/",
        views.ProductDetailView.as_view(),
        name="product-detail",
    ),
    # Cart & Wishlist (authenticated)
    path("cart/", views.CartView.as_view(), name="cart"),
    path("wishlist/", views.WishlistView.as_view(), name="wishlist"),
    path("wishlist/toggle/", views.WishlistToggleView.as_view(), name="wishlist-toggle"),
    # Orders (authenticated)
    path("orders/pincode-verify/", views.PincodeVerifyView.as_view(), name="pincode-verify"),
    path("orders/", views.OrderListView.as_view(), name="order-list"),
    path("orders/create/", views.OrderCreateView.as_view(), name="order-create"),
    path("orders/confirm-payment/", views.OrderConfirmPaymentView.as_view(), name="order-confirm-payment"),
    path("orders/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<int:pk>/", views.OrderDetailView.as_view(), name="order-detail"),
    # Admin
    path("admin/orders/", views.AdminOrderListView.as_view(), name="admin-orders"),
    path(
        "admin/orders/<int:pk>/",
        views.AdminOrderDetailView.as_view(),
        name="admin-order-detail",
    ),
    path(
        "admin/orders/<int:pk>/status/",
        views.AdminOrderStatusView.as_view(),
        name="admin-order-status",
    ),
    path(
        "admin/orders/<int:pk>/tracking/",
        views.AdminOrderTrackingUploadView.as_view(),
        name="admin-order-tracking",
    ),
    path(
        "admin/analytics/", views.AdminAnalyticsView.as_view(), name="admin-analytics"
    ),
    path(
        "admin/traffic/", views.AdminTrafficView.as_view(), name="admin-traffic"
    ),
    # Router
    path("", include(router.urls)),
]

