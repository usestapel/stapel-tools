"""stapel-billing's E104 (payment provider unconfigured = check-time Error)
stays exactly as it is in the library — it is the deliberate prod guard: a
deployment must never come up silently answering checkout/portal/cancel with
fabricated placeholders. The defect this closes was in the SCAFFOLD: a
generated project that selected billing with no Stripe key could not even
boot `manage.py check`/migrate in dev, because E104 fires regardless of
environment.

The fix is a dev-only env flag, never a library change:
``ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1`` opens stapel_billing's own escape
hatch (W104 instead of E104). It is written into the DEV env file only
(``.env.local`` for monolith, ``.env``/``.env.example`` for minimal — this
preset has one file, not a pair) and NEVER into the monolith/microservices
PROD template (``.env.example``), which instead gets a commented
``# STRIPE_SECRET_KEY=`` placeholder so E104 still fires at prod boot until
the owner configures real keys. That split IS the prod guard.

WHICH projects get the hatch is decided by stapel-billing's own axis, not by
a Stripe secret handed to the generator. Two facts about the real 0.11.0
contract force that:

* ``STRIPE_SECRET_KEY`` is not a ``module_config`` key at all — the module's
  ``docs/capabilities.json`` declares ONE billing axis, ``PAYMENT_PROVIDER``,
  and ``validate_module_config`` hard-refuses anything outside the axis +
  extension surface. It is a secret; a generator renders module_config into a
  committed ``STAPEL_BILLING = {…}`` settings block, which is the last place
  a secret belongs. It reaches a deployment through the environment
  (``AppSettings.env_var_names`` → the bare name), which is why the prod
  ``.env.example`` placeholder is still the right artifact.
* So the old gate — "was a STRIPE_SECRET_KEY supplied?" — was a branch nobody
  could ever take. Every billing project got the dev hatch, including one
  that had named its own payment backend: a placebo flag silently suppressing
  the single check that would have reported that backend unconfigured.

The gate is now ``PAYMENT_PROVIDER``: left at the library's declared default
(the Stripe provider, whose credential only ever arrives via env) the project
gets the hatch and the prod placeholder; naming its own provider it gets
neither, because host code carries host credentials and E104 is how a
deployment is supposed to learn they are missing.
"""
from stapel_tools.create_project import create_project


def _create_minimal(tmp_path, name="app", modules=None, module_config=None):
    create_project(
        name=name, project_type="minimal", title=name.capitalize(),
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=modules or ["core"], output_dir=tmp_path,
        use_submodules=False, init_git=False, module_config=module_config,
    )
    return tmp_path / name


def _create_monolith(tmp_path, name="app", modules=None, module_config=None):
    create_project(
        name=name, project_type="monolith", title=name.capitalize(),
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=modules or ["core"], output_dir=tmp_path,
        use_submodules=False, init_git=False, module_config=module_config,
    )
    return tmp_path / name


class TestMinimalBillingWithoutStripeKey:
    def test_env_example_carries_the_dev_hatch(self, tmp_path):
        proj = _create_minimal(tmp_path, modules=["core", "billing"])
        text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1" in text
        # Explained, not a bare flag dropped with no context.
        assert "E104" in text

    def test_generated_env_carries_it_too(self, tmp_path):
        # .env is copied from .env.example at generation time (SEC-6 secret
        # injection) — the flag must survive that copy.
        proj = _create_minimal(tmp_path, modules=["core", "billing"])
        text = (proj / ".env").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1" in text


class TestMinimalBillingWithItsOwnPaymentProvider:
    def test_flag_absent_when_the_project_names_its_own_provider(self, tmp_path):
        proj = _create_minimal(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"PAYMENT_PROVIDER": "app.billing.HouseProvider"}},
        )
        text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in text

    def test_restating_the_library_default_is_not_naming_your_own(self, tmp_path):
        """The value the axis already carries is not a decision to run
        something else, so it must not close the hatch — the project still
        boots on the Stripe provider with no key in sight."""
        from stapel_tools._module_config import axis_default

        default = axis_default("billing", "PAYMENT_PROVIDER")
        assert default, "stapel-billing capabilities.json must declare the axis default"
        proj = _create_minimal(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"PAYMENT_PROVIDER": default}},
        )
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1" in (proj / ".env.example").read_text()

    def test_a_stripe_secret_is_not_a_module_config_key_at_all(self, tmp_path):
        """The contract the old gate was written against does not exist: the
        key is refused before any env file is written. A generator branch
        gated on it was unreachable, which is why the hatch went into every
        billing project ever generated."""
        import pytest

        with pytest.raises(SystemExit) as excinfo:
            _create_minimal(
                tmp_path, modules=["core", "billing"],
                module_config={"billing": {"STRIPE_SECRET_KEY": "sk_test_x"}},
            )
        assert "STRIPE_SECRET_KEY" in str(excinfo.value)


