"""Focused unit coverage for the framework-light PR3 security primitives."""

from __future__ import annotations

import threading

import pytest

from core.security import (
    CanonicalHost,
    CanonicalOrigin,
    ProductionConfigurationError,
    SlidingWindowLimiter,
    apply_security_headers,
    build_csp,
    build_security_headers,
    canonical_host,
    canonical_origin,
    host_is_allowed,
    is_production_mode,
    parse_allowed_hosts,
    parse_trust_proxy_hops,
    public_origin_policy,
    require_production_config,
    resolve_public_base_url,
    validate_production_config,
    validate_request_origin,
)


GOOD_SECRET = "8a4f6c2e91d0b7a53c8e4f1a6b9d2e70"
GOOD_SETUP_TOKEN = "setup-K9m2pR7vT4xQ8nL5wC3z"


def _valid_env(data_dir, **overrides):
    env = {
        "APP_ENV": "production",
        "SECRET_KEY": GOOD_SECRET,
        "SETUP_TOKEN": GOOD_SETUP_TOKEN,
        "DATA_DIR": str(data_dir),
        "FLASK_DEBUG": "false",
        "PUBLIC_BASE_URL": "https://tableview.example",
    }
    env.update(overrides)
    return env


def test_production_detection_platform_marker_cannot_be_downgraded():
    assert not is_production_mode({})
    assert not is_production_mode({"APP_ENV": "development"})
    assert is_production_mode({"APP_ENV": "production"})
    assert is_production_mode({"APP_ENV": "staging"})
    assert is_production_mode({"ENVIRONMENT": "production"})
    assert is_production_mode(
        {"APP_ENV": "development", "ENVIRONMENT": "production"}
    )
    assert is_production_mode(
        {"APP_ENV": "production", "ENVIRONMENT": "development"}
    )
    assert is_production_mode(
        {"APP_ENV": "development", "RAILWAY_DEPLOYMENT_ID": "deploy-1"}
    )


def test_valid_production_configuration_passes(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()

    assert validate_production_config(
        _valid_env(data),
        base_dir=base,
        bootstrap_pristine=True,
    ) == ()
    require_production_config(
        _valid_env(data),
        base_dir=base,
        bootstrap_pristine=True,
    )


def test_railway_public_domain_supplies_public_origin(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data)
    env.pop("PUBLIC_BASE_URL")
    env.update({
        "RAILWAY_DEPLOYMENT_ID": "deploy-1",
        "RAILWAY_PUBLIC_DOMAIN": "tableview.up.railway.app",
    })

    assert resolve_public_base_url(env) == "https://tableview.up.railway.app"
    assert validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=True,
    ) == ()


def test_strong_persisted_secret_can_back_existing_production(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data)
    env.pop("SECRET_KEY")

    assert validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=True,
        persisted_secret_key=GOOD_SECRET,
    ) == ()


def test_persisted_secret_must_be_present_and_strong(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data)
    env.pop("SECRET_KEY")

    missing = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
    )
    weak = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
        persisted_secret_key="short",
    )
    assert {issue.code for issue in missing} == {"secret_key_missing"}
    assert {issue.code for issue in weak} == {"secret_key_weak"}


def test_explicit_weak_secret_is_not_replaced_by_persisted_secret(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data, SECRET_KEY="short")

    issues = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
        persisted_secret_key=GOOD_SECRET,
    )
    assert {issue.code for issue in issues} == {"secret_key_weak"}


def test_railway_domain_fallback_requires_runtime_marker(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data)
    env.pop("PUBLIC_BASE_URL")
    env["RAILWAY_PUBLIC_DOMAIN"] = "tableview.up.railway.app"

    assert resolve_public_base_url(env) == ""
    issues = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"public_base_url_missing"}


def test_explicit_invalid_public_origin_is_not_replaced_by_railway_domain(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(
        data,
        PUBLIC_BASE_URL="not-a-url",
        RAILWAY_DEPLOYMENT_ID="deploy-1",
        RAILWAY_PUBLIC_DOMAIN="tableview.up.railway.app",
    )

    assert resolve_public_base_url(env) == "not-a-url"
    issues = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"public_base_url_invalid"}


def test_explicit_custom_public_origin_wins_over_railway_domain(tmp_path):
    env = _valid_env(
        tmp_path,
        PUBLIC_BASE_URL="https://custom.example",
        RAILWAY_DEPLOYMENT_ID="deploy-1",
        RAILWAY_PUBLIC_DOMAIN="tableview.up.railway.app",
    )

    assert resolve_public_base_url(env) == "https://custom.example"


