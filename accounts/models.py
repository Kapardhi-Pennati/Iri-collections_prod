from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

class User(AbstractUser):
    """Custom user with RBAC roles."""

    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("customer", "Customer"),
    )
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="customer")
    phone = models.CharField(max_length=15, blank=True)
    full_name = models.CharField(max_length=150, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return self.email

    @property
    def is_admin_user(self):
        return self.role == "admin"


class OTP(models.Model):
    """Model to store OTPs for email verification."""
    email = models.EmailField()
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)

    class Meta:
        db_table = "otps"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} - {self.otp_code}"

    def is_valid(self):
        # Valid for 15 minutes
        return timezone.now() < self.created_at + timezone.timedelta(minutes=15)


class Address(models.Model):
    """Model to store user addresses."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    name = models.CharField(max_length=150, help_text="e.g., Home, Work", blank=True)
    street = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)
    phone = models.CharField(max_length=15, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "addresses"

    def __str__(self):
        return f"{self.user.email} - {self.city} ({self.pincode})"

    def save(self, *args, **kwargs):
        if self.is_default:
            # Unset default for other addresses
            Address.objects.filter(user=self.user).update(is_default=False)
        super().save(*args, **kwargs)
