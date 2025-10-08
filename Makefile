
MAX_LINE = 99
SOURCES = src

.PHONY: install check flake8 black black-check mypy

check: flake8 black-check mypy
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

