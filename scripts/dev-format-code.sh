# 使用isort、ruff和black进行代码格式化、删除未使用的导入和修复代码风格问题

ruff check src tests --fix

isort src tests

black src tests