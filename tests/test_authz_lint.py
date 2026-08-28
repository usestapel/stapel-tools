"""stapel-authz-lint tests — AUTHZ001-005.

Every rule carries three kinds of case, because a security linter that cries
wolf is turned off and is then strictly worse than no linter at all:

* a **positive** — the defect stapel-core 0.38.0-0.43.0 actually shipped,
  written the way it was actually written;
* a **negative** — the fixed shape, which must be silent;
* at least one **near-miss** — code a naive matcher would flag (or would miss)
  and this one must get right. The near-misses are the point of the file.

The whole-tree control lives at the bottom: the pre-fix ``login_views.py``
verbatim must trip AUTHZ001 *and* AUTHZ002, and the post-fix one must trip
neither. A rule that does not survive that pair is inverted.
"""
from pathlib import Path

import pytest

from stapel_tools import lint_profile, verify
from stapel_tools.authz_lint import lint_file, lint_paths, lint_project, main


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _codes(violations):
    return sorted(v.rule for v in violations)


# ===========================================================================
# AUTHZ001 — a LoginView subclass whose form_valid asks nothing
# ===========================================================================


class TestAuthz001:
    def test_the_shipped_bypass_is_flagged(self, tmp_path):
        """stapel-core 0.37.0 ``JWTCookieLoginView.form_valid``, in shape."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView
from django.contrib.auth import login


class JWTCookieLoginView(LoginView):
    template_name = 'admin/login.html'

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        return super().form_valid(form)
""")
        violations = lint_file(path)
        assert "AUTHZ001" in _codes(violations)
        assert [v for v in violations if v.rule == "AUTHZ001"][0].line == 8

    def test_an_admin_form_attribute_satisfies_it(self, tmp_path):
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView
from django.contrib.admin.forms import AdminAuthenticationForm


class AdminCookieLoginView(LoginView):
    authentication_form = AdminAuthenticationForm

    def form_valid(self, form):
        return super().form_valid(form)
""")
        assert _codes(lint_file(path)) == []

    def test_a_lazy_get_form_class_satisfies_it(self, tmp_path):
        """Core's real fix imports the admin form lazily (app-registry safe),
        so the name never appears as a class attribute."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView


class AdminCookieLoginView(LoginView):
    authentication_form = None

    def get_form_class(self):
        from django.contrib.admin.forms import AdminAuthenticationForm

        return AdminAuthenticationForm

    def form_valid(self, form):
        return super().form_valid(form)
""")
        assert _codes(lint_file(path)) == []

    def test_a_defensive_getattr_read_satisfies_it(self, tmp_path):
        """``getattr(user, "is_staff", False)`` is a read. An attribute-only
        matcher misses it, and the fixed core code is written this way."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView


class CookieLoginView(LoginView):
    def form_valid(self, form):
        user = form.get_user()
        if not getattr(user, "is_staff", False):
            return self.form_invalid(form)
        return super().form_valid(form)
""")
        assert _codes(lint_file(path)) == []

    def test_a_same_module_predicate_satisfies_it(self, tmp_path):
        """NEAR MISS: extracting the check into a named predicate is the
        first thing anyone does. A rule that read that as absence would be
        suppressed within a week."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView


def has_admin_access(user) -> bool:
    return bool(getattr(user, "is_superuser", False))


class CookieLoginView(LoginView):
    def form_valid(self, form):
        if not has_admin_access(form.get_user()):
            return self.form_invalid(form)
        return super().form_valid(form)
""")
        assert _codes(lint_file(path)) == []

    def test_no_form_valid_override_is_not_flagged(self, tmp_path):
        """NEAR MISS: a LoginView subclass with no ``form_valid`` inherits
        Django's, which mints nothing. Flagging every subclass would make
        this rule noise."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView


class BrandedLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True
""")
        assert _codes(lint_file(path)) == []

    def test_a_staff_read_in_another_method_does_not_satisfy_it(self, tmp_path):
        """NEAR MISS, the one that matters most: the shipped file DID read
        ``is_staff`` three times — all three in ``dispatch()``'s
        already-authenticated branch, none on the minting path. Any
        file-level grep calls this file safe. It was not."""
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView
from django.contrib.auth import login


class JWTCookieLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.is_staff or request.user.is_superuser:
                return redirect('/admin/')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        login(self.request, form.get_user())
        return super().form_valid(form)
""")
        assert "AUTHZ001" in _codes(lint_file(path))

    def test_a_non_login_view_base_is_out_of_scope(self, tmp_path):
        """NEAR MISS: a class merely NAMED ...LoginView but built on View has
        no ``form_valid`` contract to reason about."""
        path = _write(tmp_path, "views.py", """\
from django.views import View


class ApiLoginView(View):
    def form_valid(self, form):
        return None
""")
        assert _codes(lint_file(path)) == []

    def test_noqa_suppresses_it(self, tmp_path):
        path = _write(tmp_path, "login_views.py", """\
from django.contrib.auth.views import LoginView


class PublicCookieLoginView(LoginView):
    def form_valid(self, form):  # noqa: AUTHZ001 - storefront login, no admin
        return super().form_valid(form)
""")
        assert _codes(lint_file(path)) == []


# ===========================================================================
# AUTHZ002 — mint after a credential check, no authorization read before it
# ===========================================================================


class TestAuthz002:
    def test_login_then_mint_is_flagged(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
from django.contrib.auth import login

from .provider import jwt_provider
from .utils import set_jwt_cookies


def sign_in(request, form):
    user = form.get_user()
    login(request, user)
    access, refresh = jwt_provider.create_tokens(user)
    response = redirect('/')
    set_jwt_cookies(response, access, refresh)
    return response
""")
        assert _codes(lint_file(path)) == ["AUTHZ002"]

    def test_a_read_before_the_mint_satisfies_it(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
from django.contrib.auth import login

from .provider import jwt_provider


def sign_in(request, form):
    user = form.get_user()
    if not user.is_staff:
        return None
    login(request, user)
    return jwt_provider.create_tokens(user)
""")
        assert _codes(lint_file(path)) == []

    def test_a_read_after_the_mint_does_not_satisfy_it(self, tmp_path):
        """NEAR MISS: a token outlives the request that minted it, so a check
        that runs after ``create_tokens`` is a log line, not a gate. A
        set-membership matcher ('does this function mention is_staff?') passes
        this file."""
        path = _write(tmp_path, "views.py", """\
from django.contrib.auth import login

from .provider import jwt_provider


def sign_in(request, form):
    user = form.get_user()
    login(request, user)
    tokens = jwt_provider.create_tokens(user)
    if user.is_staff:
        logger.info("staff signed in")
    return tokens
""")
        assert _codes(lint_file(path)) == ["AUTHZ002"]

    def test_a_test_client_login_is_not_a_credential_check(self, tmp_path):
        """NEAR MISS: ``self.client.login(...)`` is a test harness, not
        ``django.contrib.auth.login``. Matching the bare name ``login`` would
        light up every auth suite in the fleet, which is how a rule gets
        waived wholesale."""
        path = _write(tmp_path, "test_tokens.py", """\
from .provider import jwt_provider


class TokenTests(TestCase):
    def test_pair_is_minted(self):
        self.client.login(username="u", password="p")
        access, refresh = jwt_provider.create_tokens(self.user)
        assert access
""")
        assert _codes(lint_file(path)) == []

    def test_a_mint_with_no_credential_call_is_out_of_scope(self, tmp_path):
        """NEAR MISS: a DRF flow that mints for an account a service already
        verified (password reset, OTP promotion) is the intended grant, not
        this defect class. stapel-auth has ~15 such call sites."""
        path = _write(tmp_path, "password_views.py", """\
from .provider import jwt_provider


def reset_confirm(request, user):
    access, refresh = jwt_provider.create_tokens(user)
    return access, refresh
""")
        assert _codes(lint_file(path)) == []

    def test_a_nested_function_is_not_an_escape(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
from django.contrib.auth import login


def outer(request, form):
    def _finish(user):
        login(request, user)
        return create_tokens(user)

    return _finish(form.get_user())
""")
        assert "AUTHZ002" in _codes(lint_file(path))

    def test_noqa_suppresses_it(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
from django.contrib.auth import login


def sign_in(request, form):
    login(request, form.get_user())
    return create_tokens(form.get_user())  # noqa: AUTHZ002 - caller decided
""")
        assert _codes(lint_file(path)) == []


