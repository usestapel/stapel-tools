"""Third-wave STAPEL_LIBS onboarding: stapel-docs, stapel-forms,
stapel-moderation. All three shipped with a full MODULE.md/CONFIG.MD/urls.py
surface and a PyPI release, but were never added to the registry
``create_project``/``assemble_scaffold`` read from — a selection naming any
of them silently fell through ``assemble_scaffold``'s "unknown lib" path
instead of generating a working project.

Mirrors the second-wave contract in test_registry_onboarding.py: each entry
resolves (dir/pin/requires/http/url_prefix present and well-formed), the
requires topology stays acyclic and dependency-ordered, and a project that
selects one actually gets it mounted (INSTALLED_APPS + urls) under its own
v1-canon prefix.
"""
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import STAPEL_LIBS, _expand_with_requires

THIRD_WAVE = ["docs", "forms", "moderation"]


class TestRegistryResolves:
    def test_every_third_wave_lib_is_registered(self):
        for key in THIRD_WAVE:
            assert key in STAPEL_LIBS, key

    def test_dir_follows_stapel_underscore_convention(self):
        for key in THIRD_WAVE:
            assert STAPEL_LIBS[key]["dir"] == f"stapel_{key}", key

    def test_every_lib_carries_pin_requires_and_http_flag(self):
        for key in THIRD_WAVE:
            info = STAPEL_LIBS[key]
            assert "pin" in info and info["pin"], key
            assert "ahead_of_pypi" in info, key
            assert isinstance(info.get("requires", []), list), key
            assert info.get("http", True) is True, key

    def test_http_libs_declare_the_v1_canon_bare_mount(self):
        # docs/urls.py, forms/urls.py, moderation/urls.py all bake api/v1/
        # into their own root URLconf (api-versioning.md §2) — hosts mount
        # them bare, e.g. path("docs/", include("stapel_docs.urls")).
        for key in THIRD_WAVE:
            assert STAPEL_LIBS[key]["url_prefix"] == f"{key}/", key

    def test_forms_requires_attributes(self):
        # pyproject.toml: stapel-attributes>=0.4.6,<1.0 — a form's schema IS
        # a list of stapel-attributes FeatureDefs.
        assert STAPEL_LIBS["forms"]["requires"] == ["attributes"]

    def test_docs_and_moderation_have_no_hard_requires(self):
        # Both depend on stapel-core only (pyproject.toml).
        assert STAPEL_LIBS["docs"]["requires"] == []
        assert STAPEL_LIBS["moderation"]["requires"] == []


class TestRequiresTopology:
    def test_forms_dependency_precedes_it_in_registry_order(self):
        order = list(STAPEL_LIBS)
        assert order.index("attributes") < order.index("forms")

    def test_expand_pulls_attributes_in_for_forms(self):
        assert _expand_with_requires(["forms"]) == ["attributes", "forms"]


class TestMountedInGeneratedProject:
    def test_docs_mounts_at_its_own_v1_prefix(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["docs"], output_dir=tmp_path, verify=False
        )
        assert "docs" in result.libs_applied
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        assert '"stapel_docs"' in settings
        assert 'path("docs/", include("stapel_docs.urls"))' in urls

    def test_forms_pulls_attributes_and_both_mount_correctly(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["forms"], output_dir=tmp_path, verify=False
        )
        assert set(result.libs_applied) == {"attributes", "forms"}
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        assert '"stapel_forms"' in settings
        assert 'path("forms/", include("stapel_forms.urls"))' in urls
        # attributes is an L1 library — never mounted as an app or a route.
        assert '"stapel_attributes"' not in settings
        assert "stapel_attributes.urls" not in urls

    def test_moderation_mounts_at_its_own_v1_prefix(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["moderation"], output_dir=tmp_path, verify=False
        )
        assert "moderation" in result.libs_applied
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        assert '"stapel_moderation"' in settings
        assert 'path("moderation/", include("stapel_moderation.urls"))' in urls

    def test_all_three_together_registry_order_not_request_order(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["moderation", "docs", "forms"],
            output_dir=tmp_path, verify=False,
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        i_docs = settings.index('"stapel_docs"')
        i_forms = settings.index('"stapel_forms"')
        i_moderation = settings.index('"stapel_moderation"')
        # Registry order: ... currencies, docs, forms, geo, ... mailtrap,
        # moderation, recordings, ...
        assert i_docs < i_forms < i_moderation
