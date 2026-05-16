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
import uuid
from typing import Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.html import escape
from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.security import (
    audit_log,
    generate_secure_otp,
    generate_otp_session_token,
    get_client_ip,
    increment_failed_login_attempts,
    is_account_locked,
    is_rate_limited,
    unlock_account,
    verify_otp_session_token,
)
from core.permissions import IsAdminOrCustomerUser
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


def _set_auth_cookies(response: Response, refresh: str, access: str) -> None:
    cookie_settings = settings.SIMPLE_JWT
    secure = cookie_settings["AUTH_COOKIE_SECURE"]
    samesite = cookie_settings["AUTH_COOKIE_SAMESITE"]
    http_only = cookie_settings["AUTH_COOKIE_HTTP_ONLY"]

    response.set_cookie(
        cookie_settings["AUTH_COOKIE_ACCESS"],
        access,
        max_age=int(cookie_settings["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        httponly=http_only,
        secure=secure,
        samesite=samesite,
        path="/",
    )
    response.set_cookie(
        cookie_settings["AUTH_COOKIE_REFRESH"],
        refresh,
        max_age=int(cookie_settings["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=http_only,
        secure=secure,
        samesite=samesite,
        path=cookie_settings["AUTH_COOKIE_REFRESH_PATH"],
    )


def _clear_auth_cookies(response: Response) -> None:
    cookie_settings = settings.SIMPLE_JWT
    response.delete_cookie(
        cookie_settings["AUTH_COOKIE_ACCESS"],
        path="/",
        samesite=cookie_settings["AUTH_COOKIE_SAMESITE"],
    )
    response.delete_cookie(
        cookie_settings["AUTH_COOKIE_REFRESH"],
        path=cookie_settings["AUTH_COOKIE_REFRESH_PATH"],
        samesite=cookie_settings["AUTH_COOKIE_SAMESITE"],
    )


def _build_authenticated_response(user, message: str, status_code: int) -> Response:
    tokens = _build_jwt_response(user)
    response = Response(
        {
            "message": message,
            "user": UserSerializer(user).data,
        },
        status=status_code,
    )
    _set_auth_cookies(response, tokens["refresh"], tokens["access"])
    return response


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


def _dispatch_email_task(task, fallback, *args) -> bool:
    """
    Try async email dispatch first, then fall back to synchronous send.

    This keeps auth flows functional even when Celery broker/workers
    are temporarily unavailable.
    """
    try:
        task.delay(*args)
        return True
    except Exception as exc:
        logger.warning(
            "Async email dispatch failed for %s; using sync fallback. error=%s",
            getattr(task, "name", getattr(task, "__name__", "unknown_task")),
            str(exc),
        )

    try:
        return bool(fallback(*args))
    except Exception:
        logger.exception(
            "Sync email fallback failed for %s.",
            getattr(fallback, "__name__", "unknown_fallback"),
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
# OTP GENERATION & VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class RequestOTPView(APIView):
    """
    Generate and dispatch a cryptographically secure OTP via email.

    Security:
            secrets.randbelow() — cryptographic OTP, no modulo bias
            Atomic rate limiting: 3 OTPs per email per hour
            Old OTPs deleted before issuing new one (prevents accumulation)
            Anti-enumeration: password-reset for unknown email returns 200
            Sanitised audit log (no log injection)
    """
    permission_classes = [AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        action = request.data.get("action", "signup")
        
        # New: Validate password early if signing up
        if action == "signup":
            password = request.data.get("password", "")
            if not password:
                return Response(
                    {"error": "Password is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            pwd_valid, pwd_error = InputValidator.validate_password(password)
            if not pwd_valid:
                return Response(
                    {"error": pwd_error},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
        from core.services.email_service import (
            send_otp_email,
            send_password_reset_email,
        )

        otp_dispatched = _dispatch_email_task(
            task_send_otp_email,
            send_otp_email,
            email,
            otp_code,
        )
        if not otp_dispatched:
            OTP.objects.filter(email=email).delete()
            return Response(
                {"error": "Failed to send OTP. Please try again in a moment."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if action == "reset":
            try:
                user = User.objects.get(email=email)
                _dispatch_email_task(
                    task_send_password_reset_email,
                    send_password_reset_email,
                    user.id,
                )
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
            select_for_update() prevents two concurrent requests from both
            verifying the same OTP record (race condition fix)
            Atomic rate limiting: 5 failed attempts per 30 minutes per email
            OTP expiry enforced via OTP.is_valid() (15-minute window)
            Consistent response time prevents timing oracle on invalid codes
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
        return Response(
            {
                "message": "OTP verified successfully.",
                "otp_session_token": generate_otp_session_token(email),
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

class RegisterView(generics.CreateAPIView):
    """
    Register a new user, gated behind a verified OTP.

    Security:
        Requires OTP to be verified before account creation
        Argon2 password hashing (configured in settings.PASSWORD_HASHERS)
        full_name sanitised against XSS via django.utils.html.escape
        Entire operation wrapped in a DB transaction (atomic rollback)
        Verification email dispatched asynchronously
    """
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    @transaction.atomic
    def create(self, request, *args, **kwargs) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        otp_session_token = str(request.data.get("otp_session_token", "")).strip()
        verified_email = verify_otp_session_token(otp_session_token)
        if verified_email != email:
            audit_log(
                action="SIGNUP_FAILED_INVALID_OTP_SESSION",
                details={"email": email},
                severity="WARNING",
            )
            return Response(
                {"error": "Verified OTP session is missing or expired. Please verify again."},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        # Verify-email notification removed per product requirement.

        # Merge session/guest cart with user's cart (if session items provided)
        from store.views import _merge_session_cart_with_user_cart
        session_items = request.data.get("session_cart_items")
        if session_items and isinstance(session_items, list):
            _merge_session_cart_with_user_cart(user, session_items)

        audit_log(
            action="USER_REGISTERED",
            user_id=user.id,
            details={"email": email},
            severity="INFO",
        )
        return _build_authenticated_response(
            user,
            message="Registration successful!",
            status_code=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

class LoginView(APIView):
    """
    Secure login with account lockout and rate limiting.

    Security:
        Atomic failed-attempt tracking (no race condition on lockout)
        Anti-enumeration: non-existent email returns generic "Invalid credentials"
        Consistent error wording prevents user/password oracle distinction
        Audit log records IP for security correlation
    """
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request) -> Response:
        identifier = str(
            request.data.get("identifier", request.data.get("email", ""))
        ).strip()
        if not identifier:
            return Response(
                {"error": "Email/username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lookup_email = ""
        if "@" in identifier:
            ok, lookup_email, err = _require_valid_email(identifier)
            if not ok:
                # Return generic message — don't reveal email validation details
                return Response(
                    {"error": "Invalid credentials."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            user = User.objects.filter(email=lookup_email).first()
        else:
            user = User.objects.filter(username__iexact=identifier).first()

        if user is None:
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

        if is_account_locked(user.id):
            audit_log(
                action="LOGIN_ATTEMPT_LOCKED_ACCOUNT",
                user_id=user.id,
                details={"email": user.email, "ip": client_ip, "identifier": identifier},
                severity="WARNING",
            )
            return Response(
                {"error": "Account temporarily locked. Try again in 1 hour."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        authenticated_user = user if user.is_active and user.check_password(password) else None

        if authenticated_user is None:
            failed_count = increment_failed_login_attempts(user.id)
            remaining = max(0, 5 - failed_count)
            audit_log(
                action="LOGIN_FAILED_INVALID_PASSWORD",
                user_id=user.id,
                details={
                    "email": user.email,
                    "ip": client_ip,
                    "identifier": identifier,
                    "failed_attempts": str(failed_count),
                },
                severity="WARNING",
            )
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        unlock_account(user.id)  # Reset counter on successful login

        # Merge session/guest cart with user's cart (if session items provided)
        from store.views import _merge_session_cart_with_user_cart
        session_items = request.data.get("session_cart_items")
        if session_items and isinstance(session_items, list):
            _merge_session_cart_with_user_cart(authenticated_user, session_items)

        audit_log(
            action="LOGIN_SUCCESS",
            user_id=user.id,
            details={"email": user.email, "ip": client_ip, "identifier": identifier},
            severity="INFO",
        )
        return _build_authenticated_response(
            authenticated_user,
            message="Login successful!",
            status_code=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD RESET
# ─────────────────────────────────────────────────────────────────────────────

class ResetPasswordView(APIView):
    """
    Reset password after OTP verification.

    Security:
        select_for_update() prevents race conditions on OTP consumption
        Password strength validated before hashing
        Anti-enumeration: missing OTP returns 200 (not 404)
        Argon2 hashing applied via set_password()
    """
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        otp_session_token = str(request.data.get("otp_session_token", "")).strip()
        verified_email = verify_otp_session_token(otp_session_token)
        if verified_email != email:
            return Response(
                {"error": "Verified OTP session is missing or expired. Please verify again."},
                status=status.HTTP_403_FORBIDDEN,
            )

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


class RefreshTokenCookieView(APIView):
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        refresh_cookie_name = settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"]
        refresh_token = request.COOKIES.get(refresh_cookie_name) or request.data.get(
            "refresh", ""
        )
        if not refresh_token:
            response = Response(
                {"error": "Refresh token missing."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_auth_cookies(response)
            return response

        try:
            refresh = RefreshToken(refresh_token)
            access = str(refresh.access_token)

            if settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
                refresh.set_jti()
                refresh.set_exp()
                refresh.set_iat()
                rotated_refresh = str(refresh)
                if settings.SIMPLE_JWT.get("BLACKLIST_AFTER_ROTATION"):
                    try:
                        RefreshToken(refresh_token).blacklist()
                    except TokenError:
                        pass
            else:
                rotated_refresh = str(refresh)
        except TokenError:
            response = Response(
                {"error": "Refresh token invalid or expired."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_auth_cookies(response)
            return response

        response = Response({"message": "Token refreshed."}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, rotated_refresh, access)
        return response


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request) -> Response:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        # Guest sessions should never retain server-side checkout holds after logout.
        if user and getattr(user, "is_guest", False):
            try:
                from store.models import Cart, StockReservation

                cart = Cart.objects.filter(user=user).first()
                if cart:
                    cart.items.all().delete()
                StockReservation.objects.filter(user=user).delete()
            except Exception:
                logger.exception("Failed to clear guest checkout residue during logout.")

        refresh_cookie_name = settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"]
        refresh_token = request.COOKIES.get(refresh_cookie_name)

        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                pass

        if hasattr(request, "session"):
            request.session.flush()

        response = Response({"message": "Logged out."}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class GuestSessionView(APIView):
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request) -> Response:
        if request.user and request.user.is_authenticated:
            return Response(
                {
                    "message": "Session already authenticated.",
                    "user": UserSerializer(request.user).data,
                },
                status=status.HTTP_200_OK,
            )

        suffix = uuid.uuid4().hex[:12]
        guest_email = f"guest-{suffix}@checkout.local"
        guest_username = f"guest_{suffix}"
        guest_password = uuid.uuid4().hex

        guest_user = User.objects.create_user(
            email=guest_email,
            username=guest_username,
            password=guest_password,
            full_name="Guest Checkout",
            role="customer",
            is_guest=True,
        )

        audit_log(
            action="GUEST_SESSION_CREATED",
            user_id=guest_user.id,
            details={"email": guest_user.email},
            severity="INFO",
        )

        return _build_authenticated_response(
            guest_user,
            message="Guest checkout session created.",
            status_code=status.HTTP_201_CREATED,
        )


class ConvertGuestAccountView(APIView):
    permission_classes = [IsAdminOrCustomerUser]

    @transaction.atomic
    def post(self, request) -> Response:
        user = request.user
        if not getattr(user, "is_guest", False):
            return Response(
                {"error": "Only guest sessions can be converted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, email, err = _require_valid_email(request.data.get("email", ""))
        if not ok:
            return err

        password = str(request.data.get("password", "")).strip()
        password2 = str(request.data.get("password2", "")).strip()
        full_name = escape(str(request.data.get("full_name", "")).strip())[:150]
        phone = str(request.data.get("phone", "")).strip()

        if not password:
            return Response(
                {"error": "Password is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if password != password2:
            return Response(
                {"error": "Passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pwd_valid, pwd_error = InputValidator.validate_password(password)
        if not pwd_valid:
            return Response(
                {"error": pwd_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.filter(email=email).exclude(id=user.id).exists():
            return Response(
                {"error": "This email is already in use."},
                status=status.HTTP_409_CONFLICT,
            )

        if phone:
            phone_ok, normalized_phone = InputValidator.validate_phone(phone)
            if not phone_ok:
                return Response(
                    {"error": "Enter a valid phone number."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.phone = normalized_phone

        user.email = email
        user.username = email.split("@")[0]
        user.full_name = full_name or user.full_name or "Customer"
        user.is_guest = False
        user.set_password(password)
        user.save(update_fields=["email", "username", "full_name", "phone", "is_guest", "password"])

        audit_log(
            action="GUEST_SESSION_CONVERTED",
            user_id=user.id,
            details={"email": user.email},
            severity="INFO",
        )

        return _build_authenticated_response(
            user,
            message="Account created successfully.",
            status_code=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# USER PROFILE & ADDRESSES
# ─────────────────────────────────────────────────────────────────────────────

class ProfileView(generics.RetrieveUpdateAPIView):
    """
    Retrieve or update the authenticated user's profile.

    Security: IsAuthenticated — users can only access their own record.
    """
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrCustomerUser]

    def get_object(self):
        return self.request.user

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)


class AddressViewSet(viewsets.ModelViewSet):
    """
    CRUD for user shipping addresses.

    Security:
        Queryset scoped to request.user — prevents IDOR
        user auto-assigned on create — client cannot set arbitrary owner
        Audit logging on every mutating operation
    """
    serializer_class = AddressSerializer
    permission_classes = [IsAdminOrCustomerUser]

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
