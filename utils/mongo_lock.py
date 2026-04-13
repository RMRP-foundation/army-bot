from beanie import Document


async def try_lock(model: type[Document], doc_id: int, status_field: str, locked_value: str, expected_value: str) -> bool:
    result = await model.get_pymongo_collection().find_one_and_update(
        {"_id": doc_id, status_field: expected_value},
        {"$set": {status_field: locked_value}},
    )
    return result is not None