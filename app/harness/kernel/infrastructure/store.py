from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    PutOp,
    SearchItem,
    SearchOp,
)

from ..domain.models import digest
from ..domain.ports import Repository


class ScopedStore(BaseStore):
    def __init__(self, repository: Repository, tenant: str, namespace: str):
        self.repository, self.tenant, self.namespace = repository, tenant, namespace

    def _key(self, namespace: tuple[str, ...], key: str) -> str:
        return digest([self.namespace, namespace, key])

    def _item(self, record: dict[str, Any], search: bool = False) -> Item:
        constructor = SearchItem if search else Item
        return constructor(
            namespace=tuple(record["namespace"]),
            key=record["key"],
            value=record["value"],
            created_at=datetime.fromtimestamp(record["created"], UTC),
            updated_at=datetime.fromtimestamp(record["updated"], UTC),
        )

    def batch(self, ops: Any) -> list[Any]:
        results = []
        with self.repository.transaction():
            for operation in ops:
                if isinstance(operation, GetOp):
                    record = self.repository.get(
                        self.tenant, "engine_store", self._key(operation.namespace, operation.key)
                    )
                    valid = (
                        record
                        and record["value"] is not None
                        and (not record["expires"] or record["expires"] > time.time())
                    )
                    results.append(self._item(record) if valid else None)
                elif isinstance(operation, PutOp):
                    key = self._key(operation.namespace, operation.key)
                    previous = self.repository.get(self.tenant, "engine_store", key)
                    record = {
                        "scope": self.namespace,
                        "namespace": operation.namespace,
                        "key": operation.key,
                        "value": operation.value,
                        "created": previous["created"] if previous else time.time(),
                        "updated": time.time(),
                        "expires": time.time() + operation.ttl * 60 if operation.ttl else None,
                    }
                    self.repository.put(self.tenant, "engine_store", key, record)
                    results.append(None)
                elif isinstance(operation, (SearchOp, ListNamespacesOp)):
                    records = [
                        record
                        for record in self.repository.scan(self.tenant, "engine_store")
                        if record["scope"] == self.namespace
                        and record["value"] is not None
                        and (not record["expires"] or record["expires"] > time.time())
                    ]
                    if isinstance(operation, SearchOp):
                        matches = [
                            record
                            for record in records
                            if tuple(record["namespace"][: len(operation.namespace_prefix)])
                            == operation.namespace_prefix
                            and all(
                                record["value"].get(key) == value
                                for key, value in (operation.filter or {}).items()
                            )
                            and (
                                not operation.query
                                or operation.query.casefold() in str(record["value"]).casefold()
                            )
                        ]
                        results.append(
                            [
                                self._item(record, True)
                                for record in matches[
                                    operation.offset : operation.offset + operation.limit
                                ]
                            ]
                        )
                    else:
                        namespaces = sorted({tuple(record["namespace"]) for record in records})
                        for condition in operation.match_conditions or ():
                            namespaces = [
                                namespace
                                for namespace in namespaces
                                if self._matches(namespace, condition)
                            ]
                        namespaces = sorted(
                            {
                                namespace[: operation.max_depth]
                                if operation.max_depth
                                else namespace
                                for namespace in namespaces
                            }
                        )
                        results.append(
                            namespaces[operation.offset : operation.offset + operation.limit]
                        )
                else:
                    raise TypeError(f"Unsupported store operation {type(operation)}")
        return results

    @staticmethod
    def _matches(namespace: tuple[str, ...], condition: Any) -> bool:
        pattern = condition.path
        selected = (
            namespace[: len(pattern)]
            if condition.match_type == "prefix"
            else namespace[-len(pattern) :]
        )
        return len(selected) == len(pattern) and all(
            expected == "*" or expected == actual
            for expected, actual in zip(pattern, selected, strict=True)
        )

    async def abatch(self, ops: Any) -> list[Any]:
        return await asyncio.to_thread(self.batch, list(ops))
