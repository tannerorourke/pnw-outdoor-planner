# Shared HTTP, error, and output helpers for the weather-report scripts.
# Imported by sibling scripts; not run directly.
import argparse
import json
import math
import sys
import urllib.request

UA = "pnw-outdoor-planner (github.com/tannerorourke)"
TIMEOUT = 20


def fetch(url: str, *, raw: bool = False):
    """GET url with the house User-Agent; exit 1 with a one-line stderr message on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json, */*"})
    # -- one retry on timeout only: the AWDB service intermittently stalls past 20 s on a first call
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read().decode()
            break
        except TimeoutError as e:
            if attempt == 2:
                sys.exit(f"fetch failed: {url}: {e}")
        except Exception as e:
            sys.exit(f"fetch failed: {url}: {e}".replace("\n", " "))
    return body if raw else json.loads(body)


# -- parent parser so '--json-indent' is accepted before or after the subcommand; SUPPRESS keeps a subparser from resetting a value given earlier
INDENT_PARENT = argparse.ArgumentParser(add_help=False)
INDENT_PARENT.add_argument("--json-indent", type=int, default=argparse.SUPPRESS)


def emit(obj, args) -> None:
    print(json.dumps(obj, indent=getattr(args, "json_indent", 0) or None))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))
