from __future__ import annotations

import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGE = PROJECT_ROOT / "linkedin_profile_api"
BROWSER_AUTOMATION_PACKAGES = {
    "playwright",
    "pyppeteer",
    "selenium",
    "splinter",
}


def test_runtime_has_no_browser_automation_dependency_or_import() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    dependencies = configuration["project"]["dependencies"]
    dependency_names = {
        dependency.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0]
        for dependency in dependencies
    }
    assert dependency_names.isdisjoint(BROWSER_AUTOMATION_PACKAGES)

    imported_roots: set[str] = set()
    for source_path in RUNTIME_PACKAGE.glob("*.py"):
        module = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(BROWSER_AUTOMATION_PACKAGES)
