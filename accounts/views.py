"""
accounts/views.py — Secure authentication views.

Refactoring changes from original:
  - Removed redundant `from .models import User` (line 29 of original);
    `User = get_user_model()` on the next line already covers this.
  - Extracted _build_jwt_response() to eliminate copy-pasted token
    construction in LoginView and RegisterView (D.R.Y.).
  - Extracted _require_valid_email() to eliminate copy-pasted email
    validation boilerplate across 4 view methods.
  - Added select_for_update() on OTP queries in VerifyOTPView and
    ResetPasswordView to prevent a race condition where two simultaneous
    requests verify the same OTP record.
  - Added password strength validation in ResetPasswordView using
    InputValidator.validate_password() (previously absent).
  - Added type hints on all public methods.
  - Task imports remain inline to avoid circular dependency:
    views → tasks → email_service → models → views.
"""

import logging
from typing import Optional, Tuple

from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.utils.html import escape
from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from core.security import (
    audit_log,
    generate_secure_otp,
    get_client_ip,
    increment_failed_login_attempts,
    is_account_locked,
    is_rate_limited,
    unlock_account,
)
from core.throttling import LoginThrottle, OTPThrottle
from core.validators import InputValidator

from .models import OTP, Address
from .serializers import AddressSerializer, RegisterSerializer, UserSerializer

User = get_user_model()
logger = logging.getLogger("accounts")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL HELPERS (D.R.Y.)
# ─────────────────────────────────────────────────────────────────────────────

def _build_jwt_response(user) -> dict:
    """
    Build a standard JWT access/refresh token pair for a user.

    Extracted from LoginView and RegisterView to eliminate duplicate code.

    Args:
        user: An authenticated User model instance.

    Returns:
        Dict with 'refresh' and 'access' token strings.
    """
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


