# 发布指南

本文件说明维护者如何从干净的 Git 提交构建、验证并发布 `movie-breakdown`。普通贡献者无需配置发布权限。

## 一次性仓库设置

1. 创建公开 GitHub 仓库并配置正确的默认分支；
2. 在 `pyproject.toml` 的 `[project.urls]` 写入实际仓库、Issue 和文档地址，不得保留占位地址；
3. 启用 Issues、Dependabot、Secret Scanning 和 Private Vulnerability Reporting；
4. 为 `main` 配置分支保护，要求 CI 通过并限制直接推送；
5. 创建 `testpypi` 和 `pypi` 两个 GitHub Environments，建议设置维护者人工批准；
6. 在 TestPyPI 和 PyPI 分别配置与对应 workflow、environment 完全一致的 Trusted Publisher。

项目首次发布时可以在 PyPI 配置 Pending Trusted Publisher，无需先手工上传。Pending Publisher 在第一次成功发布前不会保留包名，因此不应把配置完成视为名称已锁定。配置方法见 [PyPI Trusted Publishing 文档](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)。

当前 workflow 约定：

| 索引 | Workflow | Environment | 触发方式 |
| --- | --- | --- | --- |
| TestPyPI | `.github/workflows/testpypi.yml` | `testpypi` | GitHub Actions 手动触发 |
| PyPI | `.github/workflows/release.yml` | `pypi` | 发布 GitHub Release |

Trusted Publisher 不需要在仓库中保存长期 PyPI Token。它使用 GitHub OIDC 换取短期凭据；仓库所有者仍必须严格保护发布 workflow、环境审批和标签权限。

## 发布前检查

从干净 checkout 执行，不复用工作区中旧的 `dist/`：

```powershell
uv sync --locked --all-groups
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not live" --cov-fail-under=85
uv build --no-sources --clear
uv run --isolated --no-project --with .\dist\*.whl movie-breakdown --version
uv run --isolated --no-project --with .\dist\*.tar.gz movie-breakdown --version
```

还必须确认：

- `git status --short` 为空；
- 版本只在 `pyproject.toml` 声明，运行时版本能够从安装元数据读取；
- `CHANGELOG.md` 已把对应条目从“未发布”移动到带日期的版本标题；
- wheel 和 sdist 都包含 `LICENSE`，且不包含 `.env`、真实剧本、模型产物或本地路径；
- 离线测试没有调用 DeepSeek；需要验证模型契约时单独显式运行并记录费用；
- Git tag、包版本和 GitHub Release 版本完全一致，格式为 `vX.Y.Z`。

## TestPyPI 预发布

在 GitHub Actions 中手动运行“发布到 TestPyPI” workflow。环境批准后，workflow 会重新构建并验证发行包，再通过 Trusted Publishing 上传。

安装验证时使用隔离环境，并替换成实际版本：

```powershell
uv run --isolated --no-project `
  --index-url https://test.pypi.org/simple/ `
  --extra-index-url https://pypi.org/simple/ `
  --with "movie-breakdown==X.Y.Z" `
  movie-breakdown --version
```

TestPyPI 与 PyPI 都不允许覆盖同名同版本文件。测试发布发现问题时必须提升版本，不能重新上传已经使用的版本号。

## 正式发布

1. 合并并确认 `main` 的 CI 全部通过；
2. 创建指向已验证提交的 `vX.Y.Z` 标签；
3. 从该标签发布 GitHub Release，并粘贴对应 Changelog；
4. `release.yml` 校验标签与包版本一致，重新构建并冒烟测试；
5. `pypi` environment 批准后，Trusted Publishing 上传发行包；
6. 在 PyPI 页面核对许可证、作者、Python 版本、文件哈希和项目链接；
7. 在全新环境安装正式版本，复验 `movie-breakdown --version` 与 `doctor --no-online`。

发布失败时不要手工上传来源不明的本地 `dist/`。修复 workflow 或元数据后，使用新的版本号重新走完整流程。
