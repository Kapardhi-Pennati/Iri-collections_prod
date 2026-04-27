from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import Address
from core.validators import InputValidator

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("email", "username", "full_name", "phone", "password", "password2")

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "Passwords do not match."})

        phone = attrs.get("phone", "")
        if phone:
            is_valid, normalized = InputValidator.validate_phone(phone)
            if not is_valid:
                raise serializers.ValidationError({"phone": "Enter a valid phone number."})
            attrs["phone"] = normalized

        attrs["full_name"] = attrs.get("full_name", "").strip()[:150]
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        user = User.objects.create_user(
            email=validated_data["email"],
            username=validated_data.get(
                "username", validated_data["email"].split("@")[0]
            ),
            full_name=validated_data.get("full_name", ""),
            phone=validated_data.get("phone", ""),
            password=validated_data["password"],
            role="customer",
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "username",
            "full_name",
            "phone",
            "role",
            "is_guest",
            "date_joined",
        )
        read_only_fields = fields


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ("id", "name", "street", "city", "state", "pincode", "phone", "is_default")
        read_only_fields = ("id",)

    def validate_street(self, value: str) -> str:
        is_valid, sanitized = InputValidator.validate_address(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid street address.")
        return sanitized

    def validate_city(self, value: str) -> str:
        value = value.strip()
        if len(value) < 2 or len(value) > 100:
            raise serializers.ValidationError("Enter a valid city.")
        return value

    def validate_state(self, value: str) -> str:
        value = value.strip()
        if len(value) < 2 or len(value) > 100:
            raise serializers.ValidationError("Enter a valid state.")
        return value

    def validate_pincode(self, value: str) -> str:
        is_valid, normalized = InputValidator.validate_pincode(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid pincode.")
        return normalized

    def validate_phone(self, value: str) -> str:
        if not value:
            return value
        is_valid, normalized = InputValidator.validate_phone(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid phone number.")
        return normalized

    def validate_name(self, value: str) -> str:
        return value.strip()[:150]
