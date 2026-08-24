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


class TestMinimalBillingWithStripeKey:
    def test_flag_absent_when_a_stripe_key_is_supplied(self, tmp_path):
        proj = _create_minimal(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"STRIPE_SECRET_KEY": "sk_test_x"}},
        )
        text = (proj / ".env.example").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in text


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

    def test_stripe_key_supplied_closes_the_dev_hatch_too(self, tmp_path):
        proj = _create_monolith(
            tmp_path, modules=["core", "billing"],
            module_config={"billing": {"STRIPE_SECRET_KEY": "sk_test_x"}},
        )
        local_text = (proj / ".env.local").read_text()
        assert "ALLOW_UNCONFIGURED_PAYMENT_PROVIDER" not in local_text
