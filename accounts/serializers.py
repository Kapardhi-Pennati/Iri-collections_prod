from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import Address

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
            "date_joined",
        )
        read_only_fields = fields


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ("id", "name", "street", "city", "state", "pincode", "phone", "is_default")
        read_only_fields = ("id",)
