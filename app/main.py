"""
T3 实体消歧判断服务 - FastAPI 入口。

  POST /resolve_batch   实体消歧判断（MERGE/REVIEW/CREATE），不读写任何数据库

对应 docs/T3实体消歧接口规约.md（课题四后端仓库）。
这个接口同时服务内容抽取（T2之后）和账号身份识别两种场景，输入结构完全一样。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas.resolve_batch import ResolveBatchRequest, ResolveBatchResponse
from app.services import resolve_service

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="T3 Resolve Batch Service",
    description="实体消歧判断（MERGE/REVIEW/CREATE），课题四 T3 算法接口实现",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    FastAPI 默认422只把detail塞进响应体返回给调用方，自己的容器日志里不会打印具体原因，
    排查起来只能靠调用方（Java后端）那边的异常堆栈——但后端那边如果这个调用被try-catch
    包住只记了warn/没记日志，两边都看不到具体是哪个字段的问题。这里补一份服务端自己的日志，
    以后422发生时直接在这个服务的容器日志里就能看到详细原因，不用再靠猜。
    """
    body = await request.body()
    logger.error(
        "422 Unprocessable Entity on %s %s\nvalidation errors: %s\nraw request body: %s",
        request.method, request.url.path, exc.errors(), body.decode("utf-8", errors="replace")[:5000],
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/resolve_batch", response_model=ResolveBatchResponse)
def resolve_batch(request: ResolveBatchRequest) -> ResolveBatchResponse:
    return resolve_service.resolve_batch(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.service_port, reload=True)
