import os
import logging

log = logging.getLogger("tollgate.backend")


class BackendConfig:

    def __init__(self, backend_type: str | None = None):
        self.type = (backend_type
                     or os.environ.get("TOLLGATE_BACKEND", "go")).lower()
        if self.type not in ("go", "rust"):
            raise ValueError(f"Unknown backend: {self.type!r}. Must be 'go' or 'rust'")

    @property
    def is_rust(self) -> bool:
        return self.type == "rust"

    @property
    def is_go(self) -> bool:
        return self.type == "go"

    @property
    def repo(self) -> str:
        if self.is_rust:
            return "Amperstrand/tollgate-rs-ai-research-and-experiments"
        return "OpenTollGate/tollgate-module-basic-go"

    @property
    def workflow(self) -> str:
        if self.is_rust:
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
        return self.is_go

    @property
    def has_sessions_json(self) -> bool:
        return self.is_go

    @property
    def has_config_json(self) -> bool:
        """Whether the backend uses /etc/tollgate/config.json for configuration."""
        return self.is_go

    @property
    def service_name(self) -> str:
        return "tollgate-wrt"
