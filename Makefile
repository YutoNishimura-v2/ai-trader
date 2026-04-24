.PHONY: install install-dev test backtest clean

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

test:
	pytest -q

backtest:
	python -m ai_trader.scripts.run_backtest --config config/default.yaml --synthetic --days 180 --seed 7

clean:
	rm -rf artifacts/runs/* .pytest_cache __pycache__
	find . -name __pycache__ -exec rm -rf {} +
