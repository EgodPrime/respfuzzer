# 使用isort、ruff和black进行代码格式化、删除未使用的导入和修复代码风格问题

uv run ruff check src tests --fix

uv run isort src tests

uv run black src tests