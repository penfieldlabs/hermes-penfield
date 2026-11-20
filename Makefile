# Developer commands. Needs: pip install -r requirements-dev.txt
#
#   make check        lint, types and unit tests (what CI runs first)
#   make test-hermes  integration tests inside a real Hermes Agent install

.PHONY: check lint typecheck test test-hermes

check: lint typecheck test

lint:
	ruff check .
	ruff format --check .

# mypy needs the modules inside a package directory with a valid name; the
# repository root is the plugin itself, so they are copied into one first.
typecheck:
	@tmp=$$(mktemp -d) && mkdir "$$tmp/penfield" && cp ./*.py "$$tmp/penfield/" \
	  && (cd "$$tmp" && mypy --config-file "$(CURDIR)/mypy.ini" penfield/); \
	  status=$$?; rm -rf "$$tmp"; exit $$status

test:
	python -m pytest -m "not integration and not hermes"

# Run with the Python environment that has Hermes Agent installed.
test-hermes:
	HERMES_PENFIELD_REQUIRE_HERMES=1 python -m pytest -m hermes --no-cov
