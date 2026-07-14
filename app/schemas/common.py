"""
所有 schema 的公共基类。JSON 走 camelCase（跟后端 Java DTO 的 Jackson 序列化习惯保持一致），
Python 代码内部走 snake_case，通过 alias 互相转换，两边都能用。
"""
from typing import get_origin

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel


def _empty_container_for(annotation):
    """
    annotation 可能是裸类型（比如 `dict`）也可能是带参数的泛型（比如 `dict[str, Any]`），
    get_origin() 只对后者有效，裸类型要直接判断 annotation 本身。返回 None 表示这个字段
    不是list/dict类型，不用管。
    """
    origin = get_origin(annotation)
    if origin is list or annotation is list:
        return []
    if origin is dict or annotation is dict:
        return {}
    return None


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _null_collections_to_empty(cls, data):
        """
        Java DTO 里 List<X>/Map<K,V> 字段没有值的时候，Jackson序列化出来是显式的 `null`，
        不是省略这个key、也不是 `[]`/`{}`。pydantic 的字段默认值只在 key 完全缺失时才生效，
        显式传 null 还是会按字段类型校验，直接报错——这里统一在校验之前把
        "list/dict类型字段 + 值是None" 的情况转成对应的空容器，一次性解决所有这类字段的问题，
        不用给每个字段单独写 validator。
        """
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            empty_value = _empty_container_for(field.annotation)
            if empty_value is None:
                continue
            for key in (field.alias, name):
                if key and key in data and data[key] is None:
                    data[key] = empty_value.copy()
        return data
