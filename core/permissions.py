from rest_framework.permissions import BasePermission


class RolePermission(BasePermission):
    """
    Shared RBAC base permission.

    Every authenticated API caller must belong to an allowed application role.
    """

    allowed_roles = ()

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return user.is_superuser or getattr(user, "role", None) in self.allowed_roles


class IsAdminUser(RolePermission):
    allowed_roles = ("admin",)


class IsCustomerUser(RolePermission):
    allowed_roles = ("customer",)


class IsAdminOrCustomerUser(RolePermission):
    allowed_roles = ("admin", "customer")
