"""Explicit installation into immutable artifacts and scoped registrations."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import uuid

from wire import environment, loads
from decision_cli.errors import ProviderError

RESERVED = {"typesafe", "openai", "gemini", "cloudflare"}


def manifest(root):
    value = loads((root / "decision-provider.json").read_bytes())
    if not isinstance(value, dict) or type(value.get("protocol_version")) is not int or value["protocol_version"] != 1:
        raise ValueError("Unsupported provider manifest")
    if not isinstance(value.get("name"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value["name"]) or value["name"] in RESERVED:
        raise ValueError("Invalid or reserved provider name")
    if not isinstance(value.get("version"), str) or not value["version"].strip():
        raise ValueError("Invalid provider version")
    for field in ("command", "setup"):
        command = value.get(field)
        if field == "setup" and command is None:
            continue
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x or "\0" in x for x in command):
            raise ValueError("Provider commands must be nonempty argv arrays")
    check_url(value.get("default_base_url"))
    if "default_model" in value and (not isinstance(value["default_model"], str) or not value["default_model"].strip()):
        raise ValueError("Invalid default model")
    names = value.get("credential_env_vars", [])
    if not isinstance(names, list) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) for x in names):
        raise ValueError("Invalid credential variable names")
    if not isinstance(value.get("schema_constraints", {}), dict):
        raise ValueError("Invalid provider schema constraints")
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(value.get("schema_constraints", {}))
    return value


def check_url(value):
    if not isinstance(value, str):
        raise ValueError("Provider URL required")
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("URL requires HTTPS or loopback HTTP without credentials")
    _ = url.port
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Redirects are unsupported")


def run(command, cwd):
    child = subprocess.Popen(command, cwd=cwd, env=environment(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        if child.wait(timeout=30):
            raise ProviderError("Install command failed")
    except BaseException:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        raise


class Store:
    def __init__(self, home, project=None):
        self.home = Path(home).expanduser().resolve()
        self.personal = self.home / "registry.json"
        self.project = Path(project).expanduser().resolve() if project else None

    def read(self, path):
        if not path.exists():
            return {"providers": {}}
        value = loads(path.read_bytes())
        if not isinstance(value, dict) or not isinstance(value.get("providers"), dict):
            raise ValueError("Invalid provider registry")
        return value

    def registrations(self):
        value = dict(self.read(self.personal)["providers"])
        if self.project:
            value.update(self.read(self.project)["providers"])
        return value

    def write(self, value):
        target = self.project or self.personal
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as temp:
            json.dump(value, temp)
        os.replace(temp.name, target)

    def resolve(self, name):
        registration = self.registrations().get(name)
        if not registration:
            raise ValueError("Provider is not installed")
        root = Path(registration["path"])
        value = manifest(root)
        if value["name"] != name or value["version"] != registration["version"]:
            raise ValueError("Installed provider metadata changed")
        return root, value

    def listing(self):
        return [{"name": name, "version": item["version"]} for name, item in sorted(self.registrations().items())]

    def remove(self, name):
        target = self.project or self.personal
        value = self.read(target)
        if name not in value["providers"]:
            raise ValueError("Provider is not registered in selected scope")
        del value["providers"][name]
        self.write(value)
        return {"name": name, "removed": True}

    def install(self, source, sha256=None):
        self.home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=self.home) as temp:
            stage = Path(temp) / "package"
            commit = None
            if source.startswith("git+"):
                remote, separator, ref = source[4:].rpartition("#")
                if not separator or not ref or ref.startswith("-"):
                    raise ValueError("Git source needs an explicit ref")
                url = urlsplit(remote)
                if url.scheme == "file":
                    if url.netloc or not Path(url.path).is_absolute():
                        raise ValueError("Git file fixture requires an absolute local path")
                else:
                    check_url(remote)
                    if url.scheme != "https":
                        raise ValueError("Git requires HTTPS")
                run(["git", "-c", "http.followRedirects=false", "clone", "--no-checkout", "--", remote, str(stage)], temp)
                run(["git", "checkout", "--detach", ref], stage)
                commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=stage, env=environment(), stderr=subprocess.DEVNULL, timeout=30).decode().strip()
                shutil.rmtree(stage / ".git")
            elif source.startswith(("https://", "http://")):
                check_url(source)
                if not sha256 or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
                    raise ValueError("Archive installation requires SHA-256")
                archive = Path(temp) / "source.tar.gz"
                digest = hashlib.sha256()
                with build_opener(NoRedirect).open(source, timeout=30) as response, archive.open("wb") as output:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 128 * 1024 * 1024:
                            raise ValueError("Archive exceeds install limit")
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != sha256.lower():
                    raise ValueError("Archive digest mismatch")
                stage.mkdir()
                with tarfile.open(archive, "r:gz") as tar:
                    total = 0
                    for member in tar.getmembers():
                        destination = (stage / member.name).resolve()
                        total += member.size
                        if not destination.is_relative_to(stage.resolve()) or not (member.isfile() or member.isdir()) or total > 256 * 1024 * 1024:
                            raise ValueError("Unsafe archive member")
                    tar.extractall(stage, filter="data")
            else:
                local = Path(source).expanduser().resolve()
                if local.is_file():
                    if local.name != "decision-provider.json":
                        raise ValueError("Expected a provider manifest")
                    local = local.parent
                if any(path.is_symlink() for path in local.rglob("*")):
                    raise ValueError("Package symlinks are unsupported")
                shutil.copytree(local, stage, ignore=shutil.ignore_patterns(".git"))
            if any(path.is_symlink() for path in stage.rglob("*")):
                raise ValueError("Package symlinks are unsupported")
            value = manifest(stage)
            artifacts = self.home / "artifacts"
            artifacts.mkdir(exist_ok=True)
            artifact = artifacts / uuid.uuid4().hex
            stage.rename(artifact)
            # Setup runs at the permanent path so author-created venv shebangs
            # and other absolute install paths survive registration activation.
            if value.get("setup"):
                run(value["setup"], artifact)
            if manifest(artifact) != value:
                raise ValueError("Setup changed provider metadata")
            target = self.project or self.personal
            registry = self.read(target)
            registry["providers"][value["name"]] = {"path": str(artifact), "version": value["version"], "commit": commit}
            self.write(registry)
            return {"name": value["name"], "version": value["version"], "restart_required": True}