def test_invalid_production_configuration_reports_every_problem(tmp_path):
    base = tmp_path / "checkout"
    base.mkdir()
    env = {
        "RAILWAY_DEPLOYMENT_ID": "deploy-1",
        "SECRET_KEY": "short",
        "DATA_DIR": str(base),
        "FLASK_DEBUG": "true",
        "SETUP_TOKEN": "tiny",
        "PUBLIC_BASE_URL": "http://tableview.example",
        "TRUST_PROXY_HOPS": "4",
    }

    issues = validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=True,
        data_dir_writable=False,
    )
    assert {issue.code for issue in issues} == {
        "secret_key_weak",
        "data_dir_not_distinct",
        "data_dir_unwritable",
        "flask_debug_enabled",
        "public_base_url_insecure",
        "trust_proxy_hops_invalid",
        "setup_token_weak",
    }
    with pytest.raises(ProductionConfigurationError) as exc_info:
        require_production_config(
            env,
            base_dir=base,
            bootstrap_pristine=True,
            data_dir_writable=False,
        )
    assert exc_info.value.issues == issues
    assert "short" not in str(exc_info.value)


def test_missing_data_and_secrets_fail_only_in_production(tmp_path):
    base = tmp_path / "checkout"
    base.mkdir()
    production = validate_production_config(
        {"APP_ENV": "production"},
        base_dir=base,
        bootstrap_pristine=True,
    )
    assert {issue.code for issue in production} == {
        "secret_key_missing",
        "data_dir_missing",
        "public_base_url_missing",
        "setup_token_missing",
    }
    assert validate_production_config(
        {},
        base_dir=base,
        bootstrap_pristine=True,
    ) == ()


def test_repeated_placeholder_does_not_satisfy_secret_strength(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    issues = validate_production_config(
        _valid_env(data, SECRET_KEY="change-me-change-me-change-me-change-me"),
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"secret_key_weak"}


def test_setup_token_not_required_after_bootstrap(tmp_path):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    env = _valid_env(data)
    env.pop("SETUP_TOKEN")
    assert validate_production_config(
        env,
        base_dir=base,
        bootstrap_pristine=False,
    ) == ()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ALLOWED_HOSTS", "https://bad.example"),
        ("ALLOWED_HOSTS", "*.example.com"),
        ("RAILWAY_PUBLIC_DOMAIN", "bad.example/path"),
        ("RAILWAY_PRIVATE_DOMAIN", "bad host.example"),
    ],
)
def test_configured_host_allowlists_require_exact_hosts(tmp_path, name, value):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    issues = validate_production_config(
        _valid_env(data, **{name: value}),
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"allowed_hosts_invalid"}


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?query=yes",
        "https://example.com/#fragment",
    ],
)
def test_public_base_url_must_be_a_canonical_origin(tmp_path, value):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    issues = validate_production_config(
        _valid_env(data, PUBLIC_BASE_URL=value),
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"public_base_url_invalid"}


@pytest.mark.parametrize("value", ["-1", "4", "1.0", "one", "+1", "01"])
def test_proxy_hops_rejects_noncanonical_or_out_of_range_values(tmp_path, value):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    issues = validate_production_config(
        _valid_env(data, TRUST_PROXY_HOPS=value),
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert {issue.code for issue in issues} == {"trust_proxy_hops_invalid"}


@pytest.mark.parametrize("value", ["", "0", "1", "2", "3"])
def test_proxy_hops_accepts_absent_or_bounded_integer(tmp_path, value):
    base = tmp_path / "checkout"
    data = tmp_path / "volume"
    base.mkdir()
    data.mkdir()
    issues = validate_production_config(
        _valid_env(data, TRUST_PROXY_HOPS=value),
        base_dir=base,
        bootstrap_pristine=False,
    )
    assert issues == ()


def test_proxy_hop_parser_exposes_validated_integration_value():
    assert parse_trust_proxy_hops(None) == 0
    assert parse_trust_proxy_hops(" 2 ") == 2
    assert parse_trust_proxy_hops("", default=1) == 1
    with pytest.raises(ValueError):
        parse_trust_proxy_hops("4")
    with pytest.raises(ValueError):
        parse_trust_proxy_hops(None, default=4)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM.", CanonicalHost("example.com", None)),
        ("example.com:443", CanonicalHost("example.com", 443)),
        ("[2001:0db8::1]:8443", CanonicalHost("2001:db8::1", 8443)),
        ("b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example", CanonicalHost("xn--bcher-kva.example", None)),
    ],
)
def test_host_canonicalization(raw, expected):
    assert canonical_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://example.com",
        "*.example.com",
        "user@example.com",
        "example.com/path",
        "example.com:",
        "example.com?",
        "example.com#",
        "example.com:99999",
        "example.com\\@evil.test",
    ],
)
def test_host_canonicalization_rejects_ambiguous_values(raw):
    with pytest.raises(ValueError):
        canonical_host(raw)


