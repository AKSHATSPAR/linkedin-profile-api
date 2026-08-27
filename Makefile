.PHONY: build-ProfileFunction verify verify-lock-exports

build-ProfileFunction:
	python3 -m pip install \
		--requirement requirements-lambda.txt \
		--require-hashes \
		--platform manylinux2014_aarch64 \
		--implementation cp \
		--python-version 3.12 \
		--abi cp312 \
		--only-binary=:all: \
		--no-compile \
		--target "$(ARTIFACTS_DIR)"
	tar --exclude='__pycache__' --exclude='*.pyc' -cf - linkedin_profile_api | tar -xf - -C "$(ARTIFACTS_DIR)"

verify:
	uv lock --check
	uv run --locked ruff format --check .
	uv run --locked ruff check .
	uv run --locked mypy linkedin_profile_api
	uv run --locked pytest --cov=linkedin_profile_api --cov-report=term-missing --cov-fail-under=95
	$(MAKE) verify-lock-exports

verify-lock-exports:
	@full_export=$$(mktemp); lambda_export=$$(mktemp); \
	trap 'rm -f "$$full_export" "$$lambda_export"' EXIT; \
	uv export --locked --no-dev --no-emit-project --no-header --output-file "$$full_export" >/dev/null; \
	uv export --locked --no-dev --no-emit-project --prune uvicorn --no-header --output-file "$$lambda_export" >/dev/null; \
	diff -u requirements.txt "$$full_export"; \
	diff -u requirements-lambda.txt "$$lambda_export"
