"""Application settings (JSON in the user config folder) and API keys (OS credential store via keyring).

Windows: Credential Manager, macOS: Keychain, Linux: Secret Service. If no credential store is available,
keys are written to `secrets.json` next to the settings, readable only by the current user."""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import requests

from plagcheck import i18n
from plagcheck.config import Config

# Internal folder / keyring names stay "PlagCheck" (the app's former name) so existing settings, saved
# checks and API keys keep working after the rename to "Plagiarism".
APP_NAME = "PlagCheck"
KEYRING_SERVICE = "PlagCheck"
SECRETS = ["serper", "tavily", "anthropic", "openai"]
# Suggestions only: the model fields are free text, so any model name the provider accepts can be typed in.
ANTHROPIC_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-2", "claude-haiku-4-5"]
OPENAI_MODELS = ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"]

DEFAULTS = dict(
    language=None,              # UI language; None = English
    report_lang=None,           # None = same as the UI language
    allow_free_search=True,     # DuckDuckGo when no key / credits are used up
    llm_provider="auto",        # auto | anthropic | openai | none
    anthropic_model=ANTHROPIC_MODELS[0],
    openai_model=OPENAI_MODELS[0],
    final_model="",
    openai_base_url="",
    unpaywall_email="",
    search_budget=2400,
    max_sources=900,
    use_wayback=True,
    include_refs=False,
    notify=True,
    keep_awake=True,
    first_run_done=False,
    recommend_notice=True,      # startup notice recommending Serper + an AI model
)


