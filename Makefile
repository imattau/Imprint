.PHONY: install run test format lint db clean

install:
poetry install

run:
poetry run python tasks.py run

test:
poetry run python tasks.py test

format:
poetry run python tasks.py format

lint:
poetry run python tasks.py lint

db:
poetry run python tasks.py db

clean:
poetry run python tasks.py clean
