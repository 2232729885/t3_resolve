"""
配置项。所有配置通过环境变量注入，不在代码里硬编码任何密钥/地址。
本地开发时复制 .env.example 为 .env 并填入真实值。

大模型走 OpenAI 兼容接口——内网 vLLM 部署的 Qwen3（跟课题四后端连的是同一个模型服务），
不是云端 API，所以这里的配置项是通用的 base_url/api_key/model，不绑定具体厂商。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 内网 vLLM OpenAI 兼容endpoint，一般形如 http://<内网地址>:8000/v1
    llm_base_url: str = "http://localhost:8000/v1"
    # vLLM 默认不校验，没配置 --api-key 的话随便填一个非空字符串即可（openai SDK要求非空）
    llm_api_key: str = "EMPTY"
    # 部署的模型名，以 vLLM 启动时 --served-model-name（或模型路径最后一段）为准
    llm_model: str = "Qwen3-32B"

    # LLM调用参数
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # vLLM 是否支持 response_format={"type":"json_object"} 取决于具体版本/启动参数，
    # 不支持的话改成 false，代码会跳过这个参数，靠提示词+兜底解析来保证输出JSON
    llm_use_json_response_format: bool = True
    # Qwen3 是混合推理模型，默认可能开着思考模式，回答前会先生成一大段思考过程再给最终JSON，
    # 对这种schema已经写得很死的结构化抽取任务没必要，还会显著拖慢生成速度、增加超时概率，
    # 默认关掉；如果你们的 vLLM/模型版本不支持 enable_thinking 这个参数导致报错，改成 false
    llm_disable_thinking: bool = True
    # 同一时刻最多有几个请求真正转发给vLLM，超过的排队等待，避免并发太高把vLLM压垮
    # （尤其是多模态请求，图片视觉向量处理需要额外显存，并发一高容易把GPU显存耗尽导致vLLM整体崩溃）
    llm_max_concurrent_requests: int = 4

    # 服务本身
    service_port: int = 8003
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
