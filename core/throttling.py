"""
Custom DRF throttle classes for endpoint-specific rate limiting.
"""

from rest_framework.throttling import BaseThrottle
from rest_framework.response import Response
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class OTPThrottle(BaseThrottle):
    """
    Rate limit OTP requests: 3 per hour per email.
    
    Prevents brute-force OTP generation attacks.
    """
    
    def allow_request(self, request, view):
        email = request.data.get('email', '').lower()
        if not email:
            return False  # Reject if no email provided
        
        cache_key = f"throttle_otp:{email}"
        request_count = cache.get(cache_key, 0)
        
        if request_count >= 3:
            logger.warning(f"OTP rate limit exceeded for email: {email}")
            return False
        
        cache.set(cache_key, request_count + 1, 3600)  # 1 hour
        return True
    
    def throttle_success(self):
        return True
    
    def throttle_failure(self):
        return {
            'error': 'OTP request limit exceeded. Maximum 3 requests per hour per email.'
        }


class LoginThrottle(BaseThrottle):
    """
    Rate limit login attempts: 5 per hour per email.
    
    Prevents brute-force password attacks. After 5 failed attempts,
    account is temporarily locked (see accounts/views.py).
    """
    
    def allow_request(self, request, view):
        email = request.data.get('email', '').lower()
        if not email:
            return False
        
        cache_key = f"throttle_login:{email}"
        attempt_count = cache.get(cache_key, 0)
        
        # Hard rate limit at 5 attempts per hour
        if attempt_count >= 5:
            logger.warning(f"Login rate limit exceeded for email: {email}")
            return False
        
        cache.set(cache_key, attempt_count + 1, 3600)
        return True
    
    def throttle_failure(self):
        return {
            'error': 'Too many login attempts. Maximum 5 per hour. Account temporarily locked.'
        }


class PaymentThrottle(BaseThrottle):
    """
    Rate limit payment operations: 10 per minute per user.
    
    Prevents rapid-fire payment requests or verification attempts.
    """
    
    def allow_request(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return True  # Skip throttling for unauthenticated (payment_webhook)
        
        user_id = request.user.id
        cache_key = f"throttle_payment:{user_id}"
        attempt_count = cache.get(cache_key, 0)
        
        if attempt_count >= 10:
            logger.warning(f"Payment rate limit exceeded for user: {user_id}")
            return False
        
        cache.set(cache_key, attempt_count + 1, 60)
        return True
    
    def throttle_failure(self):
        return {
            'error': 'Payment request rate limit exceeded. Please wait 1 minute.'
        }


class AdminThrottle(BaseThrottle):
    """
    Rate limit admin endpoints: 100 per minute per admin user.
    
    Allows bulk operations but prevents denial-of-service attacks.
    """
    
    def allow_request(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        if not (request.user.role == 'admin' or request.user.is_superuser):
            return True  # Skip for non-admins
        
        user_id = request.user.id
        cache_key = f"throttle_admin:{user_id}"
        attempt_count = cache.get(cache_key, 0)
        
        if attempt_count >= 100:
            logger.warning(f"Admin rate limit exceeded for user: {user_id}")
            return False
        
        cache.set(cache_key, attempt_count + 1, 60)
        return True
    
    def throttle_failure(self):
        return {
            'error': 'Admin endpoint rate limit exceeded. Maximum 100 requests per minute.'
        }


class PincodeVerifyThrottle(BaseThrottle):
    """
    Rate limit external API calls: 20 per hour per IP.
    
    Pincode verification calls an external service. Rate limit
    prevents abuse of that service and SSRF attack attempts.
    """
    
    def allow_request(self, request, view):
        from core.security import get_client_ip
        
        client_ip = get_client_ip(request)
        cache_key = f"throttle_pincode:{client_ip}"
        attempt_count = cache.get(cache_key, 0)
        
        if attempt_count >= 20:
            logger.warning(f"Pincode verify rate limit exceeded for IP: {client_ip}")
            return False
        
        cache.set(cache_key, attempt_count + 1, 3600)
        return True
    
    def throttle_failure(self):
        return {
            'error': 'Pincode verification rate limit exceeded. Maximum 20 per hour.'
        }