class TestNoBillingSelected:
    def test_flag_never_appears_without_billing(self, tmp_path):
        proj = _create_minimal(tmp_path, modules=["core", "auth"])
        text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in text


class TestMonolithDevVsProdSplit:
    def test_env_local_dev_carries_the_hatch(self, tmp_path):
        proj = _create_monolith(tmp_path, modules=["core", "billing"])
        text = (proj / ".env.local").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1" in text

    def test_env_example_prod_never_carries_the_hatch(self, tmp_path):
        proj = _create_monolith(tmp_path, modules=["core", "billing"])
        text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in text

    def test_env_example_prod_carries_a_commented_stripe_key_placeholder(self, tmp_path):
        proj = _create_monolith(tmp_path, modules=["core", "billing"])
        text = (proj / ".env.example").read_text()
        assert "# STRIPE_SECRET_KEY=" in text
        # Commented, not a live assignment — .env.example is committed and
        # must never carry a real secret.
        assert "\nSTRIPE_SECRET_KEY=" not in text

    def test_no_billing_no_split_content_at_all(self, tmp_path):
        proj = _create_monolith(tmp_path, modules=["core", "auth"])
        local_text = (proj / ".env.local").read_text()
        example_text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in local_text
        assert "STRIPE_SECRET_KEY" not in example_text

    def test_own_payment_provider_closes_the_dev_hatch_too(self, tmp_path):
        proj = _create_monolith(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"PAYMENT_PROVIDER": "app.billing.HouseProvider"}},
        )
        local_text = (proj / ".env.local").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in local_text

    def test_own_payment_provider_drops_the_prod_stripe_placeholder(self, tmp_path):
        """The placeholder prompts for a credential only the DEFAULT provider
        reads. Committed into a project that runs its own backend it reads as
        a requirement the deployment does not have."""
        proj = _create_monolith(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"PAYMENT_PROVIDER": "app.billing.HouseProvider"}},
        )
        assert "STRIPE_SECRET_KEY" not in (proj / ".env.example").read_text()


class TestScaffoldGateSeesTheDevHatch:
    """0.49.0 wrote the hatch into ``.env.local`` and stopped there — but
    ``assemble_scaffold``'s ``check`` gate, the one place that proves a
    generated tree boots at all, read ``.env`` only. So a studio-generated
    monolith selecting billing still went SCAFFOLDING -> FAILED on E104,
    which is the exact outcome 0.49.0 set out to prevent.

    The gate's env is now ``.env`` plus the keys ``.env.local`` ADDS.
    Additions only: ``.env.local`` also re-declares SECRET_KEY and
    POSTGRES_PASSWORD as committed dev placeholders, and letting those win
    trades E104 for ``stapel_core.prodguard`` E001/E002 — a different false
    red, not a fix.
    """

    def test_dev_only_keys_reach_the_gate(self, tmp_path):
        from stapel_tools.assemble_scaffold import _load_dotenv

        proj = _create_monolith(tmp_path, modules=["core", "billing"])
        env = _load_dotenv(proj)
        assert env["ALLOW_UNCONFIGURED_PAYMENT_PROVIDER"] == "1"

    def test_env_wins_over_env_local_on_shared_keys(self, tmp_path):
        from stapel_tools.assemble_scaffold import _load_dotenv

        proj = _create_monolith(tmp_path, modules=["core", "billing"])
        env = _load_dotenv(proj)
        real_secret = dict(
            line.split("=", 1) for line in (proj / ".env").read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        assert env["SECRET_KEY"] == real_secret["SECRET_KEY"].strip()
        assert "django-insecure" not in env["SECRET_KEY"]
        assert env["POSTGRES_PASSWORD"] == real_secret["POSTGRES_PASSWORD"].strip()

    def test_minimal_layout_has_no_pair_and_still_works(self, tmp_path):
        from stapel_tools.assemble_scaffold import _load_dotenv

        proj = _create_minimal(tmp_path, modules=["core", "billing"])
        env = _load_dotenv(proj)
        assert env["ALLOW_UNCONFIGURED_PAYMENT_PROVIDER"] == "1"
        assert "SECRET_KEY" in env
