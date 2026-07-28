import os
import logging

log = logging.getLogger("tollgate.backend")

BACKEND_TYPES = ("go", "go-cdk", "rust", "rust-basic", "rust-embedded")
BACKEND_CHOICES_CLI = ("go", "rust", "rust-basic", "rust-embedded")


class BackendConfig:

    def __init__(self, backend_type: str | None = None):
        self.type = (backend_type
                     or os.environ.get("TOLLGATE_BACKEND", "go")).lower()
        if self.type not in BACKEND_TYPES:
            raise ValueError(f"Unknown backend: {self.type!r}. Must be one of: {', '.join(BACKEND_TYPES)}")

    @property
    def is_rust(self) -> bool:
        return self.type == "rust"

    @property
    def is_rust_family(self) -> bool:
        return self.type in ("rust", "rust-basic", "rust-embedded")

    @property
    def is_go(self) -> bool:
        return self.type in ("go", "go-cdk")

    @property
    def is_go_cdk(self) -> bool:
        return self.type == "go-cdk"

    @property
    def is_rust_basic(self) -> bool:
        return self.type == "rust-basic"

    @property
    def is_rust_embedded(self) -> bool:
        return self.type == "rust-embedded"

    @property
    def build_tags(self) -> str:
        return "cdk_wallet" if self.is_go_cdk else ""

    @property
    def needs_cgo(self) -> bool:
        return self.is_go_cdk

    @property
    def repo(self) -> str:
        if self.is_rust:
            return "Amperstrand/tollgate-rs-ai-research-and-experiments"
        if self.is_rust_basic or self.is_rust_embedded:
            return "felixfelix-bot/tollgate-module-basic-rust"
        return "OpenTollGate/tollgate-module-basic-go"

    @property
    def workflow(self) -> str:
        if self.is_rust or self.is_rust_basic or self.is_rust_embedded:
            return "Build and Package"
        return "Build and Publish"

    @property
    def config_path(self) -> str:
        return "/etc/tollgate/config.json"

    @property
    def has_luci(self) -> bool:
        return self.is_go

    @property
    def has_cli_socket(self) -> bool:
        return True  # Rust V1Server now has CLI socket (Phase 2, experimental branch)

    @property
    def has_sessions_json(self) -> bool:
        return self.is_go or self.is_rust_basic or self.is_rust_embedded

    @property
    def has_embedded_portal(self) -> bool:
        return self.is_rust_embedded

    @property
    def cargo_features(self) -> str:
        return "embedded-portal" if self.is_rust_embedded else ""

    @property
    def has_config_json(self) -> bool:
        """Whether the backend uses /etc/tollgate/config.json for configuration.

        Both Go and Rust backends read config.json when present. The Rust init
        script passes ``--config /etc/tollgate/config.json`` to the binary when
        the file exists (falling back to CLI flags otherwise). We always write
        a compat config for Rust via ``_write_rust_compat_config()``.
        """
        return True

    @property
    def service_name(self) -> str:
        return "tollgate-wrt"