# ===========================================================================
# AUTHZ003 — an explicit "re-mint from the token's own claims"
# ===========================================================================


class TestAuthz003:
    def test_explicit_none_positional_is_a_warning(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
def refresh(provider, token):
    return provider.refresh_access_token(token, None)
""")
        violations = lint_file(path)
        assert _codes(violations) == ["AUTHZ003"]
        assert violations[0].level == "warning"

    def test_explicit_none_keyword_is_a_warning(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
def refresh(provider, token):
    return provider.refresh_access_token(token, load_user_data=None)
""")
        assert _codes(lint_file(path)) == ["AUTHZ003"]

    def test_the_loader_form_is_silent(self, tmp_path):
        path = _write(tmp_path, "views.py", """\
from .utils import load_user_by_uid


def refresh(provider, token):
    return provider.refresh_access_token(token, load_user_by_uid)
""")
        assert _codes(lint_file(path)) == []

    def test_the_bare_call_is_silent(self, tmp_path):
        """The django-layer provider has defaulted to the database loader
        since stapel-core 0.39.0, so omitting the argument is now the SAFE
        form. A rule that flagged the bare call would be pointing at the
        wrong thing entirely — and at every consumer."""
        path = _write(tmp_path, "views.py", """\
def refresh(provider, token):
    return provider.refresh_access_token(token)
""")
        assert _codes(lint_file(path)) == []

    def test_a_computed_loader_is_not_an_explicit_none(self, tmp_path):
        """NEAR MISS: ``loader or None`` is a BoolOp, not the constant. The
        rule is about a typed-out decision, not about the value that might
        arrive at runtime — which it cannot know anyway."""
        path = _write(tmp_path, "views.py", """\
def refresh(provider, token, loader):
    return provider.refresh_access_token(token, loader or None)
""")
        assert _codes(lint_file(path)) == []

    def test_the_definition_default_is_not_a_call(self, tmp_path):
        """NEAR MISS: the framework-free ``TokenManager`` signature still
        declares ``load_user_data=None``. Flagging the definition would
        flag stapel-core's own primitive on every run."""
        path = _write(tmp_path, "token_manager.py", """\
class TokenManager:
    def refresh_access_token(self, refresh_token, load_user_data=None):
        return None
""")
        assert _codes(lint_file(path)) == []

    def test_strict_promotes_it_to_an_error(self, tmp_path, capsys):
        _write(tmp_path, "views.py", """\
def refresh(provider, token):
    return provider.refresh_access_token(token, None)
""")
        assert main([str(tmp_path)]) == 0
        assert main([str(tmp_path), "--strict"]) == 1


# ===========================================================================
# AUTHZ004 — a get_user() override that dropped Django's own check
# ===========================================================================


