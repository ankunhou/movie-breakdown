# 电影剧本结构化拆解 CLI 使用手册

本手册完整说明 `movie-breakdown` 的安装、配置、命令、参数、输入、输出、专家复核、人工修正、产物和故障处理。

所有示例均使用虚构路径和占位符，不对应任何真实剧本。形如 `<当前分析指纹>` 的内容不能原样提交，必须从当前项目生成的模板或产物中复制。

## 阅读导航

| 章节 | 适合解决的问题 |
| --- | --- |
| [01 安装配置与输入](01-安装配置与输入.md) | 如何安装、配置 DeepSeek、准备 TXT/MD/PDF，以及理解 JSON 输出和退出码 |
| [02 叙事命令](02-叙事命令.md) | 通用诊断和 8 条叙事工作流命令的完整参考 |
| [03 制片命令](03-制片命令.md) | `production` 下 9 条命令的完整参考 |
| [04 专家评审与人工修正](04-专家评审与人工修正.md) | 如何填写 4 类严格 JSON、预演修正、重新复核和封版 |
| [05 产物说明与故障排查](05-产物说明与故障排查.md) | 每个文件是什么、缓存如何恢复、常见报错如何处理 |

## 两条流水线

```text
输入剧本
  └─ 叙事 analyze / resume
      ├─ 场景、逐场叙事、全局结构
      ├─ 全人物档案、核心人物小传
      └─ validate → review → correct → 重新 review → finalize

共享 scenes.json
  └─ production analyze / resume
      ├─ 基础制片元素与目录
      └─ plan → review → correct → 重新 review
          └─ finalize --profile evaluation|professional
```

制片流水线只读复用主项目的共享场景，不依赖叙事模型对主题、人物弧光或转折点的判断，也不会改写叙事运行清单。

## 快速开始

### 创建叙事项目

```powershell
uv sync
uv run movie-breakdown doctor

uv run movie-breakdown analyze ".\inputs\sample-screenplay.txt" `
  --project ".\workspace\sample-project" `
  --framework three-act `
  --format-detection auto

uv run movie-breakdown validate ".\workspace\sample-project"
uv run movie-breakdown export ".\workspace\sample-project" --format all
```

如果分析中断：

```powershell
uv run movie-breakdown status ".\workspace\sample-project"
uv run movie-breakdown resume ".\workspace\sample-project"
```

### 创建制片作用域

主项目至少要已有 `artifacts/scenes.json`：

```powershell
uv run movie-breakdown production analyze ".\workspace\sample-project"
uv run movie-breakdown production validate ".\workspace\sample-project"
uv run movie-breakdown production export ".\workspace\sample-project" --format all
uv run movie-breakdown production plan ".\workspace\sample-project"
```

### 获取当前版本帮助

```powershell
uv run movie-breakdown --help
uv run movie-breakdown analyze --help
uv run movie-breakdown production --help
uv run movie-breakdown production finalize --help
```

内置帮助是当前安装版本参数名称和枚举值的最终依据。

## 命令总览

### 叙事与通用命令

| 命令 | 核心输入 | 核心输出 | 调用模型 |
| --- | --- | --- | ---: |
| `--version` | 无 | 版本号 | 否 |
| `doctor` | 环境和工作目录 | 诊断报告 | 默认在线检查 |
| `analyze` | 源剧本、新项目目录 | 完整叙事项目和双格式报告 | 是 |
| `resume` | 已初始化项目 | 恢复后的叙事产物 | 是 |
| `status` | 项目运行清单 | 阶段、用量和错误 | 否 |
| `validate` | 当前叙事产物 | 一致性校验 | 否 |
| `export` | 当前正式叙事聚合 | Markdown/JSON | 否 |
| `review` | 当前分析、可选答案 | 风险抽检、答案模板和质量报告 | 否 |
| `correct` | 修正集和对应答案 | 预演或原子激活修正 | 否 |
| `finalize` | 当前分析和当前复核 | 叙事稳定版门禁报告 | 否 |

### 制片命令

| 命令 | 核心输入 | 核心输出 | 调用模型 |
| --- | --- | --- | ---: |
| `production analyze` | 共享场景、新制片作用域 | 基础制片拆解和四类导出 | 是 |
| `production resume` | 已初始化制片作用域 | 恢复后的制片产物 | 是 |
| `production status` | 制片运行清单 | 四阶段状态和用量 | 否 |
| `production validate` | 当前逐场制片记录 | 制片一致性报告 | 否 |
| `production export` | 当前基础制片聚合 | Markdown/JSON/双 CSV | 否 |
| `production plan` | 有效基础制片拆解 | 单元、实体、数量、安全规划和八类导出 | 否 |
| `production review` | 当前正式规划、可选答案 | 强制目标、答案模板和报告 | 否 |
| `production correct` | 累计修正集和对应答案 | 预演或不可变 generation | 否 |
| `production finalize` | 当前规划和当前复核 | 评测或专业封版报告 | 否 |

## 关键边界

- `--format-detection local` 只让格式识别不用模型；之后的叙事分析仍调用 DeepSeek。
- `--json` 控制终端输出，不等于 `export --format json` 的文件导出。
- `review` 的自动信号是风险代理，不是叙事正确率。
- 修正成功会改变分析或规划指纹，旧答案必须作废并重新复核。
- `evaluation_ready` 可由明确标注的 AI 模拟专家形成，但不是开拍许可。
- `professional_stable` 需要真人专家和逐风险合格专业角色批准，但仍不是政府、保险、预算、场地或现场开拍许可。
- 当前 CLI 会生成评审答案模板，但不会自动生成修正集合模板；修正 JSON 中的作用域指纹需要由受控集成工具按项目的规范内容指纹算法生成。

## 相关文档

- [叙事结构拆解方案](../叙事结构拆解方案.md)
- [制片元素拆解方案](../制片元素拆解方案.md)
- [项目 README](../../README.md)
