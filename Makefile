
MAX_LINE = 99
SOURCES = src test

.PHONY: install check flake8 black black-check mypy test

check: flake8 black-check mypy test
	@ echo ✅

install:
	poetry install

flake8: install
	poetry run flake8 --max-line-length $(MAX_LINE) $(SOURCES)

black-check: install
	poetry run black --check --line-length $(MAX_LINE) $(SOURCES)

black: install
	poetry run black --line-length $(MAX_LINE) $(SOURCES)

mypy: install
	poetry run mypy $(SOURCES)

test: install
	poetry run pytest

