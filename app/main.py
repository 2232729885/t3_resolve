"""
T3 实体消歧判断服务 - FastAPI 入口。

  POST /resolve_batch   实体消歧判断（MERGE/REVIEW/CREATE），不读写任何数据库

对应 docs/T3实体消歧接口规约.md（课题四后端仓库）。
这个接口同时服务内容抽取（T2之后）和账号身份识别两种场景，输入结构完全一样。
"""
import logging

from fastapi import FastAPI

from app.config import get_settings
from app.schemas.resolve_batch import ResolveBatchRequest, ResolveBatchResponse
from app.services import resolve_service

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="T3 Resolve Batch Service",
    description="实体消歧判断（MERGE/REVIEW/CREATE），课题四 T3 算法接口实现",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/resolve_batch", response_model=ResolveBatchResponse)
def resolve_batch(request: ResolveBatchRequest) -> ResolveBatchResponse:
    return resolve_service.resolve_batch(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.service_port, reload=True)
