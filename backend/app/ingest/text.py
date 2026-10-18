def decode_text(data: bytes) -> str:
    """Decode uploaded text. UTF-8 first (with or without BOM), then Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