class TestAuthz004:
    def test_the_shipped_override_is_flagged(self, tmp_path):
        path = _write(tmp_path, "backends.py", """\
class JWTAuthBackend(BaseBackend):
    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
""")
        assert _codes(lint_file(path)) == ["AUTHZ004"]

    def test_an_is_active_guard_satisfies_it(self, tmp_path):
        path = _write(tmp_path, "backends.py", """\
class JWTAuthBackend(BaseBackend):
    def get_user(self, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        if not getattr(user, "is_active", True):
            return None
        return user
""")
        assert _codes(lint_file(path)) == []

    def test_user_can_authenticate_satisfies_it(self, tmp_path):
        path = _write(tmp_path, "session.py", """\
class EmailAuthBackend(ModelBackend):
    def get_user(self, user_id):
        try:
            user = get_user_model().objects.get(pk=user_id)
        except Exception:
            return None
        return user if self.user_can_authenticate(user) else None
""")
        assert _codes(lint_file(path)) == []

    def test_a_filtered_get_satisfies_it(self, tmp_path):
        """NEAR MISS: the check can live in the query. A matcher looking only
        for an ``if`` statement would flag correct code."""
        path = _write(tmp_path, "backends.py", """\
class Backend:
    def get_user(self, user_id):
        return User.objects.get(pk=user_id, is_active=True)
""")
        assert _codes(lint_file(path)) == []

    def test_a_module_level_helper_is_out_of_scope(self, tmp_path):
        """NEAR MISS: a free function named ``get_user`` is not Django's
        backend contract — nothing resolves ``request.user`` through it."""
        path = _write(tmp_path, "helpers.py", """\
def get_user(user_id):
    return User.objects.get(pk=user_id)
""")
        assert _codes(lint_file(path)) == []

    def test_a_form_style_get_user_is_not_flagged(self, tmp_path):
        """NEAR MISS: ``AuthenticationForm.get_user`` returns a cached
        instance; there is no query and no lifecycle decision to make."""
        path = _write(tmp_path, "forms.py", """\
class LoginForm(AuthenticationForm):
    def get_user(self):
        return self.user_cache
""")
        assert _codes(lint_file(path)) == []

    def test_delegating_to_super_is_not_flagged(self, tmp_path):
        path = _write(tmp_path, "backends.py", """\
class Backend(ModelBackend):
    def get_user(self, user_id):
        return super().get_user(user_id)
""")
        assert _codes(lint_file(path)) == []


# ===========================================================================
# AUTHZ005 — revocation written into a per-service cache namespace
# ===========================================================================


class TestAuthz005:
    def test_the_shipped_user_blacklist_is_flagged(self, tmp_path):
        path = _write(tmp_path, "authentication.py", """\
from django.core.cache import cache

USER_BLACKLIST_PREFIX = 'user_blacklisted:'


def blacklist_user(user_id, ttl=7200):
    cache.set(f'{USER_BLACKLIST_PREFIX}{user_id}', '1', ttl)
    return True
""")
        assert _codes(lint_file(path)) == ["AUTHZ005"]

    def test_the_class_supplies_the_context_its_method_lacks(self, tmp_path):
        """``TokenBlacklist.clear_all`` names nothing revocation-ish: not its
        method name, not its arguments. Only its CLASS does."""
        path = _write(tmp_path, "token_blacklist.py", """\
class TokenBlacklist:
    def clear_all(self):
        from django.core.cache import cache

        cache.clear()
        return True
""")
        assert _codes(lint_file(path)) == ["AUTHZ005"]

    def test_a_read_is_flagged_too(self, tmp_path):
        """The read half is what answers 'not revoked' to a revoked token."""
        path = _write(tmp_path, "authentication.py", """\
from django.core.cache import cache


def is_user_blacklisted(user_id):
    return bool(cache.get(f'user_blacklisted:{user_id}'))
""")
        assert _codes(lint_file(path)) == ["AUTHZ005"]

    def test_the_shared_namespace_is_silent(self, tmp_path):
        path = _write(tmp_path, "authentication.py", """\
from stapel_core.core.revocation_store import revocation_cache


def blacklist_user(user_id, ttl=7200):
    revocation_cache().set(f'user_blacklisted:{user_id}', '1', ttl)
    return True
""")
        assert _codes(lint_file(path)) == []

    def test_ordinary_caching_is_silent(self, tmp_path):
        """NEAR MISS: a rule that flagged every ``cache.set`` would fire on
        every repo in the fleet on day one."""
        path = _write(tmp_path, "services.py", """\
from django.core.cache import cache


def cached_categories():
    hit = cache.get('categories:v1')
    if hit is None:
        hit = build()
        cache.set('categories:v1', hit, 300)
    return hit
""")
        assert _codes(lint_file(path)) == []

    def test_prose_is_not_a_context_signal(self, tmp_path):
        """NEAR MISS, found in the fleet sweep: stapel-moderation's cache
        fixture has 'blacklist' in its DOCSTRING and clears the whole default
        cache, which is neither a revocation write nor a defect. Judging on
        prose is how a rule earns its first blanket suppression."""
        path = _write(tmp_path, "conftest.py", """\
import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    \"\"\"The notification cooldown and the user blacklist live in the cache.\"\"\"
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
""")
        assert _codes(lint_file(path)) == []

    def test_the_module_that_implements_the_remedy_is_exempt(self, tmp_path):
        """NEAR MISS: ``revocation_store`` itself must reach for a raw cache
        connection — it is the thing that builds the shared one. A rule that
        flags its own fix teaches readers the rule is wrong."""
        path = _write(tmp_path, "revocation_store.py", """\
def revocation_cache():
    try:
        return _build()
    except Exception:
        from django.core.cache import cache

        return cache
""")
        assert _codes(lint_file(path)) == []

    def test_an_identifier_in_the_key_is_enough_context(self, tmp_path):
        """The call sits in ``test_rearm_...``: the only signal is the key
        name in the call itself."""
        path = _write(tmp_path, "test_sanctions.py", """\
from django.core.cache import cache

BLACKLIST_KEY = "user_blacklisted:{user_id}"


def test_rearm_restores_a_key_the_cache_ttl_dropped(user):
    cache.delete(BLACKLIST_KEY.format(user_id=user.pk))
""")
        assert _codes(lint_file(path)) == ["AUTHZ005"]

    def test_noqa_suppresses_it(self, tmp_path):
        path = _write(tmp_path, "legacy.py", """\
from django.core.cache import cache


def blacklist_user(user_id):
    cache.set(f'user_blacklisted:{user_id}', '1')  # noqa: AUTHZ005 - single-service
""")
        assert _codes(lint_file(path)) == []


