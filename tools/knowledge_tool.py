import json
import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def search_product_docs(source_system, error_message=None):
    """
    Search product documentation by source system
    and optionally match an error message.
    """

    file_path = os.path.join(DATA_DIR, "product_docs.json")

    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    results = []

    for doc in data["documents"]:
        if doc["source_system"].lower() != source_system.lower():
            continue

        if error_message:
            searchable_text = " ".join(
                doc.get("error_messages", [])
                + doc.get("error_codes", [])
                + [doc.get("topic", "")]
            ).lower()

            if error_message.lower() not in searchable_text:
                continue

        results.append(doc)

    return results


if __name__ == "__main__":
    matches = search_product_docs(
        source_system="PM7",
        error_message="Access denied"
    )

    print(json.dumps(matches, indent=2, ensure_ascii=False))