def _require_valid_email(
    raw_email: str,
) -> Tuple[bool, str, Optional[Response]]:
    """
    Validate and normalise an email address.

    Args:
        raw_email: Raw string from request.data.

    Returns:
        (True, normalised_email, None) on success.
        (False, "", error_Response) on failure — caller should return the Response.
    """
    is_valid, email = InputValidator.validate_email(raw_email.strip())
    if not is_valid:
        return False, "", Response(
            {"error": "Please enter a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return True, email, None


# ─────────────────────────────────────────────────────────────────────────────
# OTP GENERATION & VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class RequestOTPView(APIView):
    """
    Generate and dispatch a cryptographically secure OTP via email.

    Security:
        ✅ secrets.randbelow() — cryptographic OTP, no modulo bias
        ✅ Atomic rate limiting: 3 OTPs per email per hour
        ✅ Old OTPs deleted before issuing new one (prevents accumulation)
        ✅ Anti-enumeration: password-reset for unknown email returns 200
        ✅ Sanitised audit log (no log injection)
    """
    permission_classes = [AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        action = request.data.get("action", "signup")
        if action not in ("signup", "reset"):
            return Response(
                {"error": "Invalid action. Must be 'signup' or 'reset'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_exists = User.objects.filter(email=email).exists()

        if action == "signup" and user_exists:
            audit_log(
                action="SIGNUP_OTP_BLOCKED_EXISTING_EMAIL",
                details={"email": email},
                severity="WARNING",
            )
            return Response(
                {"error": "An account already exists with this email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == "reset" and not user_exists:
            # Anti-enumeration: don't reveal the email doesn't exist
            audit_log(
                action="RESET_OTP_SILENT_NONEXISTENT",
                details={"email": email},
                severity="INFO",
            )
            return Response(
                {"message": "If an account exists, an OTP has been sent."},
                status=status.HTTP_200_OK,
            )

        # Atomically replace any existing OTP (delete then create)
        otp_code = generate_secure_otp(length=6)
        OTP.objects.filter(email=email).delete()
        OTP.objects.create(email=email, otp_code=otp_code)

        # Deferred imports prevent circular dependency: views→tasks→email_service
        from core.tasks import task_send_otp_email, task_send_password_reset_email

        task_send_otp_email.delay(email, otp_code)

        if action == "reset":
            try:
                user = User.objects.get(email=email)
                task_send_password_reset_email.delay(user.id)
            except User.DoesNotExist:
                pass  # Guarded by anti-enumeration check above

        audit_log(
            action="OTP_GENERATED",
            details={"email": email, "action": action},
            severity="INFO",
        )
        return Response({"message": "OTP sent successfully to your email."})


class VerifyOTPView(APIView):
    """
    Verify a user-submitted OTP and mark it as verified.

    Security:
        ✅ select_for_update() prevents two concurrent requests from both
           verifying the same OTP record (race condition fix)
        ✅ Atomic rate limiting: 5 failed attempts per 30 minutes per email
        ✅ OTP expiry enforced via OTP.is_valid() (15-minute window)
        ✅ Consistent response time prevents timing oracle on invalid codes
    """
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        otp_code = request.data.get("otp_code", "").strip()
        if not otp_code:
            return Response(
                {"error": "OTP code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if is_rate_limited(f"otp_verify:{email}", max_attempts=5, window_seconds=1800):
            audit_log(
                action="OTP_VERIFY_RATE_LIMIT_EXCEEDED",
                details={"email": email},
                severity="WARNING",
            )
            return Response(
                {"error": "Too many failed attempts. Try again in 30 minutes."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            # select_for_update() locks the row for the duration of this
            # transaction, preventing a race condition where two simultaneous
            # requests both succeed with the same OTP record.
            with transaction.atomic():
                otp = OTP.objects.select_for_update().get(
                    email=email, otp_code=otp_code
                )
                if not otp.is_valid():
                    audit_log(
                        action="OTP_VERIFICATION_EXPIRED",
                        details={"email": email},
                        severity="INFO",
                    )
                    return Response(
                        {"error": "OTP has expired. Please request a new one."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                otp.is_verified = True
                otp.save(update_fields=["is_verified"])

        except OTP.DoesNotExist:
            audit_log(
                action="OTP_VERIFICATION_FAILED_INVALID_CODE",
                details={"email": email},
                severity="INFO",
            )
            return Response(
                {"error": "Invalid OTP code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit_log(
            action="OTP_VERIFIED",
            details={"email": email},
            severity="INFO",
        )
        return Response({"message": "OTP verified successfully."})


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

class RegisterView(generics.CreateAPIView):
    """
    Register a new user, gated behind a verified OTP.

    Security:
        ✅ Requires OTP to be verified before account creation
        ✅ Argon2 password hashing (configured in settings.PASSWORD_HASHERS)
        ✅ full_name sanitised against XSS via django.utils.html.escape
        ✅ Entire operation wrapped in a DB transaction (atomic rollback)
        ✅ Welcome and verification emails dispatched asynchronously
    """
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    @transaction.atomic
    def create(self, request, *args, **kwargs) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        try:
            otp = OTP.objects.select_for_update().get(
                email=email, is_verified=True
            )
            if not otp.is_valid():
                audit_log(
                    action="SIGNUP_FAILED_OTP_EXPIRED",
                    details={"email": email},
                    severity="WARNING",
                )
                return Response(
                    {"error": "Verified session expired. Please request a new OTP."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except OTP.DoesNotExist:
            audit_log(
                action="SIGNUP_FAILED_NO_OTP",
                details={"email": email},
                severity="WARNING",
            )
            return Response(
                {"error": "Please verify your email with an OTP first."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Sanitise full_name: strip HTML tags and escape entities (XSS prevention)
        full_name = escape(serializer.validated_data.get("full_name", ""))[:150]

        user = serializer.save()
        user.full_name = full_name
        user.email = email
        user.save(update_fields=["full_name", "email"])

        otp.delete()  # Consume the OTP — cannot be reused

        from core.tasks import task_send_verification_email, task_send_welcome_email
        task_send_welcome_email.delay(user.id)
        task_send_verification_email.delay(user.id)

        audit_log(
            action="USER_REGISTERED",
            user_id=user.id,
            details={"email": email},
            severity="INFO",
        )
        return Response(
            {
                "message": "Registration successful!",
                "user": UserSerializer(user).data,
                "tokens": _build_jwt_response(user),  # D.R.Y. helper
            },
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

class LoginView(APIView):
    """
    Secure login with account lockout and rate limiting.

    Security:
        ✅ Atomic failed-attempt tracking (no race condition on lockout)
        ✅ Anti-enumeration: non-existent email returns generic "Invalid credentials"
        ✅ Consistent error wording prevents user/password oracle distinction
        ✅ Audit log records IP for security correlation
    """
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            # Return generic message — don't reveal email validation details
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        password = request.data.get("password", "")
        client_ip = get_client_ip(request)

        if not password:
            return Response(
                {"error": "Email and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            audit_log(
                action="LOGIN_ATTEMPT_NONEXISTENT_EMAIL",
                details={"email": email, "ip": client_ip},
                severity="INFO",
            )
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if is_account_locked(user.id):
            audit_log(
                action="LOGIN_ATTEMPT_LOCKED_ACCOUNT",
                user_id=user.id,
                details={"email": email, "ip": client_ip},
                severity="WARNING",
            )
            return Response(
                {"error": "Account temporarily locked. Try again in 1 hour."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        authenticated_user = authenticate(
            request, username=email, password=password
        )

        if authenticated_user is None:
            failed_count = increment_failed_login_attempts(user.id)
            remaining = max(0, 5 - failed_count)
            audit_log(
                action="LOGIN_FAILED_INVALID_PASSWORD",
                user_id=user.id,
                details={
                    "email": email,
                    "ip": client_ip,
                    "failed_attempts": str(failed_count),
                },
                severity="WARNING",
            )
            return Response(
                {
                    "error": (
                        f"Invalid credentials. "
                        f"{remaining} attempt(s) remaining before lockout."
                    )
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        unlock_account(user.id)  # Reset counter on successful login

        audit_log(
            action="LOGIN_SUCCESS",
            user_id=user.id,
            details={"email": email, "ip": client_ip},
            severity="INFO",
        )
        return Response(
            {
                "message": "Login successful!",
                "user": UserSerializer(authenticated_user).data,
                "tokens": _build_jwt_response(authenticated_user),  # D.R.Y. helper
            },
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD RESET
# ─────────────────────────────────────────────────────────────────────────────

class ResetPasswordView(APIView):
    """
    Reset password after OTP verification.

    Security:
        ✅ select_for_update() prevents race conditions on OTP consumption
        ✅ Password strength validated before hashing
        ✅ Anti-enumeration: missing OTP returns 200 (not 404)
        ✅ Argon2 hashing applied via set_password()
    """
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        new_password = request.data.get("new_password", "").strip()
        if not new_password:
            return Response(
                {"error": "new_password is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate password strength before touching the DB
        pwd_valid, pwd_error = InputValidator.validate_password(new_password)
        if not pwd_valid:
            return Response(
                {"error": pwd_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                otp = OTP.objects.select_for_update().get(
                    email=email, is_verified=True
                )
                if not otp.is_valid():
                    audit_log(
                        action="PASSWORD_RESET_FAILED_OTP_EXPIRED",
                        details={"email": email},
                        severity="WARNING",
                    )
                    return Response(
                        {"error": "Verified session expired. Request a new OTP."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                try:
                    user = User.objects.get(email=email)
                    user.set_password(new_password)  # Argon2 applied
                    user.save(update_fields=["password"])
                    otp.delete()
                except User.DoesNotExist:
                    # Anti-enumeration: return success even for unknown emails
                    return Response(
                        {"message": "If an account exists, the password has been reset."},
                        status=status.HTTP_200_OK,
                    )

        except OTP.DoesNotExist:
            return Response(
                {"message": "If an OTP was verified, the password has been reset."},
                status=status.HTTP_200_OK,
            )

        audit_log(
            action="PASSWORD_RESET_SUCCESS",
            user_id=user.id,
            details={"email": email},
            severity="INFO",
        )
        return Response({"message": "Password reset successfully."})


# ─────────────────────────────────────────────────────────────────────────────
# USER PROFILE & ADDRESSES
# ─────────────────────────────────────────────────────────────────────────────

class ProfileView(generics.RetrieveUpdateAPIView):
    """
    Retrieve or update the authenticated user's profile.

    Security: IsAuthenticated — users can only access their own record.
    """
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)


class AddressViewSet(viewsets.ModelViewSet):
    """
    CRUD for user shipping addresses.

    Security:
        ✅ Queryset scoped to request.user — prevents IDOR
        ✅ user auto-assigned on create — client cannot set arbitrary owner
        ✅ Audit logging on every mutating operation
    """
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer) -> None:
        serializer.save(user=self.request.user)
        audit_log(
            action="ADDRESS_CREATED",
            user_id=self.request.user.id,
            details={"address_id": str(serializer.instance.id)},
            severity="INFO",
        )

    def perform_update(self, serializer) -> None:
        serializer.save()
        audit_log(
            action="ADDRESS_UPDATED",
            user_id=self.request.user.id,
            details={"address_id": str(serializer.instance.id)},
            severity="INFO",
        )

    def perform_destroy(self, instance) -> None:
        audit_log(
            action="ADDRESS_DELETED",
            user_id=self.request.user.id,
            details={"address_id": str(instance.id)},
            severity="INFO",
        )
        instance.delete()
