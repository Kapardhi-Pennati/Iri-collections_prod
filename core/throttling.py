"""
Atomic DRF throttle classes for sensitive endpoints.
"""

import logging
import re
from typing import Optional

from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger(__name__)


class AtomicRateThrottle(BaseThrottle):
    """
    Counter-based throttle that uses cache.add()/cache.incr() atomically.

    DRF's list-history throttles are easy to reason about, but they allow race
    windows under heavy concurrency. Sensitive auth and checkout endpoints
    benefit from monotonic counters instead.
    """

    scope = "atomic"
    rate = "10/min"

    def __init__(self) -> None:
        self.num_requests, self.duration = self._parse_rate(self.rate)
        self.key = None
        self._wait = None

    def _parse_rate(self, rate: str) -> tuple[int, int]:
        amount, period = rate.split("/")
        num_requests = int(amount)
        token = period.strip().lower()

        named_windows = {
            "second": 1,
            "sec": 1,
            "s": 1,
            "minute": 60,
            "min": 60,
            "m": 60,
            "hour": 3600,
            "h": 3600,
            "day": 86400,
            "d": 86400,
        }
        if token in named_windows:
            return num_requests, named_windows[token]

        match = re.fullmatch(r"(\d+)([smhd])", token)
        if not match:
            raise ValueError(f"Unsupported throttle rate: {rate}")

        value = int(match.group(1))
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
        return num_requests, value * multiplier

    def get_cache_key(self, request, view) -> Optional[str]:
        raise NotImplementedError

    def allow_request(self, request, view) -> bool:
        cache_key = self.get_cache_key(request, view)
        if not cache_key:
            return True

        self.key = f"throttle:{self.scope}:{cache_key}"

        try:
            if cache.add(self.key, 1, self.duration):
                return True

            current = cache.incr(self.key)
        except ValueError:
            cache.set(self.key, 1, self.duration)
            return True
        except Exception:
            logger.exception("Throttle backend failure for scope=%s", self.scope)
            # Fail open when cache is unavailable so critical endpoints remain
            # functional instead of returning 429 for every request.
            return True

        if not isinstance(current, int):
            logger.warning("Throttle backend returned non-int counter for scope=%s", self.scope)
            try:
                current = int(current)
            except (TypeError, ValueError):
                return True

        if current > self.num_requests:
            self._wait = self._get_wait_seconds()
            return False

        return True

    def _get_wait_seconds(self) -> Optional[int]:
        try:
            ttl = cache.ttl(self.key)
            if ttl is None:
                return self.duration
            return max(1, int(ttl))
        except Exception:
            return self.duration

    def wait(self) -> Optional[int]:
        return self._wait


class OTPThrottle(AtomicRateThrottle):
    scope = "otp"
    rate = "20/hour"

    def get_cache_key(self, request, view) -> Optional[str]:
        from core.security import get_client_ip

        email = str(request.data.get("email", "")).strip().lower()
        if not email:
            return None
        return f"{email}:{get_client_ip(request)}"


class LoginThrottle(AtomicRateThrottle):
    scope = "login"
    rate = "100/hour"

    def get_cache_key(self, request, view) -> Optional[str]:
        from core.security import get_client_ip

        email = str(request.data.get("email", "")).strip().lower()
        if not email:
            return get_client_ip(request)
        return f"{email}:{get_client_ip(request)}"


class CheckoutThrottle(AtomicRateThrottle):
    scope = "checkout"
    rate = "15/10m"

    def get_cache_key(self, request, view) -> Optional[str]:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        return str(user.id)


class PaymentThrottle(CheckoutThrottle):
    scope = "payment"
    rate = "20/10m"


class CheckoutOTPVerifyThrottle(AtomicRateThrottle):
    scope = "checkout_otp_verify"
    rate = "7/10m"

    def get_cache_key(self, request, view) -> Optional[str]:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        return str(user.id)


class AdminMutationThrottle(AtomicRateThrottle):
    scope = "admin_mutation"
    rate = "60/min"

    def get_cache_key(self, request, view) -> Optional[str]:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        return str(user.id)


class PincodeVerifyThrottle(AtomicRateThrottle):
    scope = "pincode"
    rate = "20/hour"

    def get_cache_key(self, request, view) -> Optional[str]:
        from core.security import get_client_ip

        return get_client_ip(request)