# ===========================================================================
# AUTHZ006 — an anonymous read of the unfiltered manager that names people
# ===========================================================================


#: ``stapel-listings`` at 4cb3e74, in shape: the endpoint that was live on a
#: production stand, answering an unauthenticated GET for any id with
#: ``owner_id`` and ``moderation_status``, over ``all_objects`` so drafts,
#: rejected and soft-deleted rows answered too.
LISTINGS_PRE_FIX_VIEWS = """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from drf_spectacular.utils import extend_schema

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    queryset = Listing.objects.all()
    permission_classes = [IsAuthenticatedOrReadOnly]

    @extend_schema(responses={200: ListingStatusSerializer})
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        try:
            listing = Listing.all_objects.get(pk=pk)
        except Listing.DoesNotExist:
            return StapelErrorResponse(404, ERR_404_LISTING_NOT_FOUND)
        return StapelResponse(ListingStatusSerializer(listing))
"""

#: ``stapel-listings`` at 4f1bd8d (released 0.8.0). Note what did NOT change:
#: still ``AllowAny``, still ``all_objects``, still the same serializer in the
#: schema. The capability was kept and the disclosure was narrowed — which is
#: exactly why the rule keys on the missing DECISION rather than on the
#: permission or the manager.
LISTINGS_POST_FIX_VIEWS = """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from drf_spectacular.utils import extend_schema

from stapel_core.django.api.permissions import IsServiceRequest

from .models import Listing
from .serializers import ListingPresenceSerializer, ListingStatusSerializer


def _may_see_full_status(request, listing) -> bool:
    if IsServiceRequest().has_permission(request, None):
        return True
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return str(getattr(user, "pk", "")) == str(listing.owner_id)


class ListingViewSet(viewsets.ModelViewSet):
    queryset = Listing.objects.all()
    permission_classes = [IsAuthenticatedOrReadOnly]

    @extend_schema(responses={200: ListingStatusSerializer})
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        try:
            listing = Listing.all_objects.get(pk=pk)
        except Listing.DoesNotExist:
            return StapelErrorResponse(404, ERR_404_LISTING_NOT_FOUND)
        if _may_see_full_status(request, listing):
            return StapelResponse(ListingStatusSerializer(listing))
        return StapelResponse(ListingPresenceSerializer(listing))
"""

