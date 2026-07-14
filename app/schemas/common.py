"""
所有 schema 的公共基类。JSON 走 camelCase（跟后端 Java DTO 的 Jackson 序列化习惯保持一致），
Python 代码内部走 snake_case，通过 alias 互相转换，两边都能用。
"""
from typing import get_origin

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel


def _is_list_annotation(annotation) -> bool:
    return get_origin(annotation) is list


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _null_lists_to_empty(cls, data):
        """
        Java DTO 里 List<X> 字段没有值的时候，Jackson序列化出来是显式的 `null`，
        不是省略这个key、也不是 `[]`。pydantic 的字段默认值只在 key 完全缺失时才生效，
        显式传 null 还是会按字段类型校验，直接报错——这里统一在校验之前把
        "list类型字段 + 值是None" 的情况转成空列表，一次性解决所有list字段的这个问题，
        不用给每个字段单独写 validator。
        """
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            if not _is_list_annotation(field.annotation):
                continue
            for key in (field.alias, name):
                if key and key in data and data[key] is None:
                    data[key] = []
        return data
