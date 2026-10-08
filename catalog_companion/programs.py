"""List/search catalog programs without refreshing, uploading or scanning."""
import argparse
import json
import sys

from .cli import connection_arguments, entrypoint, gateway, get_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    connection_arguments(parser)
    parser.add_argument("--search", default="")
    parser.add_argument("--source", choices=("rix-scope", "chaos", "arkadiyt"))
    parser.add_argument("--platform")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all-pages", action="store_true")
    parser.add_argument("--json", action="store_true", help="Machine-readable pagination envelope")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200 or args.offset < 0:
        parser.error("--limit must be 1..200 and --offset must be nonnegative")
    params = {"q": args.search, "active": "true", "limit": args.limit, "offset": args.offset}
    params.update({key: value for key, value in {"source": args.source, "platform": args.platform}.items() if value})
    items = []
    with gateway(args) as client:
        while True:
            page = get_json(client, "GET", "/catalog-api/v1/programs", params=params)
            if (not isinstance(page.get("items"), list) or type(page.get("total")) is not int
                    or type(page.get("has_more")) is not bool):
                raise RuntimeError("Invalid program pagination response")
            items.extend(page["items"])
            if not args.all_pages or not page["has_more"]:
                break
            if not page["items"]:
                raise RuntimeError("Program pagination stalled")
            params["offset"] += len(page["items"])
    result = {"items": items, "total": page["total"], "limit": len(items) if args.all_pages else args.limit,
              "offset": args.offset, "has_more": page["has_more"]}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("ID\tNAME\tPLATFORM\tSOURCES")
        for item in items:
            # Keep external names from injecting terminal control characters/rows.
            values = [str(item.get(key, "")) for key in ("id", "name", "platform", "sources")]
            print("\t".join("".join(c if c.isprintable() else " " for c in value) for value in values))
        print(f"{len(items)} shown / {page['total']} matching; more={page['has_more']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(entrypoint(main))
