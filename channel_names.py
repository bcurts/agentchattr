"""Validation shared by live channel creation and archive import."""


def valid_channel_name(name: object, max_length: int = 60) -> bool:
    """Allow readable names without filesystem separators or control characters."""
    return (isinstance(name, str) and 1 <= len(name) <= max_length
            and name[0].isalnum()
            and all(c.isalnum() or c in ' -_' for c in name))
