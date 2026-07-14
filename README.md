# T3 Resolve Batch Service

课题四 T3 算法接口的 Python 实现，基于 FastAPI，大模型走内网 vLLM 部署的 Qwen3（OpenAI 兼容接口，跟 T1/T2 连的是同一个模型服务）。

对应规约文档（课题四后端仓库 `docs/` 目录）：
- `T3实体消歧接口规约.md` —— 唯一权威来源

## 接口

`POST /resolve_batch` —— 给定一批 mention + 各自的候选实体列表，判断每个 mention 应该 `MERGE`（合并进某个候选）/`REVIEW`（转人工审核）/`CREATE`（新建）。**不读写任何数据库**，候选实体是后端已经用 ES/Milvus 查好传进来的，这个接口只负责判断。

同一个接口同时服务两种场景，输入结构完全一样，不需要区分调用方是谁：
- T2 内容抽取之后的实体消歧
- 账号身份识别（判断账号 `displayName` 对应哪个 Person/Organization）

## 快速开始（本地）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：填入内网 vLLM 的 LLM_BASE_URL（形如 http://<内网地址>:8000/v1）和 LLM_MODEL

uvicorn app.main:app --reload --port 8003
```

启动后访问 `http://localhost:8003/docs` 看自动生成的接口文档。

## 用 Docker 部署

```bash
docker build -t t3-resolve:latest .
docker run -d -p 5000:5000 --env-file .env --name t3-resolve t3-resolve:latest
```

## 目录结构

```
app/
  main.py                    FastAPI入口
  config.py                  环境变量配置（跟t1_annotation/t2_extract共用同一份实现）
  llm_client.py               大模型调用封装（同上，三个项目完全一致）
  prompts.py                  系统提示词
  schemas/
    common.py                 驼峰命名基类
    resolve_batch.py           请求/响应schema
  services/
    resolve_service.py         业务逻辑，包含对大模型输出的兜底校验（sanitize）
tests/
  test_schemas.py             schema + sanitize 兜底逻辑的测试（不需要真实连上vLLM）
Dockerfile
.dockerignore
```

## 设计说明

- **`resolve_service._sanitize()` 对大模型输出做了比较严格的兜底校验**，这是跟 t1_annotation/t2_extract 不太一样的地方——因为 T3 的判断直接决定"要不要把两个实体合并成一个"，判断错了比其他接口的分类错误更难挽回（合并错的实体不好拆分）。具体校验了这几件事：
  - `action` 不是 `MERGE`/`REVIEW`/`CREATE` 三者之一的，降级成 `CREATE`
  - `action=MERGE`/`REVIEW` 但 `matchedEntityId` 是空的、或者根本不在这个mention自己的候选列表里（大模型编造的id）——降级成 `CREATE`，不会让一个编造出来的实体ID被当真
  - 大模型漏掉了某个mention的判断结果——补一条兜底 `CREATE`，保证每个输入item都有对应输出，不会让请求方拿到数量对不上的结果
  - `results`里出现了不属于这次请求的`mentionId`——直接丢弃
- **大模型整体调用失败时，兜底策略是全部判 `CREATE`**（`resolve_service._build_fallback_response()`）——宁可多建一个之后靠 `EntityDeduplicationJob` 定时任务合并的重复实体，也不冒险把两个不相关的实体错误合并到一起，合并错了才是真正麻烦的事故。
- 阈值（`autoMergeThreshold`/`reviewThreshold`）**以请求里 `strategy` 传的值为准**，不在代码里写死，后端以后调阈值不需要改这个服务。
- 请求里 `candidates[].score` 是后端 ES/Milvus 召回时给的粗筛分数，提示词里明确要求大模型不能只看这个分数，要结合 mention 和候选的实际语义相似度重新判断。

## 测试

```bash
pytest
```
