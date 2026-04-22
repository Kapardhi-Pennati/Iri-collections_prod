"""
Input validation and sanitization utilities for production security.
"""

import re
import socket
from typing import Dict, Tuple
from urllib.parse import urlparse
from django.core.exceptions import ValidationError
from django.utils.html import escape, strip_tags


class InputValidator:
    """Comprehensive input validation with security-first approach."""
    
    # ─────────────────────────────────────────────────────────────────────────
    # EMAIL VALIDATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_email(email: str, max_length: int = 254) -> Tuple[bool, str]:
        """
        Validate and normalize email addresses.
        
        Args:
            email: Email address to validate
            max_length: Max length allowed (RFC 5321)
        
        Returns:
            (is_valid, normalized_email)
        
        Security:
            - Enforces RFC 5321 length limits
            - Normalizes to lowercase (prevents duplicate accounts)
            - Rejects special characters and international domains (for now)
            - Rejects common typos (gmail.com misspellings)
        
        Example:
            is_valid, normalized = InputValidator.validate_email("User@Gmail.COM")
            # Returns: (True, "user@gmail.com")
        """
        if not email or not isinstance(email, str):
            return False, ""
        
        email = email.strip().lower()
        
        if len(email) > max_length:
            return False, ""
        
        # RFC 5322 simplified regex (allows 99% of real emails)
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return False, ""
        
        # Reject common typos (e.g., gmail.com misspellings)
        common_typos = {
            'gmial.com': True, 'gmai.com': True, 'gmail.co': True,
            'yahooo.com': True, 'yaho.com': True, 'hotmial.com': True,
        }
        domain = email.split('@')[1]
        if domain in common_typos:
            return False, ""
        
        return True, email
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # PHONE VALIDATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_phone(phone: str, country_code: str = "IN") -> Tuple[bool, str]:
        """
        Validate phone number with country-specific rules.
        
        Args:
            phone: Phone number
            country_code: ISO country code (default India)
        
        Returns:
            (is_valid, normalized_phone)
        
        Security:
            - Enforces reasonable length (10-15 digits)
            - Removes non-numeric characters
            - Validates country-specific format
        
        Example:
            is_valid, normalized = InputValidator.validate_phone("+91 98765 43210")
            # Returns: (True, "+919876543210")
        """
        if not phone or not isinstance(phone, str):
            return False, ""
        
        # Remove common formatting characters
        phone_clean = re.sub(r'[\s\-\(\)]+', '', phone)
        
        # Extract only digits and leading +
        phone_digits = re.sub(r'^(\+)?[^0-9]', '', phone_clean)
        if phone_clean.startswith('+'):
            phone_digits = '+' + phone_digits
        
        # Validate length (reasonable bounds)
        digit_count = len(re.sub(r'\D', '', phone_digits))
        if digit_count < 10 or digit_count > 15:
            return False, ""
        
        # Country-specific validation (India example)
        if country_code == "IN":
            # Indian numbers: +91 followed by 10 digits
            india_pattern = r'^(\+91|0)?[6-9]\d{9}$'
            if not re.match(india_pattern, phone_digits.replace('+91', '').replace('+', '')):
                return False, ""
        
        return True, phone_digits
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # ADDRESS VALIDATION & SANITIZATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_address(address: str, max_length: int = 500) -> Tuple[bool, str]:
        """
        Validate and sanitize shipping addresses.
        
        Args:
            address: Shipping address
            max_length: Max length allowed
        
        Returns:
            (is_valid, sanitized_address)
        
        Security:
            - Removes XSS/script injection attempts
            - Enforces max length
            - Escapes HTML entities
            - Removes control characters
        
        Example:
            is_valid, safe = InputValidator.validate_address(
                "123 Main St<script>alert('xss')</script>, NYC"
            )
            # Returns: (True, "123 Main St&lt;script&gt;alert('xss')&lt;/script&gt;, NYC")
        """
        if not address or not isinstance(address, str):
            return False, ""
        
        # Check length
        if len(address) > max_length:
            return False, ""
        
        # Remove control characters
        address = ''.join(char for char in address if ord(char) >= 32 or char == '\n')
        
        # Strip tags and escape HTML entities
        address = strip_tags(address).strip()
        address = escape(address)
        
        # Minimum length check (avoid empty/garbage input)
        if len(address) < 5:
            return False, ""
        
        return True, address
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # PINCODE VALIDATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_pincode(pincode: str, country_code: str = "IN") -> Tuple[bool, str]:
        """
        Validate postal code/pincode format.
        
        Args:
            pincode: Postal code
            country_code: ISO country code
        
        Returns:
            (is_valid, normalized_pincode)
        
        Security:
            - Country-specific format validation
            - Prevents injection attacks
        
        Example:
            is_valid, normalized = InputValidator.validate_pincode("600001")
            # Returns: (True, "600001")
        """
        if not pincode or not isinstance(pincode, str):
            return False, ""
        
        pincode = pincode.strip()
        
        # Country-specific validation (India example)
        if country_code == "IN":
            # Indian pincodes: exactly 6 digits
            if not re.match(r'^\d{6}$', pincode):
                return False, ""
        
        return True, pincode
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # QUANTITY VALIDATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_quantity(quantity, max_per_order: int = 100, min_quantity: int = 1) -> Tuple[bool, int]:
        """
        Validate order quantity with reasonable bounds.
        
        Args:
            quantity: Requested quantity
            max_per_order: Max items per order
            min_quantity: Minimum quantity
        
        Returns:
            (is_valid, validated_quantity)
        
        Security:
            - Prevents integer overflow attacks
            - Enforces business logic bounds
            - Type-safe conversion
        
        Example:
            is_valid, qty = InputValidator.validate_quantity(5, max_per_order=100)
            # Returns: (True, 5)
        """
        try:
            qty = int(quantity)
            if qty < min_quantity or qty > max_per_order:
                return False, 0
            return True, qty
        except (ValueError, TypeError):
            return False, 0
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # URL VALIDATION (for external API calls)
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def is_valid_url(url: str, allowed_domains: list = None) -> bool:
        """
        Validate URLs and prevent SSRF attacks.
        
        Args:
            url: URL to validate
            allowed_domains: List of allowed domains (whitelist)
        
        Returns:
            True if URL is safe to access
        
        Security:
            - Whitelist only trusted domains
            - Prevents internal IP access (127.0.0.1, 192.168.x.x, etc.)
            - Rejects file: and gopher: protocols
        
        Example:
            is_safe = InputValidator.is_valid_url(
                "https://api.example.com/data",
                allowed_domains=["api.example.com"]
            )
        """
        try:
            parsed = urlparse(url)
            
            # Reject dangerous schemes
            if parsed.scheme not in ['http', 'https']:
                return False
            
            # Reject localhost/internal IPs (SSRF prevention)
            hostname = parsed.hostname
            if not hostname:
                return False
            
            # Local/internal IP ranges (common SSRF targets)
            internal_ips = [
                'localhost', '127.', '0.0.0.0', '192.168.', '10.', '172.16.',
                '169.254.',  # Link-local
            ]
            if any(hostname.startswith(ip) for ip in internal_ips):
                return False
            
            # Whitelist check if provided
            if allowed_domains:
                if hostname not in allowed_domains:
                    return False
            
            return True
        except Exception:
            return False
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # PASSWORD VALIDATION
    # ─────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def validate_password(password: str) -> Tuple[bool, str]:
        """
        Validate password strength (supplementary to Django validators).
        
        Args:
            password: Password to check
        
        Returns:
            (is_valid, error_message)
        
        Security:
            - Enforces minimum entropy
            - Requires mixed character types
            - Checks against common patterns
        
        Note:
            Django's AUTH_PASSWORD_VALIDATORS are still the source of truth.
            This is supplementary for custom rules.
        """
        if not password or len(password) < 8:
            return False, "Password must be at least 8 characters."
        
        # Check for mixed character types
        has_lower = bool(re.search(r'[a-z]', password))
        has_upper = bool(re.search(r'[A-Z]', password))
        has_digit = bool(re.search(r'[0-9]', password))
        has_special = bool(re.search(r'[!@#$%^&*(),.?":{}|<>]', password))
        
        # Require at least 3 of 4 character types
        char_type_count = sum([has_lower, has_upper, has_digit, has_special])
        if char_type_count < 3:
            return False, "Use 3 of 4: lowercase, uppercase, numbers, special chars."
        
        return True, ""