def test_allowed_hosts_are_exact_and_default_port_aware():
    allowed = parse_allowed_hosts("TABLEVIEW.EXAMPLE., localhost:5001")
    assert host_is_allowed("tableview.example", allowed, scheme="https")
    assert host_is_allowed("tableview.example:443", allowed, scheme="https")
    assert host_is_allowed("localhost:5001", allowed, scheme="http")
    assert not host_is_allowed("evil.tableview.example", allowed, scheme="https")
    assert not host_is_allowed("tableview.example:444", allowed, scheme="https")

    explicit_default = parse_allowed_hosts("tableview.example:443")
    assert host_is_allowed("tableview.example", explicit_default, scheme="https")


def test_public_origin_policy_derives_exact_allowed_host():
    policy = public_origin_policy("https://TABLEVIEW.example:443/")
    assert policy.origin == CanonicalOrigin("https", "tableview.example", 443)
    assert policy.allowed_hosts == frozenset({CanonicalHost("tableview.example")})
    assert host_is_allowed("tableview.example:443", policy.allowed_hosts, scheme="https")

    nondefault = public_origin_policy("https://localhost:8443")
    assert nondefault.allowed_hosts == frozenset({CanonicalHost("localhost", 8443)})
    with pytest.raises(ValueError):
        public_origin_policy("http://tableview.example")


def test_origin_canonicalization_normalizes_case_idna_and_default_ports():
    assert canonical_origin("HTTPS://Example.COM.:443") == CanonicalOrigin(
        "https", "example.com", 443
    )
    assert canonical_origin("https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example") == CanonicalOrigin(
        "https", "xn--bcher-kva.example", 443
    )
    assert str(canonical_origin("http://[2001:db8::1]:8080")) == (
        "http://[2001:db8::1]:8080"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "file:///tmp/a",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com/?q=1",
        "https://example.com:",
        "https://example.com?",
        "https://example.com#",
        "https://example.com\r\nX-Evil: yes",
    ],
)
def test_origin_canonicalization_rejects_opaque_or_non_origin_values(raw):
    with pytest.raises(ValueError):
        canonical_origin(raw)


def test_request_origin_accepts_exact_origin_or_referer():
    exact = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        origin_header="https://TABLEVIEW.example:443",
        sec_fetch_site="same-origin",
        allowed_hosts=parse_allowed_hosts("tableview.example"),
    )
    assert exact.allowed and exact.reason == "same_origin"

    referer = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        referer_header="https://tableview.example/me?page=2",
        allowed_hosts=parse_allowed_hosts("tableview.example"),
    )
    assert referer.allowed and referer.source == "referer"


def test_request_origin_rejects_sibling_missing_and_untrusted_host():
    sibling = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        origin_header="https://evil.example",
        sec_fetch_site="same-site",
        allowed_hosts=parse_allowed_hosts("tableview.example"),
    )
    assert not sibling.allowed and sibling.reason == "origin_mismatch"

    missing = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
    )
    assert not missing.allowed and missing.reason == "missing_origin"

    explicit_non_cookie_integration = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        allow_missing=True,
    )
    assert explicit_non_cookie_integration.allowed

    poisoned = validate_request_origin(
        request_scheme="https",
        request_host="evil.example",
        origin_header="https://evil.example",
        allowed_hosts=parse_allowed_hosts("tableview.example"),
    )
    assert not poisoned.allowed and poisoned.reason == "request_host_not_allowed"


