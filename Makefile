.PHONY: install run test format lint db clean

install:
	poetry install

run:
	PYTHONPATH=. poetry run python tasks.py run

test:
	PYTHONPATH=. poetry run python tasks.py test

format:
	PYTHONPATH=. poetry run python tasks.py format

lint:
	PYTHONPATH=. poetry run python tasks.py lint

db:
	PYTHONPATH=. poetry run python tasks.py db

clean:
	PYTHONPATH=. poetry run python tasks.py clean