LISTINGS_SERIALIZERS = """\
from rest_framework import serializers


class ListingPresenceSerializer(serializers.Serializer):
    is_deleted = serializers.BooleanField()

    def to_representation(self, instance):
        return {"is_deleted": instance.is_deleted}


class ListingStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ListingStatus.choices)
    moderation_status = serializers.CharField()
    is_deleted = serializers.BooleanField()
    is_expired = serializers.BooleanField()
    is_active = serializers.BooleanField()
    owner_id = serializers.CharField()
"""


class TestAuthz006:
    def test_the_shipped_oracle_is_flagged(self, tmp_path):
        """The live defect: AllowAny + all_objects + owner_id/moderation."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", LISTINGS_PRE_FIX_VIEWS)
        violations = [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"]
        assert len(violations) == 1
        # Reported on the `def` line, so a per-rule suppression comment lands
        # where the module's other per-handler ones already live. (Spelling
        # the directive out here would make ruff read this line as one.)
        assert violations[0].line == 16
        assert "owner_id" in violations[0].message
        assert "moderation_status" in violations[0].message

    def test_narrowing_the_disclosure_silences_it(self, tmp_path):
        """The control that proves the rule is not inverted.

        The 0.8.0 fix kept AllowAny AND all_objects AND the full serializer.
        A rule that keyed on any of those three would call the remedy the
        defect — which is how a security linter gets switched off."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", LISTINGS_POST_FIX_VIEWS)
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_an_owner_scoped_view_on_all_objects_is_not_flagged(self, tmp_path):
        """MUST NOT FIRE: `all_objects` behind IsAuthenticated in an
        owner-scoped view is the ordinary, correct use of the manager."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def archived_status(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk, owner=request.user)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_an_allowany_view_on_the_ordinary_manager_is_not_flagged(self, tmp_path):
        """MUST NOT FIRE: a public read through the DEFAULT manager sees only
        what the default filter already publishes. AllowAny is not the defect;
        AllowAny over the filter-bypassing manager is."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        listing = Listing.objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_a_boolean_only_response_is_not_flagged(self, tmp_path):
        """MUST NOT FIRE: the capability with no disclosure. This is the
        endpoint the browser client actually needs, and the rule has to leave
        the whole shape alone or the fix has nowhere to land."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Listing
from .serializers import ListingPresenceSerializer


class ListingViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingPresenceSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_a_read_under_is_authenticated_or_read_only_is_flagged(self, tmp_path):
        """A signed-in stranger is still a stranger: an oracle that costs an
        attacker one free account is not closed. The class-level permission is
        inherited by an @action that names none of its own."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        codes = [v.rule for v in lint_project(tmp_path)]
        assert "AUTHZ006" in codes

    def test_a_write_under_is_authenticated_or_read_only_is_not_flagged(self, tmp_path):
        """NEAR MISS: `IsAuthenticatedOrReadOnly` is only permissive on SAFE
        methods. On a POST it demands a session, so it is not an anonymous
        surface and the rule must not pretend otherwise."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_allow_any_anded_with_a_real_permission_is_not_flagged(self, tmp_path):
        """NEAR MISS: DRF ANDs `permission_classes`, so [AllowAny, IsOwner] is
        IsOwner. A rule that matched the mere presence of `AllowAny` would be
        reading the list backwards."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Listing
