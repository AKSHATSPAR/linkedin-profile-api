.PHONY: build-ProfileFunction verify

build-ProfileFunction:
	python3 -m pip install \
		--requirement requirements-lambda.txt \
		--platform manylinux2014_aarch64 \
		--implementation cp \
		--python-version 3.12 \
		--abi cp312 \
		--only-binary=:all: \
		--no-compile \
		--target "$(ARTIFACTS_DIR)"
	tar --exclude='__pycache__' --exclude='*.pyc' -cf - linkedin_profile_api | tar -xf - -C "$(ARTIFACTS_DIR)"

verify:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy linkedin_profile_api
	uv run pytest --cov=linkedin_profile_api --cov-report=term-missing