def test_request_origin_rejects_null_or_conflicting_fetch_metadata():
    opaque = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        origin_header="null",
    )
    assert not opaque.allowed and opaque.reason == "invalid_origin"

    conflict = validate_request_origin(
        request_scheme="https",
        request_host="tableview.example",
        origin_header="https://tableview.example",
        sec_fetch_site="cross-site",
    )
    assert not conflict.allowed and conflict.reason == "cross_origin_fetch"


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def test_sliding_window_limit_and_retry_after_boundary():
    clock = _Clock()
    limiter = SlidingWindowLimiter(2, 10, max_keys=4, clock=clock)

    assert limiter.consume("alice").remaining == 1
    clock.value = 101.2
    assert limiter.consume("alice").remaining == 0
    clock.value = 102.1
    denied = limiter.consume("alice")
    assert not denied.allowed
    assert denied.retry_after == 8
    assert denied.retry_after_header == "8"

    clock.value = 110.0
    allowed = limiter.consume("alice")
    assert allowed.allowed
    assert allowed.remaining == 0  # event at 101.2 remains in the window
    clock.value = 111.2
    assert limiter.consume("alice").allowed


def test_limiter_is_bounded_and_unseen_keys_share_overflow_bucket():
    clock = _Clock()
    limiter = SlidingWindowLimiter(2, 30, max_keys=2, clock=clock)
    assert limiter.consume("one").allowed
    assert limiter.consume("two").allowed
    assert limiter.consume("overflow-a").allowed
    assert limiter.consume("overflow-b").allowed
    denied = limiter.consume("overflow-c")
    assert not denied.allowed
    assert limiter.tracked_keys == 2
    assert limiter.stored_events == 4

    clock.value += 31
    assert limiter.prune() == 4
    assert limiter.tracked_keys == 0
    assert limiter.stored_events == 0


def test_limiter_reset_and_concurrent_consumption_are_exact():
    clock = _Clock()
    limiter = SlidingWindowLimiter(25, 60, max_keys=5, clock=clock)
    barrier = threading.Barrier(51)
    decisions = []
    decisions_lock = threading.Lock()

    def consume_once():
        barrier.wait()
        result = limiter.consume("shared")
        with decisions_lock:
            decisions.append(result.allowed)

    workers = [threading.Thread(target=consume_once) for _ in range(50)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert decisions.count(True) == 25
    assert decisions.count(False) == 25
    assert limiter.stored_events == 25
    assert limiter.reset("shared")
    assert limiter.consume("shared").allowed


def test_limiter_rejects_nonfinite_explicit_times():
    limiter = SlidingWindowLimiter(1, 10)
    with pytest.raises(ValueError):
        limiter.consume("key", now=float("nan"))
    with pytest.raises(ValueError):
        limiter.prune(now=float("inf"))


def test_csp_builder_deduplicates_and_rejects_injection():
    assert build_csp(
        {
            "default-src": ("'self'", "'self'"),
            "object-src": "'none'",
        }
    ) == "default-src 'self'; object-src 'none'"
    with pytest.raises(ValueError):
        build_csp({"default-src\r\nX-Evil": ("'self'",)})
    with pytest.raises(ValueError):
        build_csp({"default-src": ("'self'; img-src *",)})
    with pytest.raises(ValueError):
        build_csp({"default-src": ("'self'\x00",)})


def test_security_headers_are_safe_and_environment_aware():
    production = build_security_headers(
        production=True,
        request_is_secure=True,
        sensitive=True,
        report_only_csp={"default-src": ("'self'",)},
    )
    assert production["Strict-Transport-Security"] == "max-age=31536000"
    assert production["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in production["Content-Security-Policy"]
    assert "object-src 'none'" in production["Content-Security-Policy"]
    assert production["Referrer-Policy"] == "same-origin"
    assert production["Cache-Control"] == "no-store, max-age=0"
    assert production["Content-Security-Policy-Report-Only"] == "default-src 'self'"

    local = build_security_headers(production=False, request_is_secure=False)
    assert "Strict-Transport-Security" not in local
    assert "Cache-Control" not in local


def test_apply_security_headers_preserves_existing_values_by_default():
    target = {"referrer-policy": "no-referrer"}
    result = apply_security_headers(
        target,
        production=False,
        request_is_secure=False,
    )
    assert result is target
    assert target["referrer-policy"] == "no-referrer"
    assert "Referrer-Policy" not in target
    assert target["X-Content-Type-Options"] == "nosniff"

    apply_security_headers(
        target,
        overwrite=True,
        production=False,
        request_is_secure=False,
    )
    assert target["referrer-policy"] == "same-origin"
    assert "Referrer-Policy" not in target
