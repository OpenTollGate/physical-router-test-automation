import os
import logging

log = logging.getLogger("tollgate.backend")


class BackendConfig:

    def __init__(self, backend_type: str | None = None):
        self.type = (backend_type
                     or os.environ.get("TOLLGATE_BACKEND", "go")).lower()
        if self.type not in ("go", "rust", "basic-rust"):
            raise ValueError(
                f"Unknown backend: {self.type!r}. Must be 'go', 'rust', or 'basic-rust'"
            )

    @property
    def is_rust(self) -> bool:
        return self.type == "rust"

    @property
    def is_go(self) -> bool:
        return self.type == "go"

    @property
    def is_basic_rust(self) -> bool:
        """The 1:1 Go clone in Rust (production drop-in using CDK).

        Behaves like Go for all features except LuCI (which it lacks).
        Tests that skip on ``is_rust`` must NOT skip here — this backend
        is a faithful Go clone and should pass the full Go test suite.
        """
        return self.type == "basic-rust"

    @property
    def repo(self) -> str:
        if self.is_basic_rust:
            return "felixfelix-bot/tollgate-module-basic-rust"
        if self.is_rust:
            return "Amperstrand/tollgate-rs-ai-research-and-experiments"
        return "OpenTollGate/tollgate-module-basic-go"

    @property
    def workflow(self) -> str:
        if self.is_basic_rust:
            return "CI"
        if self.is_rust:
            return "Build and Package"
        return "Build and Publish"

    @property
    def config_path(self) -> str:
        return "/etc/tollgate/config.json"

    @property
    def has_luci(self) -> bool:
        # The Rust 1:1 clone does not ship a LuCI admin UI (same as the
        # experimental Rust backend). Go is the only backend with LuCI.
        return self.is_go

    @property
    def has_cli_socket(self) -> bool:
        # All currently-supported backends expose /var/run/tollgate.sock.
        return True

    @property
    def has_sessions_json(self) -> bool:
        # The Rust 1:1 clone persists sessions to /etc/tollgate/sessions.json
        # exactly like Go. Only the experimental Rust backend is in-memory.
        return self.is_go or self.is_basic_rust

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
