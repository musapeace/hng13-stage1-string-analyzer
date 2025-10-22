from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import FastAPI, HTTPException, Request, Path, Query, Response
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import hashlib
import re
from threading import Lock

app = FastAPI(title="String Analyzer")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle Pydantic validation errors and return appropriate status codes."""
    errors = exc.errors()
    
    for error in errors:
        if 'value' in error.get('loc', []):
            if error.get('type') == 'missing':
                return JSONResponse(
                    status_code=400,
                    content={"error": "Missing or empty 'value' field"}
                )
            elif error.get('type') in ['string_type', 'type_error']:
                return JSONResponse(
                    status_code=422,
                    content={"error": "Invalid data type for 'value' (must be string)"}
                )
    
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid data type for 'value' (must be string)"}
    )

# Thread-safe in-memory store: sha256_hash -> record
STORE: Dict[str, Dict[str, Any]] = {}
STORE_LOCK = Lock()


# ---------- Helpers ----------
def now_iso_z() -> str:
    """Return current UTC time in ISO8601 with milliseconds and Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_of(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def char_freq_map(s: str) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    return freq


def cleaned_for_palindrome(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def is_palindrome_str(s: str) -> bool:
    c = cleaned_for_palindrome(s)
    return c == c[::-1]


def count_words(s: str) -> int:
    return len(re.findall(r"\S+", s))


def analyze_string(value: str) -> Dict[str, Any]:
    v = value  # value assumed already trimmed by caller if needed
    return {
        "length": len(v),
        "is_palindrome": is_palindrome_str(v),
        "unique_characters": len(set(v)),
        "word_count": count_words(v),
        "sha256_hash": sha256_of(v),
        "character_frequency_map": char_freq_map(v),
    }


# ---------- Request models ----------
class StringInput(BaseModel):
    value: str


# ---------- POST /strings ----------
@app.post("/strings", status_code=201)
def create_string(payload: StringInput):
    # Pydantic ensures type is string; FastAPI will return 422 if missing or wrong type
    value = payload.value.strip()
    if value == "":
        # Match Rust: return 422 for missing/empty value
        raise HTTPException(status_code=422, detail={"error": "Missing or empty 'value' field"})

    props = analyze_string(value)
    id_ = props["sha256_hash"]

    with STORE_LOCK:
        if id_ in STORE:
            # Duplicate: respond 409
            raise HTTPException(status_code=409, detail={"error": "String already exists"})

        entry = {
            "id": id_,
            "value": value,
            "properties": props,
            "created_at": now_iso_z(),
        }
        STORE[id_] = entry

    return entry


# ---------- GET /strings/{string_value} ----------
@app.get("/strings/{string_value}")
def get_string(string_value: str = Path(..., description="Exact string value (URL-encoded if needed)")):
    # Look up by SHA-256 hash of the provided string (same as Rust)
    h = sha256_of(string_value)
    with STORE_LOCK:
        entry = STORE.get(h)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "String does not exist in the system"})
        return entry


# ---------- GET /strings with strict validation ----------
@app.get("/strings")
def get_all_strings(request: Request):
    params = request.query_params  # preserves keys exactly
    allowed = {"is_palindrome", "min_length", "max_length", "word_count", "contains_character"}

    # 1) reject unknown params exactly like Rust would
    for k in params.keys():
        if k not in allowed:
            raise HTTPException(status_code=400, detail={"error": f"Invalid query parameter '{k}'"})

    # parsing helpers that mimic Rust strictness
    def parse_bool_strict(v: Optional[str]) -> Optional[bool]:
        if v is None:
            return None
        if v.strip() == "":
            # empty value considered invalid
            raise ValueError
        lv = v.strip().lower()
        if lv == "true":
            return True
        if lv == "false":
            return False
        # anything else invalid
        raise ValueError

    def parse_int_strict(v: Optional[str]) -> Optional[int]:
        if v is None:
            return None
        if v.strip() == "":
            # empty string -> invalid (Rust treated empty as invalid)
            raise ValueError
        # try parse int
        return int(v.strip())

    try:
        is_pal = parse_bool_strict(params.get("is_palindrome")) if "is_palindrome" in params else None
        min_length = parse_int_strict(params.get("min_length")) if "min_length" in params else None
        max_length = parse_int_strict(params.get("max_length")) if "max_length" in params else None
        word_count = parse_int_strict(params.get("word_count")) if "word_count" in params else None
    except ValueError:
        # Rust returns BadRequest for invalid types/empty values
        raise HTTPException(status_code=400, detail={"error": "Invalid query parameter values or types"})

    # contains_character: must be exactly one character and alphabetic (Rust checked length==1)
    contains_character = None
    if "contains_character" in params:
        val = params.get("contains_character")
        if val is None or val.strip() == "" or len(val) != 1 or (not val.isalpha()):
            raise HTTPException(status_code=400, detail={"error": "Invalid query parameter values or types"})
        contains_character = val

    # logical validation: min_length > max_length -> invalid
    if (min_length is not None) and (max_length is not None) and (min_length > max_length):
        raise HTTPException(status_code=400, detail={"error": "Invalid query parameter values or types"})

    # Apply filters
    with STORE_LOCK:
        all_entries = list(STORE.values())

    results: List[Dict[str, Any]] = []
    for e in all_entries:
        props = e["properties"]
        if is_pal is not None and props["is_palindrome"] != is_pal:
            continue
        if min_length is not None and props["length"] < min_length:
            continue
        if max_length is not None and props["length"] > max_length:
            continue
        if word_count is not None and props["word_count"] != word_count:
            continue
        if contains_character is not None and (contains_character not in e["value"]):
            continue
        results.append(e)

    filters_applied = {
        "is_palindrome": is_pal,
        "min_length": min_length,
        "max_length": max_length,
        "word_count": word_count,
        "contains_character": contains_character,
    }

    return {"data": results, "count": len(results), "filters_applied": filters_applied}


