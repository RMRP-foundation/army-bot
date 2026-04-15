from beanie import Document


async def try_lock(model: type[Document], doc_id: int, status_field: str, locked_value: str, expected_values: str | list[str]) -> bool:
    if isinstance(expected_values, str):
        expected_values = [expected_values]

    result = await model.get_pymongo_collection().find_one_and_update(
        {
            "_id": doc_id,
            status_field: {"$in": expected_values}
        },
        {
            "$set": {status_field: locked_value}
        },
    )
    return result is not None