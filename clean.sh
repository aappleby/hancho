find . -type d -name build -prune -exec rm -rf {} +
find . -type d -name __pycache__ -prune -exec rm -rf {} +
find . -type d -name .pytest_cache -prune -exec rm -rf {} +
