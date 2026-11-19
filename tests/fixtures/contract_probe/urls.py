from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AccountView, ItemListView, SignInViewSet

router = SimpleRouter(trailing_slash=True)
router.register("auth", SignInViewSet, basename="auth")

urlpatterns = [
    path("api/account/", AccountView.as_view()),
    path("api/items/", ItemListView.as_view()),
    *router.urls,
]

# The JWT cookie scheme's OpenAPI extension registers when the swagger URLs
# are built; this project mounts no swagger, so register it directly.
from stapel_core.django.openapi.swagger import _register_jwt_auth_extension  # noqa: E402

_register_jwt_auth_extension()