# ---------- GET /strings/filter-by-natural-language ----------
@app.get("/strings/filter-by-natural-language")
def filter_nl(query: Optional[str] = Query(None)):
    if query is None:
        raise HTTPException(status_code=400, detail={"error": "Missing 'query' parameter"})

    q = query.strip().lower()
    if q == "":
        raise HTTPException(status_code=400, detail={"error": "Unable to parse natural language query"})

    want_palindrome: Optional[bool] = None
    want_word_count: Optional[int] = None
    min_length: Optional[int] = None
    contains_character: Optional[str] = None

    # Palindromic or Non-palindromic
    if "non-palindromic" in q:
        want_palindrome = False
    elif "palindromic" in q or "palindrome" in q:
        want_palindrome = True

    # Single word
    if "single word" in q or "one word" in q:
        want_word_count = 1

    # longer than N (-> min_length = N+1)
    m = re.search(r"longer than\s+(\d+)", q)
    if m:
        try:
            n = int(m.group(1))
            min_length = n + 1
        except Exception:
            # parsing failure -> treat as unable to parse
            raise HTTPException(status_code=400, detail={"error": "Unable to parse natural language query"})

    # containing the letter X
    m2 = re.search(r"containing the letter\s+([a-zA-Z])", q)
    if m2:
        contains_character = m2.group(1)

    # contain the first vowel heuristic
    if "contain the first vowel" in q:
        contains_character = "a"

    # conflicting filters: phrase contains both palindromic and non-palindromic
    if ("non-palindromic" in q) and ("palindromic" in q or "palindrome" in q):
        raise HTTPException(status_code=422, detail={"error": "Query parsed but resulted in conflicting filters"})

    # if no filters recognized
    if all(v is None for v in [want_palindrome, want_word_count, min_length, contains_character]):
        raise HTTPException(status_code=400, detail={"error": "Unable to parse natural language query"})

    # apply filters
    with STORE_LOCK:
        all_entries = list(STORE.values())

    filtered: List[Dict[str, Any]] = []
    for e in all_entries:
        props = e["properties"]
        if (want_palindrome is not None) and (props["is_palindrome"] != want_palindrome):
            continue
        if (want_word_count is not None) and (props["word_count"] != want_word_count):
            continue
        if (min_length is not None) and (props["length"] < min_length):
            continue
        if (contains_character is not None):
            # validate single alphabetic char
            if len(contains_character) != 1 or (not contains_character.isalpha()):
                raise HTTPException(status_code=422, detail={"error": "Query parsed but resulted in conflicting filters"})
            if contains_character not in e["value"]:
                continue
        filtered.append(e)

    parsed_filters = {
        "is_palindrome": want_palindrome,
        "word_count": want_word_count,
        "min_length": min_length,
        "contains_character": (contains_character if contains_character is None else contains_character),
    }

    return {
        "data": filtered,
        "count": len(filtered),
        "interpreted_query": {"original": query, "parsed_filters": parsed_filters},
    }


# ---------- DELETE /strings/{string_value} ----------
@app.delete("/strings/{string_value}", status_code=204)
def delete_string(string_value: str = Path(...)):
    h = sha256_of(string_value)
    with STORE_LOCK:
        if h not in STORE:
            raise HTTPException(status_code=404, detail={"error": "String does not exist in the system"})
        del STORE[h]
    # 204 No Content: return empty response body
    return Response(status_code=204)
