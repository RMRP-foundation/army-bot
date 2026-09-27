from pymongo import ReturnDocument

async def atomic_status_transition(
    collection, request_id: int, from_status: str | list[str], to_status: str,
    extra_fields: dict | None = None, extra_filter: dict | None = None,
) -> dict | None:
    status_filter = {"$in": from_status} if isinstance(from_status, list) else from_status
    query = {"_id": request_id, "status": status_filter, **(extra_filter or {})}
    return await collection.find_one_and_update(
        query,
        {"$set": {"status": to_status, **(extra_fields or {})}},
        return_document=ReturnDocument.AFTER,
    )