def config_dir() -> Path:
    from platformdirs import user_config_dir
    p = Path(os.environ.get("PLAGCHECK_CONFIG_DIR") or user_config_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    from platformdirs import user_data_dir
    p = Path(os.environ.get("PLAGCHECK_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


class SecretStore:
    def __init__(self, folder: Path):
        self.file = folder / "secrets.json"
        self.lock = threading.Lock()
        self.backend = "file"
        try:
            import keyring
            from keyring.backends import fail
            kr = keyring.get_keyring()
            if not isinstance(kr, fail.Keyring) and "null" not in type(kr).__name__.lower():
                keyring.get_password(KEYRING_SERVICE, "__probe__")
                self.backend = "keyring"
        except Exception:
            self.backend = "file"

    def _read_file(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_file(self, d: dict):
        self.file.write_text(json.dumps(d), encoding="utf-8")
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass

    def get(self, name: str) -> str:
        if self.backend == "keyring":
            try:
                import keyring
                return keyring.get_password(KEYRING_SERVICE, name) or ""
            except Exception:
                pass
        with self.lock:
            return self._read_file().get(name, "")

    def set(self, name: str, value: str):
        value = (value or "").strip()
        if self.backend == "keyring":
            try:
                import keyring
                if value:
                    keyring.set_password(KEYRING_SERVICE, name, value)
                else:
                    try:
                        keyring.delete_password(KEYRING_SERVICE, name)
                    except Exception:
                        pass
                return
            except Exception:
                self.backend = "file"
        with self.lock:
            d = self._read_file()
            if value:
                d[name] = value
            else:
                d.pop(name, None)
            self._write_file(d)


class Settings:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else config_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / "settings.json"
        self.secrets = SecretStore(self.folder)
        self.data = dict(DEFAULTS)
        try:
            self.data.update(json.loads(self.file.read_text(encoding="utf-8")))
        except Exception:
            pass

    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def update(self, values: dict):
        for k, v in (values or {}).items():
            if k in DEFAULTS:
                self.data[k] = v
        self.save()

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    @property
    def language(self) -> str:
        return i18n.normalize(self["language"] or i18n.FALLBACK)

    @property
    def report_language(self) -> str:
        return i18n.normalize(self["report_lang"]) if self["report_lang"] else self.language

    def has(self, name: str) -> bool:
        return bool(self.secrets.get(name))

    def search_chain(self) -> list[str]:
        chain = [n for n in ("serper", "tavily") if self.has(n)]
        if self["allow_free_search"]:
            chain.append("ddg")
        return chain

    def llm(self) -> tuple[str, str]:
        p = self["llm_provider"]
        if p == "auto":
            p = "anthropic" if self.has("anthropic") else "openai" if self.has("openai") else "none"
        if p == "anthropic":
            return p, self["anthropic_model"] or ANTHROPIC_MODELS[0]
        if p == "openai":
            return p, self["openai_model"] or OPENAI_MODELS[0]
        return "none", ""

    def public(self) -> dict:
        provider, model = self.llm()
        return dict(self.data, keys={n: self.has(n) for n in SECRETS}, secret_store=self.secrets.backend,
                    effective_language=self.language, effective_report_lang=self.report_language,
                    search_chain=self.search_chain(), effective_llm=dict(provider=provider, model=model),
                    anthropic_models=ANTHROPIC_MODELS, openai_models=OPENAI_MODELS,
                    folder=str(self.folder))

    def build_config(self, work: Path, pdf: Path, meta: dict, options: dict) -> Config:
        provider, model = self.llm()
        chain = self.search_chain()
        o = dict(options or {})
        return Config.blank(
            pdf=str(pdf), work=str(work),
            doc_title=meta.get("title", ""), doc_author=meta.get("author", ""), doc_date=meta.get("date", ""),
            author_markers=meta.get("author_markers", ""), body_pages=meta.get("body_pages", ""),
            ref_pages=meta.get("ref_pages", ""),
            search_backend=",".join(chain) if chain else "none",
            search_budget=int(o.get("search_budget", self["search_budget"]) or 0),
            serper_key=self.secrets.get("serper"), tavily_key=self.secrets.get("tavily"),
            max_sources=int(o.get("max_sources", self["max_sources"]) or 900),
            unpaywall_email=self["unpaywall_email"] or "", use_wayback=bool(self["use_wayback"]),
            local_sources=str(Path(work).parent / "local_sources"),
            seeds_file=str(Path(work).parent / "seeds.txt") if (Path(work).parent / "seeds.txt").exists() else "",
            overrides_file=str(Path(work).parent / "source_overrides.json")
            if (Path(work).parent / "source_overrides.json").exists() else "",
            llm_provider=o.get("llm_provider") or provider, llm_model=o.get("llm_model") or model,
            llm_final_model=o.get("final_model", self["final_model"]) or "",
            anthropic_key=self.secrets.get("anthropic"), openai_key=self.secrets.get("openai"),
            openai_base_url=self["openai_base_url"] or "",
            report_lang=o.get("report_lang") or self.report_language,
        )


# ---------------------------------------------------------------- key checks

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def test_key(name: str, value: str, base_url: str = "") -> dict:
    """Checks a key with one cheap request. Returns {ok, code, detail, models?}; `code` is an i18n key suffix."""
    value = (value or "").strip()
    if name == "unpaywall":
        return dict(ok=bool(EMAIL_RE.match(value)), code="ok" if EMAIL_RE.match(value) else "bad_email")
    if not value:
        return dict(ok=False, code="empty")
    try:
        if name == "serper":
            r = requests.post("https://google.serper.dev/search", timeout=20,
                              headers={"X-API-KEY": value, "Content-Type": "application/json"},
                              json={"q": "plagiarism check", "num": 1})
        elif name == "tavily":
            r = requests.post("https://api.tavily.com/search", timeout=30,
                              headers={"Authorization": f"Bearer {value}"},
                              json={"query": "plagiarism check", "max_results": 1})
        elif name == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models", timeout=20,
                             headers={"x-api-key": value, "anthropic-version": "2023-06-01"})
        elif name == "openai":
            r = requests.get(f"{(base_url or 'https://api.openai.com/v1').rstrip('/')}/models", timeout=20,
                             headers={"Authorization": f"Bearer {value}"})
        else:
            return dict(ok=False, code="unknown")
    except requests.RequestException as e:
        return dict(ok=False, code="network", detail=str(e)[:200])
    if r.status_code == 200:
        out = dict(ok=True, code="ok")
        if name in ("anthropic", "openai"):
            try:
                out["models"] = sorted(m["id"] for m in r.json().get("data", []))
            except Exception:
                pass
        return out
    low = r.text[:500].lower()
    if r.status_code in (401, 403):
        return dict(ok=False, code="invalid", detail=r.text[:200])
    if r.status_code in (402, 429, 432, 433) or any(w in low for w in ("credit", "quota", "balance")):
        return dict(ok=False, code="no_credits", detail=r.text[:200])
    return dict(ok=False, code="http", detail=f"HTTP {r.status_code}: {r.text[:200]}")
