
MAX_LINE = 99
SOURCES = src test

.PHONY: check flake8 black black-check mypy test format

check: flake8 black-check mypy test
	@ echo ✅

flake8:
	uv run flake8 --max-line-length $(MAX_LINE) $(SOURCES)

black-check:
	uv run black --check --line-length $(MAX_LINE) $(SOURCES)

black:
	uv run black --line-length $(MAX_LINE) $(SOURCES)

mypy:
	uv run mypy $(SOURCES)

test:
	uv run pytest

format: flake8 black