from .permissions import IsListingOwner
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["get"], permission_classes=[AllowAny, IsListingOwner])
    def status(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_an_undeclared_permission_is_not_guessed(self, tmp_path):
        """NEAR MISS: with no `permission_classes` anywhere, DRF's
        DEFAULT_PERMISSION_CLASSES decides, and settings are not resolved
        here. Silence, not a guess — the same discipline the rest of the
        family keeps about what it cannot see."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_a_with_deleted_queryset_call_counts_as_an_escape(self, tmp_path):
        """The other soft-delete convention in the fleet: a manager method
        rather than a second manager attribute."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        listing = Listing.objects.with_deleted().get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        codes = [v.rule for v in lint_project(tmp_path)]
        assert "AUTHZ006" in codes

    def test_a_literal_dict_response_is_read(self, tmp_path):
        """No serializer at all: the fields are right there in the body. The
        DYNAMIC dict is what the rule cannot see, not the literal one."""
        path = _write(tmp_path, "views.py", """\
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny

from .models import Listing


@api_view(["GET"])
@permission_classes([AllowAny])
def listing_status(request, pk):
    listing = Listing.all_objects.get(pk=pk)
    return Response({
        "owner_id": str(listing.owner_id),
        "moderation_status": listing.moderation_status,
    })
""")
        assert "AUTHZ006" in _codes(lint_file(path))

    def test_the_legacy_marketplace_shape_is_flagged(self, tmp_path):
        """The ancestor of the defect, found by the fleet sweep: the legacy
        marketplace `ads` app carries the same endpoint, and the serializer
        declares its fields only inside `to_representation`."""
        _write(tmp_path, "ads/serializers.py", """\
from rest_framework import serializers


class AdStatusSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            'status': instance.status,
            'is_deleted': instance.is_deleted,
            'owner_id': str(instance.owner_id),
        }
""")
        _write(tmp_path, "ads/views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Ad
from .serializers import AdStatusSerializer


class AdViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=['get'], permission_classes=[AllowAny])
    def status(self, request, pk=None):
        ad = Ad.all_objects.get(pk=pk)
        serializer = AdStatusSerializer(ad)
        return Response(serializer.data)
""")
        assert [v.rule for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == [
            "AUTHZ006",
        ]

    def test_noqa_suppresses_it(self, tmp_path):
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        _write(tmp_path, "views.py", """\
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import Listing
from .serializers import ListingStatusSerializer


class ListingViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def status(self, request, pk=None):  # noqa: AUTHZ006 - internal-only network
        listing = Listing.all_objects.get(pk=pk)
        return StapelResponse(ListingStatusSerializer(listing))
""")
        assert [v for v in lint_project(tmp_path) if v.rule == "AUTHZ006"] == []

    def test_a_serializer_in_a_sibling_module_is_resolved(self, tmp_path):
        """The whole reason lint_project builds a cross-file index: the view
        names the serializer, the fields live next door. Linting the view file
        alone with no directory context sees no fields and stays silent —
        the documented cost of having no import resolution."""
        _write(tmp_path, "serializers.py", LISTINGS_SERIALIZERS)
        view = _write(tmp_path, "views.py", LISTINGS_PRE_FIX_VIEWS)
        assert "AUTHZ006" in [v.rule for v in lint_project(tmp_path)]
        # The bare-module index (no sibling): silent, and honestly so.
        assert "AUTHZ006" not in _codes(lint_file(view))


# ===========================================================================
# the whole-tree control: pre-fix vs post-fix
# ===========================================================================


PRE_FIX_LOGIN_VIEW = """\
import logging
from django.contrib.auth.views import LoginView
from django.contrib.auth import login

from .utils import set_jwt_cookies
from .provider import jwt_provider

logger = logging.getLogger(__name__)


class JWTCookieLoginView(LoginView):
    template_name = 'admin/login.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            is_staff = getattr(request.user, 'is_staff', False)
            is_superuser = getattr(request.user, 'is_superuser', False)
            if is_staff or is_superuser:
                return redirect('/admin/')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')
        access_token, refresh_token = jwt_provider.create_tokens(user)
        response = super().form_valid(form)
        set_jwt_cookies(response, access_token, refresh_token)
        return response
"""

POST_FIX_LOGIN_VIEW = """\
import logging
from django.contrib.auth.views import LoginView
from django.contrib.auth import login

from .utils import set_jwt_cookies
from .provider import jwt_provider

logger = logging.getLogger(__name__)


def has_admin_access(user) -> bool:
    return bool(
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    )


class JWTCookieLoginView(LoginView):
    template_name = 'admin/login.html'
    authentication_form = None

    def get_form_class(self):
        if self.authentication_form is not None:
            return self.authentication_form
        from django.contrib.admin.forms import AdminAuthenticationForm

        return AdminAuthenticationForm

    def form_valid(self, form):
        user = form.get_user()
        if not has_admin_access(user):
            return self.form_invalid(form)
        login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')
        access_token, refresh_token = jwt_provider.create_tokens(user)
        response = super().form_valid(form)
        set_jwt_cookies(response, access_token, refresh_token)
        return response
"""


def test_the_pre_fix_tree_trips_both_rules(tmp_path):
    _write(tmp_path, "django/jwt/login_views.py", PRE_FIX_LOGIN_VIEW)
    assert _codes(lint_project(tmp_path)) == ["AUTHZ001", "AUTHZ002"]


def test_the_post_fix_tree_is_clean(tmp_path):
    """The control that proves the rule is not inverted: the shape stapel-core
    actually shipped as the FIX must be silent. If this file ever starts
    firing, the rule is describing the remedy as the defect."""
    _write(tmp_path, "django/jwt/login_views.py", POST_FIX_LOGIN_VIEW)
    assert _codes(lint_project(tmp_path)) == []


# ===========================================================================
# driver, CLI and wiring
# ===========================================================================


def test_migrations_and_vendored_trees_are_skipped(tmp_path):
    _write(tmp_path, "migrations/0002_x.py", PRE_FIX_LOGIN_VIEW)
    _write(tmp_path, "node_modules/pkg/login_views.py", PRE_FIX_LOGIN_VIEW)
    assert _codes(lint_project(tmp_path)) == []


def test_notes_report_what_was_scanned(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    notes: list = []
    lint_project(tmp_path, notes=notes)
    assert notes and "1 python file(s) scanned" in notes[0]


def test_unparseable_files_are_skipped_not_fatal(tmp_path):
    path = _write(tmp_path, "broken.py", "def (:\n")
    assert lint_file(path) == []


def test_lint_paths_accepts_a_single_file(tmp_path):
    path = _write(tmp_path, "login_views.py", PRE_FIX_LOGIN_VIEW)
    assert _codes(lint_paths([str(path)])) == ["AUTHZ001", "AUTHZ002"]


def test_lint_paths_rejects_a_missing_path(tmp_path):
    with pytest.raises(SystemExit):
        lint_paths([str(tmp_path / "nope")])


def test_cli_json_output(tmp_path, capsys):
    _write(tmp_path, "login_views.py", PRE_FIX_LOGIN_VIEW)
    assert main([str(tmp_path), "--json"]) == 1
    payload = capsys.readouterr().out
    assert '"AUTHZ001"' in payload and '"ok": false' in payload


def test_cli_is_clean_on_a_clean_tree(tmp_path, capsys):
    _write(tmp_path, "login_views.py", POST_FIX_LOGIN_VIEW)
    assert main([str(tmp_path)]) == 0
    assert "No violations found." in capsys.readouterr().out


def test_it_is_actually_wired_into_the_gate():
    """A linter nobody composes is a linter nobody runs — the exact way R006
    and ADO002 sat unexercised while a migration shipped green."""
    assert "stapel-authz-lint" in verify.COMPOSED_LINTERS
    assert lint_profile.LINTER_SURFACES["stapel-authz-lint"] == "python"


def test_verify_reports_the_findings(tmp_path):
    _write(tmp_path, "login_views.py", PRE_FIX_LOGIN_VIEW)
    reports = {r.name: r for r in verify.verify_project(tmp_path)}
    assert "stapel-authz-lint" in reports
    assert reports["stapel-authz-lint"].errors == 2
    assert {f["rule"] for f in reports["stapel-authz-lint"].findings} == {
        "AUTHZ001", "AUTHZ002",
    }
