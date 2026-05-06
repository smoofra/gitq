
MAX_LINE = 99
SOURCES = src test

export GITQ_TEMP=

.PHONY: check flake8 black black-check mypy test coverage format lint

check: flake8 black-check mypy test
	@ echo ✅

lint: flake8 black-check mypy
	@ echo ✅

flake8:
	uv run flake8 --max-line-length $(MAX_LINE) --extend-ignore E203 $(SOURCES)

black-check:
	uv run black --check --line-length $(MAX_LINE) $(SOURCES)

black:
	uv run black --line-length $(MAX_LINE) $(SOURCES)

mypy:
	uv run mypy $(SOURCES)

test:
	uv run pytest -n auto

coverage:
	COVERAGE_PROCESS_START=$(PWD)/pyproject.toml COVERAGE_FILE=$(PWD)/.coverage uv run pytest -n auto
	uv run coverage combine
	uv run coverage html
	open htmlcov/index.html

format: flake8 black