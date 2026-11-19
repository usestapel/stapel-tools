from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.views import APIView
from stapel_core.django.api.errors import (
    StapelErrorResponse,
    StapelErrorSerializer,
    StapelResponse,
)
from stapel_core.django.api.pagination import AnchorPagination
from stapel_core.django.captcha import CaptchaMixin, captcha_protected
from stapel_core.flows import Flow, flow_step
from stapel_core.verification.decorators import requires_verification

from .errors import ERR_400_PROBE_CODE, ERR_403_PROBE_CLOSED, ERR_409_PROBE_TAKEN

PROBE_FLOW = Flow("probe.signin", title="Probe sign-in", description="probe")


class OpenUnlessFlagged(permissions.BasePermission):
    """Admits anonymous callers; refuses a request carrying the probe header."""

    def has_permission(self, request, view):
        if request.headers.get("X-Probe-Flag"):
            raise PermissionError(ERR_403_PROBE_CLOSED)
        return True


class SignedInSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["LOGGED_IN", "REGISTERED"], help_text="Outcome"
    )
    user_id = serializers.CharField()


class ChallengeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["CHALLENGE"])
    challenge_token = serializers.CharField()


SignInResponse = PolymorphicProxySerializer(
    component_name="SignInResponse",
    serializers=[SignedInSerializer, ChallengeSerializer],
    resource_type_field_name="status",
)


class SignInRequestSerializer(CaptchaMixin, serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField()
    captcha_token = serializers.CharField(required=False)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(ERR_400_PROBE_CODE)
        return value


class ItemSerializer(serializers.Serializer):
    name = serializers.CharField()


class PageSerializer(serializers.Serializer):
    results = ItemSerializer(many=True)
    count = serializers.IntegerField()
    next = serializers.IntegerField(allow_null=True)


class SignInViewSet(viewsets.GenericViewSet):
    permission_classes = [OpenUnlessFlagged]

    @extend_schema(
        request=SignInRequestSerializer,
        responses={200: SignInResponse, 400: StapelErrorSerializer},
    )
    @flow_step(PROBE_FLOW, order=1, note="sign in")
    @action(detail=False, methods=["post"], url_path="sign-in")
    def sign_in(self, request):
        ser = SignInRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from .services import LockService

        LockService.check(ser.validated_data["email"])
        return StapelResponse({"status": "LOGGED_IN", "user_id": "1"})

    @extend_schema(request=None, responses={200: SignedInSerializer})
    @captcha_protected(action="register")
    @action(detail=False, methods=["post"], url_path="register")
    def register(self, request):
        if request.data.get("taken"):
            return StapelErrorResponse(409, ERR_409_PROBE_TAKEN)
        return StapelResponse({})


class AccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: ItemSerializer})
    @requires_verification(scope="probe.delete", factors=["totp"], max_age=300)
    def delete(self, request):
        return StapelResponse({"name": "gone"})

    @extend_schema(responses={200: PageSerializer})
    def get(self, request):
        return StapelResponse({"results": [], "count": 0, "next": None})


class ItemListView(APIView):
    permission_classes = [permissions.AllowAny]
    pagination_class = AnchorPagination

    @extend_schema(responses={200: ItemSerializer(many=True)})
    def get(self, request):
        return StapelResponse([])
